"""Task specifications, file snapshots and a subprocess-backed UI runner."""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from dataclasses import dataclass

from . import config as config_module


TEMPLATES = {
    "fix": ("Corregir un error", "Reproduce el error, corrige su causa y añade una prueba de regresión."),
    "review": ("Revisar código", "Revisa los archivos seleccionados. Explica hallazgos concretos con ubicación e impacto. No edites."),
    "tests": ("Añadir pruebas", "Añade pruebas de comportamiento para los casos relevantes de estos archivos."),
    "docs": ("Actualizar documentación", "Actualiza la documentación para reflejar el comportamiento real del código seleccionado."),
}


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staging.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


@dataclass
class TaskSpec:
    root: Path
    objective: str
    files: list[str]
    verify: str = ""
    edit: bool = False
    profile: str = "compact"
    skill: str | None = None

    def validate(self) -> None:
        from .robust_kit import PROFILES, catalog
        if self.profile not in PROFILES or (self.skill and self.skill not in catalog()):
            raise ValueError("Perfil o skill desconocidos")
        if self.profile == "reader" and self.edit:
            raise ValueError("El perfil lector no permite editar")
        self.root = self.root.expanduser().resolve()
        if not self.root.is_dir() or not self.objective.strip() or not self.files:
            raise ValueError("Selecciona un proyecto, un objetivo y al menos un archivo.")
        self.files = list(dict.fromkeys(item.strip() for item in self.files if item.strip()))
        if not self.files:
            raise ValueError("Selecciona al menos un archivo.")
        if self.verify:
            from .verification_input import verification_argv
            verification_argv(self.verify, self.root)
        for item in self.files:
            path = (self.root / item).resolve()
            path.relative_to(self.root)
            if path.is_dir() or (path.exists() and path.stat().st_size > 2_000_000):
                raise ValueError("Selecciona archivos de texto de hasta 2 MB.")
            if path.exists():
                path.read_text(encoding="utf-8")


class TaskRun:
    def __init__(self, spec: TaskSpec, *, directory: Path | None = None, config_path: Path | None = None):
        spec.validate()
        self.spec = spec
        self.config_path = config_path
        self.directory = directory or config_module.CONFIG_DIR / "task-runs" / uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=False)
        self.before = {name: self._read(name) for name in spec.files}
        atomic_json(self.directory / "baseline.json", {k: base64.b64encode(v).decode() if v is not None else None for k, v in self.before.items()})
        self.process = None
        self.result: dict = {}
        self.reviewed_hashes: dict[str, str | None] = {}
        self.control = self.directory / "control.json"

    @classmethod
    def reopen(cls, request: Path):
        payload = json.loads(request.read_text(encoding="utf-8"))
        spec = TaskSpec(Path(payload["root"]), payload["text"], payload["allowed_files"].split(","),
                        payload.get("verify") or "", bool(payload.get("yes")),
                        payload.get("profile", "standard"), payload.get("skill"))
        spec.validate()
        obj = cls.__new__(cls)
        obj.spec, obj.directory = spec, request.parent
        obj.config_path = Path(payload["config"]) if payload.get("config") else None
        raw = json.loads((obj.directory / "baseline.json").read_text())
        obj.before = {name: base64.b64decode(value, validate=True) if value is not None else None for name, value in raw.items()}
        if set(obj.before) != set(spec.files):
            raise ValueError("El baseline no coincide con los archivos de la tarea.")
        obj.process = None
        result_path = obj.directory / "result.json"
        obj.result = json.loads(result_path.read_text()) if result_path.exists() else {}
        obj.reviewed_hashes = {}
        obj.control = obj.directory / "control.json"
        return obj

    def _read(self, name):
        path = (self.spec.root / name).resolve()
        path.relative_to(self.spec.root)
        return path.read_bytes() if path.is_file() else None

    @staticmethod
    def digest(value):
        return hashlib.sha256(value).hexdigest() if value is not None else None

    def diff(self) -> str:
        parts = []
        for name, before in self.before.items():
            after = self._read(name)
            self.reviewed_hashes[name] = self.digest(after)
            if before != after:
                parts.extend(difflib.unified_diff(
                    (before or b"").decode("utf-8", errors="replace").splitlines(keepends=True),
                    (after or b"").decode("utf-8", errors="replace").splitlines(keepends=True),
                    fromfile="antes/" + name, tofile="después/" + name))
        return "".join(parts) or "No hay cambios en los archivos seleccionados."

    def accept(self) -> None:
        if self.process is not None and self.process.returncode is None:
            raise ValueError("Espera a que termine la ejecución.")
        if not self.reviewed_hashes:
            raise ValueError("Revisa el diff antes de aceptar.")
        for name, digest in self.reviewed_hashes.items():
            if self.digest(self._read(name)) != digest:
                raise ValueError("Un archivo cambió desde la revisión. Actualiza el diff.")
        atomic_json(self.directory / "review.json", {"decision": "accepted", "files": self.reviewed_hashes,
                    "verification": self.result.get("progress", {}).get("verification")})

    def request(self, action: str) -> None:
        if action not in ("pause", "cancel"):
            raise ValueError("Acción desconocida")
        atomic_json(self.control, {"action": action})

    async def execute(self, on_activity, *, resume: str | None = None) -> dict:
        payload = {"text": self.spec.objective, "root": str(self.spec.root),
                   "verify": self.spec.verify or None, "yes": self.spec.edit,
                   "allow_tools": "file_read,file_write,file_edit,file_append" if self.spec.edit else "file_read",
                   "allowed_files": ",".join(self.spec.files), "control_file": str(self.control),
                   "resume": resume, "repair_attempts": 1 if self.spec.edit else 0}
        payload.update(profile=self.spec.profile, skill=self.spec.skill)
        if self.config_path is not None:
            payload["config"] = str(self.config_path)
        atomic_json(self.directory / "request.json", payload)
        atomic_json(self.control, {})
        env = dict(os.environ, PYTHONUTF8="1")
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "from lilith_cli.work_session import task_from_file; task_from_file()",
            str(self.directory / "request.json"), cwd=self.spec.root, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
        async def drain_errors():
            async for line in self.process.stderr:
                on_activity(line.decode("utf-8", errors="replace").rstrip())
        reader = asyncio.create_task(drain_errors())
        try:
            output = await self.process.stdout.read()
            await self.process.wait()
            await reader
            try:
                self.result = json.loads(output)
            except (ValueError, UnicodeError):
                self.result = {"status": "blocked", "error": "El proceso no devolvió un resultado válido."}
            atomic_json(self.directory / "result.json", self.result)
            return self.result
        finally:
            if self.process.returncode is None:
                self.request("cancel")
                await self.process.wait()
            await asyncio.gather(reader, return_exceptions=True)
