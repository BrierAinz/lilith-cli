"""Capa de evidencia de una guardia larga: transcripcion append-only e informe.

Por que existe: el 2026-09-15 cinco herramientas de este taller mintieron sobre
su propio resultado en una sola jornada (``batch_edit`` declaro ocho ediciones y
guardo una; ``vor.ps1 -Prime`` dijo "Done" tras fallar la contrasena;
``kvasir.ps1`` salio con codigo 0 sin hacer nada). Una guardia de mas de 24 horas
corre sin nadie delante: sin transcripcion en disco, eso es indetectable a
posteriori y la guardia entera es inauditable.

Dos reglas mandan aqui y no se relajan:

* **Nada entra en HECHO por el informe de una herramienta sobre si misma.** Solo
  lo que tenga una verificacion en el diario o un evento de evidencia con
  resultado.
* **Ruta y tipo, nunca el valor** (operator policy). Media
  clave sigue siendo una filtracion, asi que ningun secreto se escribe truncado
  ni con prefijo: se sustituye entero.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from .redaction import redact

__all__ = ["Evidence", "redact", "report"]


# ── Tiempo ──────────────────────────────────────────────────────────

def _as_utc(value) -> datetime | None:
    """Normaliza a ``datetime`` con zona UTC; ``None`` si no se puede."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _now(now=None) -> datetime:
    return _as_utc(now) or datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    """ISO 8601 UTC con sufijo ``Z``, sin microsegundos."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── Redaccion de secretos ───────────────────────────────────
#
# Vive en ``longrun/redaction.py`` porque la bitacora escribe al mismo disco
# con la misma regla: dos redacciones distintas eran dos niveles de proteccion
# distintos, y mandaba el mas debil. ``redact`` se reexporta aqui para no
# romper a quien ya lo importaba de este modulo.


# ── Transcripcion append-only ───────────────────────────────────────

class Evidence:
    """JSONL append-only con los eventos de una guardia.

    Cada linea es ``{"at", "kind", "payload", "seq"}``. El ``seq`` es monotono
    desde 1 y se deduce del fichero al abrirlo, porque el proceso muere y se
    relanza: ese es justamente el caso que esto tiene que sobrevivir.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.corrupt_lines = 0
        self._seq = max((int(event.get("seq") or 0) for event in self.events()), default=0)

    # -- lectura ----------------------------------------------------

    def events(self) -> list[dict]:
        """Eventos legibles del fichero. Nunca lanza por una linea rota.

        Una caida deja la ultima linea a medias; eso no puede tumbar la lectura
        de todo lo anterior. Las lineas ilegibles se cuentan en
        ``corrupt_lines``.
        """
        self.corrupt_lines = 0
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        events: list[dict] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except (ValueError, TypeError):
                self.corrupt_lines += 1
                continue
            if not isinstance(parsed, dict):
                self.corrupt_lines += 1
                continue
            events.append(parsed)
        return events

    # -- escritura --------------------------------------------------

    def _ends_mid_line(self) -> bool:
        """¿El fichero acaba en una linea sin cerrar?

        Una caida deja la ultima linea a medias y SIN salto de linea. Si el
        siguiente evento se pegara detras, se perderian dos eventos en vez de
        uno: el roto y el nuevo. Se cierra la linea rota antes de escribir.
        """
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if not size:
            return False
        try:
            with self.path.open("rb") as probe:
                probe.seek(-1, os.SEEK_END)
                return probe.read(1) != b"\n"
        except OSError:  # pragma: no cover - fichero ilegible
            return False

    def record(self, kind: str, payload: dict) -> dict:
        """Escribe un evento, con el payload SIEMPRE pasado por ``redact``."""
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("El tipo de evento no puede estar vacio")
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise TypeError("El payload de un evento debe ser un diccionario")

        self._seq += 1
        event = {
            "at": _iso(_now()),
            "kind": kind,
            "payload": redact(dict(payload)),
            "seq": self._seq,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if self._ends_mid_line() else ""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(prefix + json.dumps(event, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


# ── Informe de cinco secciones ──────────────────────────────────────

SECTIONS = (
    "HECHO",
    "NO VERIFICADO",
    "BLOQUEADO POR EL OPERADOR",
    "SIGUIENTE PASO",
    "PRESUPUESTO CONSUMIDO",
)

# Claves de payload que denotan un resultado observado (no un autoinforme).
_RESULT_KEYS = (
    "result", "resultado", "exit_code", "exitcode", "returncode", "rc",
    "status", "stdout", "output", "salida", "ok", "passed",
)
_VERIFIED_KINDS = ("verification", "verificacion", "gate", "check", "comprobacion")


def _text(value, limit: int = 300) -> str:
    # Se redacta ANTES de truncar: cortar una clave por la mitad y escribir la
    # otra mitad sigue siendo una filtracion.
    text = " ".join(str(redact(str(value))).split())
    if len(text) > limit:
        text = text[: limit - 6].rstrip() + " [...]"
    return text


def _entries(journal, kind: str | None = None) -> list[dict]:
    """Entradas del diario, tolerando dobles ligeros en los tests."""
    if journal is None:
        return []
    getter = getattr(journal, "entries", None)
    if getter is None:
        return []
    try:
        items = getter(kind=kind) if kind else getter()
    except TypeError:
        try:
            items = getter()
        except Exception:  # pragma: no cover - diario inservible
            return []
    except Exception:  # pragma: no cover - diario inservible
        return []
    result = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        if kind and item.get("kind") != kind:
            continue
        result.append(dict(item))
    return result


def _events(evidence) -> list[dict]:
    if evidence is None:
        return []
    getter = getattr(evidence, "events", None)
    if getter is None:
        return []
    try:
        items = getter()
    except Exception:  # pragma: no cover - evidencia inservible
        return []
    return [dict(item) for item in items or [] if isinstance(item, Mapping)]


def _has_result(event: Mapping) -> bool:
    if str(event.get("kind") or "").lower() in _VERIFIED_KINDS:
        return True
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    return any(key in payload for key in _RESULT_KEYS)


def _summarize(event: Mapping) -> str:
    payload = event.get("payload")
    if isinstance(payload, Mapping) and payload:
        body = json.dumps(redact(dict(payload)), ensure_ascii=False, sort_keys=True)
    else:
        body = "(sin detalle)"
    return f"`{event.get('kind', '?')}` — {_text(body, 240)}"


def _entry_line(entry: Mapping) -> str:
    line = _text(entry.get("text", ""))
    at = entry.get("at")
    return f"- {line}" + (f" _({at})_" if at else "")


def _stop_code(stop_reason) -> str | None:
    if stop_reason is None:
        return None
    code = getattr(stop_reason, "code", None)
    if isinstance(code, str) and code.strip():
        return code.strip()
    if isinstance(stop_reason, str):
        return stop_reason.strip() or None
    return str(stop_reason)


def _budget(contract):
    return getattr(contract, "budget", None)


def _fmt_usd(value) -> str:
    try:
        return f"{float(value):.2f} USD"
    except (TypeError, ValueError):
        return "desconocido"


def _section_hecho(journal, evidence) -> list[str]:
    lines: list[str] = []
    for entry in _entries(journal, "verification"):
        lines.append(_entry_line(entry))
    for event in _events(evidence):
        if _has_result(event):
            lines.append(f"- {_summarize(event)}")
    if not lines:
        return [
            "- No hay nada verificado con evidencia en disco. Nada entra aqui por el "
            "informe de una herramienta sobre si misma."
        ]
    return lines


def _section_no_verificado(journal, evidence) -> list[str]:
    lines: list[str] = []
    for kind in ("decision", "finding", "failure", "leg", "compaction"):
        for entry in _entries(journal, kind):
            lines.append(f"- [{kind}] " + _entry_line(entry)[2:])
    for event in _events(evidence):
        if not _has_result(event):
            lines.append(f"- {_summarize(event)}")
    if not lines:
        return ["- No hay nada intentado sin verificar: el diario y la evidencia no registran intentos."]
    return lines


def _decision_needed(journal, stop_reason, contract) -> str | None:
    explicit = getattr(stop_reason, "decision_needed", None)
    if isinstance(explicit, str) and explicit.strip():
        return _text(explicit)
    escalations = _entries(journal, "escalation")
    if escalations:
        return _text(escalations[-1].get("text", ""))
    goal = _text(getattr(contract, "goal", "") or "la guardia", 160)
    return (
        f"El operador debe decidir si «{goal}» continua tal como esta planteada o "
        f"se cambia de rumbo; Lilith no puede tomar esa decision."
    )


def _section_bloqueado(journal, stop_reason, contract) -> list[str]:
    lines = [_entry_line(entry) for entry in _entries(journal, "escalation")]
    if _stop_code(stop_reason) == "decision_ajena":
        needed = _decision_needed(journal, stop_reason, contract)
        lines.append(f"- **Decision que hace falta**: {needed}")
    if not lines:
        return ["- No hay nada bloqueado a la espera del operador."]
    return lines


def _section_siguiente(contract, journal, evidence, stop_reason) -> list[str]:
    code = _stop_code(stop_reason)
    verify = _text(getattr(contract, "verify", "") or "", 160)
    end_criterion = _text(getattr(contract, "end_criterion", "") or "el criterio de fin del contrato", 200)

    if code == "terminado":
        return [f"- Cerrar la guardia tras releer la evidencia que respalda el criterio de fin: {end_criterion}."]
    if code == "decision_ajena":
        return ["- Esperar la decision del operador indicada arriba y reanudar la guardia con su respuesta."]
    if code == "irreversible":
        return ["- Pedir al operador autorizacion expresa para el acto irreversible antes de tocar nada mas."]
    if code == "misma_causa_dos_veces":
        failures = _entries(journal, "failure")
        causa = _text(failures[-1].get("text", ""), 160) if failures else "la causa repetida"
        return [f"- Atacar la causa que fallo dos veces en vez de reintentar: {causa}."]
    if code == "presupuesto":
        return ["- Decidir con el operador si se amplia el presupuesto o se cierra la guardia donde esta."]
    if code == "parada_solicitada":
        return ["- Reanudar solo cuando el operador retire la parada escrita en control.json."]

    pendientes = _section_no_verificado(journal, evidence)
    if pendientes and not pendientes[0].startswith("- No hay"):
        if verify:
            return [f"- Verificar lo anotado como no verificado ejecutando `{verify}` y guardar su salida como evidencia."]
        return [f"- Verificar lo anotado como no verificado contra {end_criterion}, con salida de herramienta."]
    if verify:
        return [f"- Ejecutar `{verify}` y guardar su salida como evidencia del criterio de fin."]
    return [f"- Ejecutar el criterio de fin y guardar la salida: {end_criterion}."]


def _section_presupuesto(contract, consumed, now: datetime) -> list[str]:
    budget = _budget(contract)
    created = _as_utc(getattr(contract, "created_at", None))
    max_wall = getattr(budget, "max_wall_seconds", None)
    lines: list[str] = []

    if created is not None:
        used = max((now - created).total_seconds(), 0.0)
        if isinstance(max_wall, (int, float)) and max_wall > 0:
            lines.append(
                f"- Reloj: {used:.0f} s de {int(max_wall)} s "
                f"({used / float(max_wall) * 100:.1f} %), contado desde `created_at`."
            )
        else:
            lines.append(f"- Reloj: {used:.0f} s consumidos; el contrato no declara tope de reloj.")
    else:
        lines.append("- Reloj: desconocido; el contrato no declara `created_at` legible.")

    def _contra_tope(etiqueta: str, usado: str, tope, formato=str) -> str:
        if tope is None:
            return f"- {etiqueta}: {usado} consumidos; sin tope declarado."
        return f"- {etiqueta}: {usado} de {formato(tope)}."

    lines.append(_contra_tope("Tramos", str(getattr(consumed, "legs", 0)),
                              getattr(budget, "max_legs", None)))

    iterations = getattr(consumed, "iterations", 0)
    per_leg = getattr(budget, "max_iterations_per_leg", None)
    lines.append(
        f"- Iteraciones: {iterations} acumuladas "
        f"(tope por tramo: {per_leg if per_leg is not None else 'sin tope declarado'})."
    )

    lines.append(_contra_tope("Tokens", str(getattr(consumed, "tokens", 0)),
                              getattr(budget, "max_tokens", None)))
    lines.append(_contra_tope("Gasto", _fmt_usd(getattr(consumed, "usd", 0.0)),
                              getattr(budget, "max_usd", None), _fmt_usd))
    return lines


def report(contract, consumed, journal, evidence, *,
           stop_reason: str | None = None, now=None) -> str:
    """Devuelve el INFORME.md de la guardia, en cinco secciones fijas.

    Las cinco secciones existen SIEMPRE, incluso con el diario y la evidencia
    vacios: un hueco en blanco se lee como "todo bien", y eso es exactamente lo
    que este informe no puede permitirse.
    """
    moment = _now(now)
    code = _stop_code(stop_reason)

    head = [
        f"# Informe de guardia — {getattr(contract, 'run_id', 'sin run_id')}",
        "",
        f"- **Objetivo**: {_text(getattr(contract, 'goal', '') or 'sin objetivo declarado', 400)}",
        f"- **Criterio de fin**: {_text(getattr(contract, 'end_criterion', '') or 'sin criterio declarado', 400)}",
    ]
    detail = getattr(stop_reason, "detail", None)
    if code:
        suffix = f" — {_text(detail, 240)}" if isinstance(detail, str) and detail.strip() else ""
        head.append(f"- **Motivo de parada**: `{code}`{suffix}")
    else:
        head.append("- **Motivo de parada**: ninguno; la guardia sigue en marcha.")
    head.append(f"- **Generado**: {_iso(moment)}")

    bodies = {
        "HECHO": _section_hecho(journal, evidence),
        "NO VERIFICADO": _section_no_verificado(journal, evidence),
        "BLOQUEADO POR EL OPERADOR": _section_bloqueado(journal, stop_reason, contract),
        "SIGUIENTE PASO": _section_siguiente(contract, journal, evidence, stop_reason),
        "PRESUPUESTO CONSUMIDO": _section_presupuesto(contract, consumed, moment),
    }

    parts = ["\n".join(head)]
    for name in SECTIONS:
        parts.append(f"## {name}\n\n" + "\n".join(bodies[name]))
    return "\n\n".join(parts) + "\n"
