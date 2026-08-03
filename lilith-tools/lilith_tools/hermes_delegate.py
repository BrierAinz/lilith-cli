"""Hermes delegation tool — route a task to the Hermes agent via its CLI.

This is the "grifo" that lets Lilith's orchestrator (k3) sub-delegate to
**Hermes**, the heavy full-capability agent (shell, network, unrestricted
filesystem, its own tool suite and sub-agents). Where
``delegate_subagent`` routes to a *stateless* LLM preset (one-shot prose
or a sandboxed mini-loop), this tool hands a self-contained task to a
real Hermes process that can touch the disk and run commands.

Mechanism: a one-shot subprocess ``hermes -z "<prompt>"`` (``--oneshot``),
which prints ONLY Hermes' final answer to stdout. We capture stdout as
the tool result. No API server needs to be running — this is the CLI
path, matching how Hermes is invoked elsewhere.

The delegation is registered in the shared orchestration state
(``preset="hermes"``) exactly like ``delegate_subagent`` does, so it
shows up in ``/state`` and ``/costs`` for traceability.

Design notes:
  * Runs with ``--yolo`` by default: a one-shot subprocess has no TTY, so
    any interactive approval prompt would hang the call forever. ``--yolo``
    bypasses those prompts so the delegation runs to completion
    unattended. Set ``yolo=False`` to opt back into approvals (only useful
    when a TTY is somehow attached).
  * The prompt is passed as an argv element. Windows caps a command line
    at ~32k chars; keep delegated prompts under that. For very large
    context, write it to a file first and tell Hermes to read it.
  * Tools run in a worker thread (``asyncio.to_thread``), so the blocking
    ``subprocess.run`` here does not stall the REPL event loop.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

# Hermes can run long agentic tasks; this is a generous default ceiling.
_DEFAULT_TIMEOUT = 600


def _find_hermes() -> str | None:
    """Locate the Hermes executable.

    Honours ``HERMES_BIN`` first (explicit override), then falls back to
    ``shutil.which`` against the inherited PATH. Returns ``None`` when
    Hermes cannot be found — the tool turns that into a clean error.
    """
    override = os.environ.get("HERMES_BIN")
    if override:
        return override
    return shutil.which("hermes")


@ToolRegistry.register
class DelegateToHermesTool(BaseTool):
    """Delegate a self-contained task to the Hermes agent and return its answer.

    Use this when the work needs a *real* agent on the machine — shell
    access, unrestricted filesystem, network, or Hermes' own sub-agents —
    rather than a stateless LLM completion. Hermes does NOT see this
    conversation, so the prompt must carry all the context it needs.
    """

    name = "delegate_to_hermes"
    # Hermes runs (subprocess + full agent loop) take much longer than a
    # regular tool. The agent honours this as a timeout floor.
    timeout_seconds = _DEFAULT_TIMEOUT
    description = (
        "Delegar una tarea autocontenida al agente Hermes (via su CLI one-shot "
        "'hermes -z') y devolver su respuesta final. Hermes es el agente pesado: "
        "tiene shell, red, filesystem sin sandbox, sus propias tools y sub-agentes. "
        "Usala para trabajo REAL en disco (implementar features multi-archivo, correr "
        "comandos, refactors sobre un repo, tareas largas) que un sub-agente LLM "
        "stateless no puede hacer. Para eso preferí esta tool sobre delegate_subagent. "
        "Hermes NO ve esta conversación: el prompt debe incluir TODO el contexto "
        "(rutas absolutas, qué hacer, cómo verificar, criterio de cierre). Corre con "
        "--yolo por defecto (sin TTY no puede aprobar prompts). Devuelve el stdout "
        "final de Hermes como 'content'. La delegación queda registrada en /state "
        "con preset='hermes'."
    )
    parameters = {
        "prompt": {
            "type": "string",
            "description": (
                "Tarea completa y autocontenida para Hermes, con todo el contexto "
                "necesario: rutas absolutas, objetivo, pasos, y cómo verificar el "
                "resultado (tests/gates). Hermes no ve esta conversación."
            ),
            "required": True,
        },
        "model": {
            "type": "string",
            "description": "Modelo opcional para Hermes (pasa a -m/--model de hermes -z).",
            "required": False,
        },
        "provider": {
            "type": "string",
            "description": (
                "Provider opcional para Hermes (pasa a --provider de hermes -z)."
            ),
            "required": False,
        },
        "timeout": {
            "type": "integer",
            "default": _DEFAULT_TIMEOUT,
            "description": (
                f"Timeout en segundos para la corrida de Hermes (default {_DEFAULT_TIMEOUT})."
            ),
            "required": False,
        },
        "yolo": {
            "type": "boolean",
            "default": True,
            "description": (
                "Si True (default), corre con --yolo (sin prompts de aprobación). "
                "Necesario en subprocess sin TTY para que no cuelgue."
            ),
            "required": False,
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        prompt = str(kwargs.get("prompt", "")).strip()
        if not prompt:
            return ToolResult(success=False, data=None, error="'prompt' es requerido")

        hermes_bin = _find_hermes()
        if not hermes_bin:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    "No encontré el ejecutable de Hermes. Ponelo en el PATH o "
                    "definí la variable de entorno HERMES_BIN con su ruta."
                ),
            )

        # Register the delegation in the shared orchestration state so it
        # shows up in /state. Best-effort: never break the call if it fails.
        state_store = None
        state_task_id = None
        try:
            from .orchestration_state import OrchestrationStateStore

            state_store = OrchestrationStateStore()
            summary = " ".join(prompt.split())
            task = state_store.add_task(
                summary[:80] or "Delegación Hermes",
                summary[:500],
                status="delegada",
                preset="hermes",
            )
            state_task_id = task["id"]
        except Exception:
            state_store = None
            state_task_id = None

        cmd: list[str] = [hermes_bin, "-z", prompt]
        model = str(kwargs.get("model") or "").strip()
        if model:
            cmd += ["-m", model]
        provider = str(kwargs.get("provider") or "").strip()
        if provider:
            cmd += ["--provider", provider]
        if bool(kwargs.get("yolo", True)):
            cmd.append("--yolo")

        try:
            timeout = int(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT

        result = self._run_hermes(cmd, timeout)

        if state_store is not None and state_task_id is not None:
            data = result.data if isinstance(result.data, dict) else {}
            content = data.get("content") if result.success else result.error
            try:
                state_store.update_task(
                    state_task_id,
                    status="completada" if result.success else "fallida",
                    result=str(content or "")[:1000],
                )
            except Exception:
                pass

        return result

    def _run_hermes(self, cmd: list[str], timeout: int) -> ToolResult:
        """Run the Hermes subprocess and wrap its outcome in a ToolResult."""
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                data=None,
                error=f"Hermes excedió el timeout de {timeout}s",
            )
        except (OSError, ValueError) as exc:
            return ToolResult(
                success=False,
                data=None,
                error=f"No pude lanzar Hermes: {type(exc).__name__}: {exc}",
            )

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                data={
                    "returncode": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr[:1000],
                },
                error=(
                    f"Hermes salió con código {proc.returncode}: "
                    f"{stderr[:300] or 'sin stderr'}"
                ),
            )

        return ToolResult(
            success=True,
            data={
                "content": stdout,
                "returncode": 0,
                "stderr": stderr[:500] if stderr else "",
            },
        )
