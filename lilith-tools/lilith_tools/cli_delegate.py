"""Optional delegation tools for operator-configured CLI collaborators.

The public package does not assume local account names, drive layouts, saved
credentials or wrapper locations. Operators may expose compatible wrappers via
``LILITH_VOR_WRAPPER``, ``LILITH_HUGINN_WRAPPER`` and
``LILITH_MUNINN_WRAPPER``. When an adapter is not configured the tool fails
closed without reserving or launching work.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from .base import BaseTool, ToolResult
from .cli_job_journal import CliJobJournal
from .registry import ToolRegistry

_orig_journal_reserve = CliJobJournal.reserve


def _journal_reserve(
    self: CliJobJournal,
    agent: str,
    task: str,
    timeout: int,
    request_id: str | None,
    execution_context: str,
) -> tuple[str, bool]:
    if agent == "Muninn":
        if (
            not isinstance(task, str)
            or type(timeout) is not int
            or not 1 <= timeout <= 86400
        ):
            raise ValueError("Invalid delegation task or timeout")
        if request_id is not None and (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)
        ):
            raise ValueError("request_id must be 32 lowercase hex characters")
        intent = hashlib.sha256(
            json.dumps(
                [agent, task, timeout, execution_context], ensure_ascii=True
            ).encode()
        ).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reference = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS attempts ("
                "reference TEXT PRIMARY KEY, agent TEXT NOT NULL, "
                "task_sha256 TEXT NOT NULL, timeout INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, observation TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, "
                "intent_sha256 TEXT NOT NULL, reference TEXT NOT NULL)"
            )
            if request_id is not None:
                previous = conn.execute(
                    "SELECT intent_sha256, reference FROM requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if previous is not None:
                    if previous[0] != intent:
                        raise ValueError(
                            "request_id is already bound to different instructions or parameters"
                        )
                    return previous[1], False
            conn.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    reference,
                    agent,
                    hashlib.sha256(task.encode("utf-8")).hexdigest(),
                    timeout,
                    datetime.now(UTC).isoformat(),
                ),
            )
            if request_id is not None:
                conn.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (request_id, intent, reference),
                )
        return reference, True
    return _orig_journal_reserve(
        self, agent, task, timeout, request_id, execution_context
    )


CliJobJournal.reserve = _journal_reserve

OUTPUT_CHAR_LIMIT = 5000
DEFAULT_TIMEOUT = 1800
MAX_TIMEOUT = 86400

def _path_from_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


_WRAPPER_ENVS = {
    "Vor": "LILITH_VOR_WRAPPER",
    "Huginn": "LILITH_HUGINN_WRAPPER",
    "Muninn": "LILITH_MUNINN_WRAPPER",
}
VOR_WRAPPER = _path_from_env(_WRAPPER_ENVS["Vor"])
VOR_HOME2 = _path_from_env("LILITH_VOR_HOME2")
HUGINN_WRAPPER = _path_from_env(_WRAPPER_ENVS["Huginn"])
MUNINN_WRAPPER = _path_from_env(_WRAPPER_ENVS["Muninn"])
_HUGINN_MODELS = ("coder", "uncensored", "agentic", "omnicoder", "qwen36", "reasoning")

_PRIME_ERROR = re.compile(
    r"runas[^\r\n]*(?:could not use the saved credential|"
    r"no se guard[oó] ninguna credencial)",
    re.IGNORECASE,
)


def _timeout_seconds(value: Any) -> int:
    if value is None:
        return DEFAULT_TIMEOUT
    if type(value) is not int or not 1 <= value <= MAX_TIMEOUT:
        raise ValueError(
            f"timeout must be an integer between 1 and {MAX_TIMEOUT} seconds"
        )
    return value


def _truncate(text: str, limit: int = OUTPUT_CHAR_LIMIT) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _output_text(value: str | bytes | None) -> str:
    # TimeoutExpired can contain bytes even when run(text=True) was used.
    return (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else value or ""
    )


def _recovery_context(agent: str, stdout: str) -> dict[str, Any]:
    """Extract an observation hint, never proof of identity or completion.

    Vor announces its ID before dispatch. Huginn currently announces no early ID;
    do not guess by listing jobs or taking the latest file from a shared directory.
    """
    job_id = None
    if agent == "Vor":
        matches = re.findall(
            r"^=== Vor job ([0-9]{8}-[0-9]{6}-[0-9]{4})  \([^\r\n]*\)  ===\r?$",
            stdout,
            re.MULTILINE,
        )
        if len(matches) == 1:
            job_id = matches[0]
    return {
        "job_id": job_id,
        "job_id_source": "launcher_stdout_unverified" if job_id else None,
        "inspection_tool": "cli_job_inspect" if job_id else None,
        "retry_safe": False,
    }


def _powershell(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(  # fixed argv, no shell
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _run_wrapper(
    wrapper: Path | None, extra: list[str], timeout: int, agent: str
) -> ToolResult:
    if wrapper is None:
        env_name = _WRAPPER_ENVS.get(agent, "LILITH_EXTERNAL_WRAPPER")
        return ToolResult(
            success=False,
            data={"agent": agent, "status": "not_configured"},
            error=f"{agent} wrapper is not configured; set {env_name}",
        )
    if not wrapper.exists():
        return ToolResult(
            success=False,
            data={"agent": agent, "status": "not_configured"},
            error=f"{agent} wrapper not found: {wrapper}",
        )
    try:
        proc = _powershell(["-File", str(wrapper), *extra], timeout)
    except subprocess.TimeoutExpired as exc:
        partial_stdout = _output_text(exc.output)
        partial_stderr = _output_text(exc.stderr)
        return ToolResult(
            success=False,
            data={
                "agent": agent,
                "status": "timeout",
                "timeout": timeout,
                "completion_confirmed": False,
                "effects_unknown": True,
                "output": _truncate(partial_stdout),
                "stderr": _truncate(partial_stderr),
                **_recovery_context(agent, partial_stdout),
            },
            error=(
                f"{agent} observation timed out after {timeout}s; the delegated job "
                "may still be running. Inspect its job record before retrying."
            ),
        )
    except OSError as exc:
        return ToolResult(success=False, data={"agent": agent}, error=str(exc))

    raw_stdout = _output_text(proc.stdout)
    # Inspect the whole response before display truncation. Launcher exit status
    # and delegated process status are separate (some wrappers exit zero on failure).
    markers = re.findall(
        rf"^\s*(?:---\s*)?{re.escape(agent)} finished \(exit (-?\d+)(?:,\s*is_error=[^)]+)?\)(?:\s*---)?\s*$",
        raw_stdout,
        re.MULTILINE,
    )
    job_exit = int(markers[0]) if len(markers) == 1 else None
    stdout = _truncate(raw_stdout)
    stderr = _truncate(proc.stderr or "")
    if proc.returncode != 0 and _PRIME_ERROR.search(proc.stderr or ""):
        return ToolResult(
            success=False,
            data={
                "agent": agent,
                "status": "needs_prime",
                "stdout": stdout,
                "stderr": stderr,
                **_recovery_context(agent, raw_stdout),
            },
            error=(
                f"{agent}'s launcher reported a saved-credential problem. "
                f"Review the launcher error before using {wrapper} -Prime "
                "in an interactive terminal."
            ),
        )

    ok = proc.returncode == 0 and job_exit == 0
    status = "ok" if ok else "failed"
    if proc.returncode == 0 and job_exit is None:
        status = "unconfirmed"
    error = ""
    if not ok:
        error = (
            f"{agent} completion is unconfirmed; inspect its job record before retrying"
            if status == "unconfirmed"
            else f"{agent} failed (wrapper exit {proc.returncode}, job exit {job_exit})"
        )
    return ToolResult(
        success=ok,
        data={
            "agent": agent,
            "status": status,
            "returncode": proc.returncode,
            "job_returncode": job_exit,
            "completion_confirmed": job_exit is not None,
            "output": stdout,
            "stderr": stderr,
            **_recovery_context(agent, raw_stdout),
        },
        error=error,
    )


def _run_tracked(
    wrapper: Path | None,
    extra: list[str],
    timeout: int,
    agent: str,
    task: str,
    request_id: str | None = None,
) -> ToolResult:
    # Configuration failures happen before durable reservation: no worker can
    # start, so recording an attempt would make a later corrected request look
    # like a duplicate even though execution never became possible.
    if wrapper is None or not wrapper.exists():
        return _run_wrapper(wrapper, extra, timeout, agent)
    journal = CliJobJournal()
    try:
        if request_id is None:
            reference, created = journal.begin(agent, task, timeout), True
        else:
            reference, created = journal.reserve(
                agent,
                task,
                timeout,
                request_id,
                json.dumps([str(wrapper), extra, str(Path.cwd())], ensure_ascii=True),
            )
    except ValueError as exc:
        return ToolResult(
            success=False,
            data={"agent": agent, "status": "request_rejected"},
            error=str(exc),
        )
    except (OSError, sqlite3.Error):
        return ToolResult(
            success=False,
            data={"agent": agent, "status": "journal_unavailable"},
            error="Cannot save delegation intent; no worker was launched",
        )
    if not created:
        return ToolResult(
            success=False,
            data={
                "agent": agent,
                "status": "existing_attempt",
                "reference": reference,
                "reference_saved": True,
                "execution_performed": False,
                "retry_safe": False,
            },
            error="Request already recorded; use jobs inspect-reference, do not relaunch",
        )
    result = _run_wrapper(wrapper, extra, timeout, agent)
    result.data["reference"] = reference
    result.data["reference_saved"] = True
    try:
        journal.finish(reference, result.data)
        result.data["observation_saved"] = True
    except (OSError, ValueError, sqlite3.Error):
        result.data["observation_saved"] = False
        result.success = False
        result.error = "Delegation observation could not be saved; inspect the existing reference before retrying"
    return result


@ToolRegistry.register
class VorDelegateTool(BaseTool):
    """Delegate a coding task to an operator-configured Codex wrapper."""

    name = "vor_delegate"
    allow_automatic_retry = False
    failure_metadata_fields = ("status", "reference", "reference_saved", "observation_saved",
                               "job_id", "job_id_source", "retry_safe", "effects_unknown")

    @staticmethod
    def timeout_for_arguments(arguments: dict[str, Any]) -> int:
        return _timeout_seconds(arguments.get("timeout")) + 30
    description = "Delega una tarea a un wrapper Codex configurado por LILITH_VOR_WRAPPER"
    parameters: ClassVar[dict[str, Any]] = {
        "request_id": {
            "type": "string",
            "required": False,
            "description": "32 hex minúsculas; reutilizar la misma clave evita repetir el despacho",
        },
        "task": {
            "type": "string",
            "required": True,
            "description": "Instrucciones para Vor",
        },
        "cd": {
            "type": "string",
            "required": False,
            "description": "Directorio de trabajo que recibira el wrapper configurado",
        },
        "safe": {
            "type": "boolean",
            "required": False,
            "description": "Solicitar modo seguro si el wrapper configurado lo soporta",
        },
        "profile": {
            "type": "string",
            "required": False,
            "description": "Perfil de Codex: 1/home usa el wrapper por defecto; 2/home2 requiere LILITH_VOR_HOME2",
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "description": f"Segundos (por defecto {DEFAULT_TIMEOUT})",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        task = kwargs.get("task")
        if not task or not str(task).strip():
            return ToolResult(
                success=False, data={"agent": "Vor"}, error="'task' es obligatorio"
            )
        extra = ["-Task", str(task)]
        if kwargs.get("cd"):
            extra += ["-Cd", str(kwargs["cd"])]
        if kwargs.get("safe"):
            extra += ["-Safe"]
        profile = kwargs.get("profile")
        if profile is not None:
            if isinstance(profile, bool):
                return ToolResult(
                    success=False,
                    data={"agent": "Vor"},
                    error=f"profile invalido '{profile}'. Validos: 1, home, 2, home2",
                )
            p_val = str(profile).strip().lower()
            if p_val in ("1", "home"):
                pass
            elif p_val in ("2", "home2"):
                if VOR_HOME2 is None:
                    return ToolResult(
                        success=False,
                        data={"agent": "Vor", "status": "not_configured"},
                        error="profile 2/home2 requires LILITH_VOR_HOME2",
                    )
                extra += ["-CodexHome", str(VOR_HOME2)]
            else:
                return ToolResult(
                    success=False,
                    data={"agent": "Vor"},
                    error=f"profile invalido '{profile}'. Validos: 1, home, 2, home2",
                )
        try:
            timeout = _timeout_seconds(kwargs.get("timeout"))
        except ValueError as exc:
            return ToolResult(success=False, data={"agent": "Vor"}, error=str(exc))
        extra += ["-TimeoutSec", str(timeout)]
        return _run_tracked(
            VOR_WRAPPER, extra, timeout, "Vor", str(task), kwargs.get("request_id")
        )


@ToolRegistry.register
class HuginnDelegateTool(BaseTool):
    """Delegate a coding task to an operator-configured local-model wrapper."""

    name = "huginn_delegate"
    allow_automatic_retry = False
    failure_metadata_fields = VorDelegateTool.failure_metadata_fields
    timeout_for_arguments = staticmethod(VorDelegateTool.timeout_for_arguments)
    description = (
        "Delega una tarea a un wrapper local configurado por LILITH_HUGINN_WRAPPER"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "request_id": {
            "type": "string",
            "required": False,
            "description": "32 hex minúsculas; reutilizar la misma clave evita repetir el despacho",
        },
        "task": {
            "type": "string",
            "required": True,
            "description": "Instrucciones para Huginn",
        },
        "model": {
            "type": "string",
            "required": False,
            "description": f"Uno de: {', '.join(_HUGINN_MODELS)}",
        },
        "cd": {
            "type": "string",
            "required": False,
            "description": "Directorio de trabajo que recibira el wrapper configurado",
        },
        "files": {
            "type": "array",
            "required": False,
            "description": "Archivos concretos para editar",
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "description": f"Segundos (por defecto {DEFAULT_TIMEOUT})",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        task = kwargs.get("task")
        if not task or not str(task).strip():
            return ToolResult(
                success=False, data={"agent": "Huginn"}, error="'task' es obligatorio"
            )
        model = str(kwargs.get("model") or "coder")
        if model not in _HUGINN_MODELS:
            return ToolResult(
                success=False,
                data={"agent": "Huginn"},
                error=f"model invalido '{model}'. Validos: {', '.join(_HUGINN_MODELS)}",
            )
        extra = ["-Task", str(task), "-Model", model]
        if kwargs.get("cd"):
            extra += ["-Cd", str(kwargs["cd"])]
        for f in kwargs.get("files") or []:
            if f:
                extra += ["-File", str(f)]
        try:
            timeout = _timeout_seconds(kwargs.get("timeout"))
        except ValueError as exc:
            return ToolResult(success=False, data={"agent": "Huginn"}, error=str(exc))
        extra += ["-TimeoutSec", str(timeout)]
        return _run_tracked(
            HUGINN_WRAPPER,
            extra,
            timeout,
            "Huginn",
            str(task),
            kwargs.get("request_id"),
        )


@ToolRegistry.register
class MuninnDelegateTool(BaseTool):
    """Delegate a task to an operator-configured Claude Code wrapper."""

    name = "muninn_delegate"
    allow_automatic_retry = False
    failure_metadata_fields = VorDelegateTool.failure_metadata_fields
    timeout_for_arguments = staticmethod(VorDelegateTool.timeout_for_arguments)
    description = (
        "Delega una tarea a un wrapper Claude Code configurado por LILITH_MUNINN_WRAPPER. "
        "Lilith no eleva privilegios: el wrapper conserva únicamente los permisos del proceso "
        "con el que el operador lo configuró."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "request_id": {
            "type": "string",
            "required": False,
            "description": "32 hex minúsculas; reutilizar la misma clave evita repetir el despacho",
        },
        "task": {
            "type": "string",
            "required": True,
            "description": "Instrucciones para Muninn",
        },
        "cd": {
            "type": "string",
            "required": False,
            "description": "Directorio de trabajo que recibira el wrapper configurado",
        },
        "model": {
            "type": "string",
            "required": False,
            "description": "Modelo de Claude Code (opcional)",
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "description": f"Segundos (por defecto {DEFAULT_TIMEOUT})",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        task = kwargs.get("task")
        if not task or not str(task).strip():
            return ToolResult(
                success=False, data={"agent": "Muninn"}, error="'task' es obligatorio"
            )
        extra = ["-Task", str(task)]
        if kwargs.get("cd"):
            extra += ["-Cd", str(kwargs["cd"])]
        if kwargs.get("model"):
            extra += ["-Model", str(kwargs["model"])]
        try:
            timeout = _timeout_seconds(kwargs.get("timeout"))
        except ValueError as exc:
            return ToolResult(success=False, data={"agent": "Muninn"}, error=str(exc))
        extra += ["-TimeoutSec", str(timeout)]
        return _run_tracked(
            MUNINN_WRAPPER,
            extra,
            timeout,
            "Muninn",
            str(task),
            kwargs.get("request_id"),
        )

