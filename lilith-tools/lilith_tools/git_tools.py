"""Git operation tool for the Yggdrasil CLI agent.

Provides a single ``git_operation`` tool that runs arbitrary git subcommands
in the current working directory. It validates the operation against an
allowlist and refuses destructive commands unless explicitly confirmed.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

# Raíz del proyecto capturada al arrancar la sesión. Los sandboxes de otras
# herramientas (p. ej. `coding`) hacen os.chdir() y desplazan el cwd del
# proceso; sin este ancla, un `git status` posterior mira un árbol ajeno y
# reporta "working tree clean" sobre el repo equivocado.
_SESSION_ROOT = Path.cwd()


# Allowed git operations. Destructive operations that can rewrite history or
# change remote state require ``confirm=True``.
_SAFE_OPERATIONS = {
    "status",
    "diff",
    "log",
    "show",
    "blame",
    "branch",
}

_DESTRUCTIVE_OPERATIONS = {
    "add",
    "commit",
    "checkout",
    "push",
    "pull",
    "fetch",
    "merge",
    "rebase",
    "reset",
    "restore",
    "rm",
    "mv",
    "init",
}

_ALLOWED_OPERATIONS = _SAFE_OPERATIONS | _DESTRUCTIVE_OPERATIONS

# Serialize git subprocesses: the agent executes parallel tool calls, and
# concurrent `git add` + `git commit` race on .git/index.lock. One lock per
# process is enough — git itself guards cross-process via the lock file,
# which we additionally retry on below.
_GIT_MUTEX = threading.Lock()
_LOCK_RETRIES = 5
_LOCK_RETRY_DELAY = 0.4


@ToolRegistry.register
class GitOperationTool(BaseTool):
    """Run a git subcommand in the current working directory.

    Only whitelisted operations are allowed. Destructive operations
    (add, commit, push, pull, checkout, etc.) require ``confirm=True``.
    """

    name = "git_operation"
    description = (
        "Ejecuta un subcomando git en el directorio de trabajo actual. "
        "Operaciones seguras: status, diff, log, show, blame, branch. "
        "Operaciones destructivas: add, commit, checkout, push, pull, fetch, merge, rebase, "
        "reset, restore, rm, mv, init (requieren confirm=True)."
    )
    parameters = {
        "op": {
            "type": "string",
            "required": True,
            "description": "Subcomando git a ejecutar (ej: status, diff, log, add, commit)",
        },
        "args": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Argumentos adicionales para el subcomando git",
        },
        "confirm": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Requerido para operaciones destructivas",
        },
        "workdir": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Directorio del repo (default: la raíz del proyecto de la sesión)",
        },
    }

    def execute(
        self,
        op: str = "",
        args: str = "",
        confirm: bool = False,
        workdir: str = "",
        **_: Any,
    ) -> ToolResult:
        """Ejecuta ``git <op> <args>`` y devuelve stdout, stderr y returncode."""
        op = (op or "").strip().lower()
        if not op:
            return ToolResult(success=False, data=None, error="op es requerido")

        if op not in _ALLOWED_OPERATIONS:
            allowed = ", ".join(sorted(_ALLOWED_OPERATIONS))
            return ToolResult(
                success=False,
                data=None,
                error=f"Operacion git no permitida: '{op}'. Permitidas: {allowed}",
            )

        if op in _DESTRUCTIVE_OPERATIONS and not confirm:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"Operacion destructiva '{op}' requiere confirm=True. "
                    "Pasa confirm=True para ejecutarla."
                ),
            )

        command = ["git", op]
        if args:
            # shlex with posix=False keeps Windows backslash paths intact;
            # surrounding quotes are stripped per-token afterwards so a
            # multi-word commit message (-m "feat: ...") stays ONE argument
            # (a naive str.split() used to shatter it into fake pathspecs).
            tokens = shlex.split(args, posix=False)
            command.extend(
                t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
                for t in tokens
            )

        run_dir = Path(os.path.expanduser(workdir)) if workdir.strip() else _SESSION_ROOT
        if not run_dir.is_dir():
            return ToolResult(
                success=False,
                data=None,
                error=f"workdir no existe o no es un directorio: {run_dir}",
            )

        try:
            with _GIT_MUTEX:
                for attempt in range(_LOCK_RETRIES):
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                        cwd=str(run_dir),
                    )
                    # Another process (or a stale lock) may hold the index;
                    # git exits non-zero mentioning index.lock — retry briefly.
                    if result.returncode != 0 and "index.lock" in (result.stderr or ""):
                        if attempt < _LOCK_RETRIES - 1:
                            time.sleep(_LOCK_RETRY_DELAY)
                            continue
                    break
        except Exception as exc:  # pragma: no cover
            return ToolResult(success=False, data=None, error=str(exc))

        data = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "command": " ".join(command),
            "workdir": str(run_dir),
        }

        if result.returncode != 0:
            return ToolResult(
                success=False,
                data=data,
                error=(
                    f"git {op} fallo (returncode={result.returncode}): "
                    f"{result.stderr or result.stdout}"
                ),
            )

        return ToolResult(success=True, data=data)
