"""Bind optional edit eligibility to a reproducible, matching calibration report."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import calibration
from . import config as config_module
from .agent_modes import tool_capability
from .hearth import _backup, _write_yaml
from .task_workspace import atomic_json


def validate_report(path: Path, cfg):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("suite_hash") != calibration.SUITE_HASH or report.get("validator_hash") != hashlib.sha256(Path(calibration.__file__).read_bytes()).hexdigest():
        raise ValueError("El informe pertenece a otra versión del banco/verificador.")
    if report.get("model", {}).get("fingerprint") != calibration.model_identity(cfg)["fingerprint"]:
        raise ValueError("El modelo o endpoint no coincide con el informe.")
    age = datetime.now(UTC) - datetime.fromisoformat(report["date"])
    if not 0 <= age.total_seconds() <= 30 * 86400:
        raise ValueError("El informe está vencido o tiene una fecha futura.")
    rounds = report.get("rounds")
    if type(rounds) is not int or not 1 <= rounds <= 5 or report.get("status") != "complete":
        raise ValueError("El informe no está completo.")
    rows = report.get("results") or []
    expected = {(case["id"], index) for case in calibration.CASES for index in range(1, rounds + 1)}
    actual = {(row["case"], row["round"]) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("Faltan casos o hay resultados duplicados.")
    rescored = [{"case": row["case"], "passed": row.get("outcome") != "provider_error" and
                calibration.verify_response(row["case"], {"content": row.get("response"), "tool_calls": row.get("tool_calls")})}
               for row in rows]
    return calibration.recommendation(rescored, rounds)


def allows_tool(cfg, tool_name: str) -> bool:
    if not getattr(cfg, "require_calibration_for_edits", False) or tool_capability(tool_name) == "read":
        return True
    try:
        path = getattr(cfg, "calibration_report", None)
        return bool(path and validate_report(Path(path), cfg)["edit_candidate"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


@calibration.calibration_app.command(name="status")
def status(config: str | None = None):
    """Mostrar identidad y habilitación actuales sin llamar al proveedor."""
    cfg = config_module.load_config(config)
    print(json.dumps({"model": calibration.model_identity(cfg),
                      "enforced": cfg.require_calibration_for_edits,
                      "execution_profile": cfg.execution_profile,
                      "edit_eligible": allows_tool(cfg, "file_write"),
                      "report": cfg.calibration_report}, ensure_ascii=True, indent=2))


@calibration.calibration_app.command(name="rescore")
def rescore(report: str, output: str):
    """Reevaluar respuestas guardadas con el verificador actual, sin llamadas API."""
    source = Path(report)
    destination = Path(output)
    if destination.exists():
        raise SystemExit("El destino ya existe; conserva el informe anterior.")
    raw = source.read_bytes()
    data = json.loads(raw)
    if data.get("suite_hash") != calibration.SUITE_HASH:
        raise SystemExit("Las tareas cambiaron; hace falta una nueva ejecución del modelo.")
    for row in data["results"]:
        row["passed"] = row.get("outcome") != "provider_error" and calibration.verify_response(
            row["case"], {"content": row.get("response"), "tool_calls": row.get("tool_calls")})
    data["rescored_from_sha256"] = hashlib.sha256(raw).hexdigest()
    data["validator_hash"] = hashlib.sha256(Path(calibration.__file__).read_bytes()).hexdigest()
    data["recommendation"] = calibration.recommendation(data["results"], data["rounds"])
    atomic_json(destination, data)
    print(json.dumps({"report": str(destination.resolve()), "recommendation": data["recommendation"]}))


@calibration.calibration_app.command(name="apply")
def apply_report(report: str, config: str | None = None, dry_run: bool = False):
    """Aplicar perfil y exigir calibración vigente para acciones mutantes; conserva respaldo."""
    path = Path(config).resolve() if config else config_module.CONFIG_FILE
    cfg = config_module.load_config(str(path))
    result = validate_report(Path(report).resolve(), cfg)
    if result["profile"] == "hold":
        raise SystemExit("El modelo no alcanzó el mínimo del banco; no se modificó la configuración.")
    changes = {"execution_profile": result["profile"], "require_calibration_for_edits": True,
               "calibration_report": str(Path(report).resolve())}
    if not dry_run:
        durable_report = path.parent / "calibration" / ("qualified-" + uuid.uuid4().hex + ".json")
        atomic_json(durable_report, json.loads(Path(report).read_text(encoding="utf-8")))
        changes["calibration_report"] = str(durable_report)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw.update(changes)
        _backup(path)
        _write_yaml(path, raw)
    print(json.dumps({"applied": not dry_run, "settings": changes, "edit_candidate": result["edit_candidate"]}, ensure_ascii=True))
