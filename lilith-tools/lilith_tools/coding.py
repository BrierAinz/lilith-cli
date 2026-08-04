"""Tool de ejecucion de codigo Python sandboxed."""

import subprocess
import sys
import tempfile

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


@ToolRegistry.register
class CodingTool(BaseTool):
    """Sandboxed Python code execution tool.

    Runs user-supplied Python code in a subprocess with a blocked-keyword
    blacklist (``__import__``, ``eval``, ``exec``, ``open``, etc.) and a
    configurable timeout.
    """

    name = "coding"
    description = (
        "Ejecuta codigo Python en entorno sandboxed. "
        "Parametros: code (str), timeout (int, default 10)"
    )
    parameters = {
        "code": {"type": "string", "description": "Codigo Python a ejecutar"},
        "timeout": {
            "type": "integer",
            "description": "Timeout en segundos",
            "default": 10,
        },
    }

    def execute(self, code: str = "", timeout: int = 10) -> ToolResult:
        """Ejecuta código Python en entorno sandboxed."""
        if not code:
            return ToolResult(success=False, data=None, error="Codigo vacio")
        blocked = [
            "__import__",
            "eval(",
            "exec(",
            "compile(",
            "open(",
            "os.system",
            "subprocess",
            "import os",
            "import sys",
        ]
        for b in blocked:
            if b in code:
                return ToolResult(success=False, data=None, error=f"Uso bloqueado detectado: {b}")
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                f.flush()
                result = subprocess.run(
                    [sys.executable, f.name],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            return ToolResult(
                success=True,
                data={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, data=None, error="Timeout")
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))
