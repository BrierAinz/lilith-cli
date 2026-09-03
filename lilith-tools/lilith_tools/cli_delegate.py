"""Delegate tools for the local CLI coding subagents Vor and Huginn.

DROP INTO: <repo>/lilith-tools/lilith_tools/cli_delegate.py
THEN ADD "cli_delegate" to the module tuple in lilith-tools/lilith_tools/__init__.py
(right after "delegate").

Vor runs the Codex CLI confined to ``D:\\`` (NTFS jail, CodexAgent account);
Huginn runs a local model on the GPU via Ollama / llama.cpp in the same jail.
Both are driven by PowerShell wrappers that block until the job's ``.done``
marker and print the agent's final message on stdout:

    D:\\_codex\\vor.ps1   -Task "<t>" [-Cd <dir>] [-Safe]
    D:\\_huginn\\huginn.ps1 -Task "<t>" [-Model coder|uncensored|agentic|omnicoder|qwen36|reasoning] [-Cd <dir>] [-File <f> ...]
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

OUTPUT_CHAR_LIMIT = 5000
DEFAULT_TIMEOUT = 1800

VOR_WRAPPER = Path(r"D:\_codex\vor.ps1")
HUGINN_WRAPPER = Path(r"D:\_huginn\huginn.ps1")
_HUGINN_MODELS = ("coder", "uncensored", "agentic", "omnicoder", "qwen36", "reasoning")

_PRIME_HINTS = ("savecred", "credencial", "credential", "1219", "no se guard")


def _truncate(text: str, limit: int = OUTPUT_CHAR_LIMIT) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _powershell(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def _run_wrapper(wrapper: Path, extra: list[str], timeout: int, agent: str) -> ToolResult:
    if not wrapper.exists():
        return ToolResult(success=False, data={"agent": agent}, error=f"{agent} wrapper not found: {wrapper}")
    try:
        proc = _powershell(["-File", str(wrapper), *extra], timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(
            success=False,
            data={"agent": agent, "status": "timeout", "timeout": timeout},
            error=f"{agent} timed out after {timeout}s",
        )
    except OSError as exc:
        return ToolResult(success=False, data={"agent": agent}, error=str(exc))

    stdout = _truncate(proc.stdout or "")
    stderr = _truncate(proc.stderr or "")
    low = (stderr + stdout).lower()

    if proc.returncode != 0 and any(h in low for h in _PRIME_HINTS):
        return ToolResult(
            success=False,
            data={"agent": agent, "status": "needs_prime", "stdout": stdout, "stderr": stderr},
            error=(
                f"{agent}'s CodexAgent credential is not cached. Run once in an "
                f"interactive terminal:  {wrapper.parent}\\{wrapper.stem}.ps1 -Prime"
            ),
        )

    ok = proc.returncode == 0
    return ToolResult(
        success=ok,
        data={
            "agent": agent,
            "status": "ok" if ok else "failed",
            "returncode": proc.returncode,
            "output": stdout,
            "stderr": stderr,
        },
        error="" if ok else f"{agent} exited with code {proc.returncode}",
    )


@ToolRegistry.register
class VorDelegateTool(BaseTool):
    """Delegate a coding task to Vor (Codex CLI, D:-jailed, cloud quota)."""

    name = "vor_delegate"
    description = "Delega una tarea de codigo a Vor (Codex CLI enjaulado en D:)"
    parameters = {
        "task": {"type": "string", "required": True, "description": "Instrucciones para Vor"},
        "cd": {"type": "string", "required": False, "description": "Directorio de trabajo (bajo D:)"},
        "safe": {"type": "boolean", "required": False, "description": "Usar el sandbox propio de Codex ademas del jail NTFS"},
        "timeout": {"type": "integer", "required": False, "description": f"Segundos (por defecto {DEFAULT_TIMEOUT})"},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        task = kwargs.get("task")
        if not task or not str(task).strip():
            return ToolResult(success=False, data={"agent": "Vor"}, error="'task' es obligatorio")
        extra = ["-Task", str(task)]
        if kwargs.get("cd"):
            extra += ["-Cd", str(kwargs["cd"])]
        if kwargs.get("safe"):
            extra += ["-Safe"]
        timeout = int(kwargs.get("timeout") or DEFAULT_TIMEOUT)
        return _run_wrapper(VOR_WRAPPER, extra, timeout, "Vor")


@ToolRegistry.register
class HuginnDelegateTool(BaseTool):
    """Delegate a coding task to Huginn (local model on the GPU, zero cloud quota)."""

    name = "huginn_delegate"
    description = "Delega una tarea de codigo a Huginn (modelo local en GPU, sin cuota cloud)"
    parameters = {
        "task": {"type": "string", "required": True, "description": "Instrucciones para Huginn"},
        "model": {"type": "string", "required": False, "description": f"Uno de: {', '.join(_HUGINN_MODELS)}"},
        "cd": {"type": "string", "required": False, "description": "Directorio de trabajo (bajo D:)"},
        "files": {"type": "array", "required": False, "description": "Archivos concretos para editar"},
        "timeout": {"type": "integer", "required": False, "description": f"Segundos (por defecto {DEFAULT_TIMEOUT})"},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        task = kwargs.get("task")
        if not task or not str(task).strip():
            return ToolResult(success=False, data={"agent": "Huginn"}, error="'task' es obligatorio")
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
        timeout = int(kwargs.get("timeout") or DEFAULT_TIMEOUT)
        return _run_wrapper(HUGINN_WRAPPER, extra, timeout, "Huginn")
