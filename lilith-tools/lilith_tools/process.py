"""Background process tools for dev servers, watchers and long-running tasks."""

from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

# Keep a single ProcessManager instance per process. The CLI package may not be
# installed in the same Python environment, so we import lazily.
_process_manager = None


def _get_manager() -> Any:
    global _process_manager
    if _process_manager is None:
        from lilith_cli.process_manager import ProcessManager

        _process_manager = ProcessManager()
    return _process_manager


@ToolRegistry.register
class BgStartTool(BaseTool):
    """Tool that starts a long-running background process.

    Useful for dev servers, file watchers, or any process that should outlive
    the current agent turn.
    """

    name = "bg_start"
    description = "Inicia un proceso en segundo plano (servidor, watcher, etc.)"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre unico del proceso",
        },
        "command": {
            "type": "string",
            "required": True,
            "description": "Comando shell a ejecutar",
        },
        "cwd": {
            "type": "string",
            "required": False,
            "description": "Directorio de trabajo opcional",
        },
    }

    def execute(self, name: str, command: str, cwd: str | None = None, **_: Any) -> ToolResult:
        """Inicia un proceso en segundo plano."""
        manager = _get_manager()
        try:
            pid = manager.start(name, command, cwd=cwd)
        except Exception as exc:  # pragma: no cover
            return ToolResult(success=False, data=None, error=str(exc))
        if pid is None:
            return ToolResult(
                success=False,
                data=None,
                error=f"No se pudo iniciar el proceso '{name}' (puede que ya exista o el comando fallo)",
            )
        return ToolResult(success=True, data={"name": name, "pid": pid})


@ToolRegistry.register
class BgStatusTool(BaseTool):
    """Tool that returns the status of one or all background processes."""

    name = "bg_status"
    description = "Consulta el estado de procesos en segundo plano"
    parameters = {
        "name": {
            "type": "string",
            "required": False,
            "description": "Nombre del proceso. Si se omite, lista todos",
        },
    }

    def execute(self, name: str | None = None, **_: Any) -> ToolResult:
        """Consulta el estado de procesos en segundo plano."""
        manager = _get_manager()
        try:
            if name:
                status = manager.status(name)
                if status is None:
                    return ToolResult(success=False, data=None, error=f"Proceso no encontrado: {name}")
                data = status
            else:
                data = {"processes": manager.list()}
        except Exception as exc:  # pragma: no cover
            return ToolResult(success=False, data=None, error=str(exc))
        return ToolResult(success=True, data=data)


@ToolRegistry.register
class BgStopTool(BaseTool):
    """Tool that stops a background process by name."""

    name = "bg_stop"
    description = "Detiene un proceso en segundo plano"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre del proceso a detener",
        },
    }

    def execute(self, name: str, **_: Any) -> ToolResult:
        """Detiene un proceso en segundo plano."""
        manager = _get_manager()
        try:
            stopped = manager.stop(name)
        except Exception as exc:  # pragma: no cover
            return ToolResult(success=False, data=None, error=str(exc))
        if not stopped:
            return ToolResult(success=False, data=None, error=f"No se pudo detener el proceso: {name}")
        return ToolResult(success=True, data={"name": name, "stopped": True})


@ToolRegistry.register
class BgLogTool(BaseTool):
    """Tool that returns the last N lines of a process log file."""

    name = "bg_log"
    description = "Muestra las ultimas lineas del log de un proceso en segundo plano"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre del proceso",
        },
        "lines": {
            "type": "integer",
            "required": False,
            "default": 50,
            "description": "Cantidad de lineas a devolver",
        },
    }

    def execute(self, name: str, lines: int = 50, **_: Any) -> ToolResult:
        """Muestra las ultimas lineas del log de un proceso."""
        manager = _get_manager()
        try:
            log = manager.get_log(name, lines=lines)
        except Exception as exc:  # pragma: no cover
            return ToolResult(success=False, data=None, error=str(exc))
        return ToolResult(success=True, data={"name": name, "log": log})
