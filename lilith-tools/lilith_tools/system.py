"""System information tools for querying OS details and current time."""

from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


@ToolRegistry.register
class SystemInfoTool(BaseTool):
    """Tool that returns basic operating system information (OS, version, machine)."""

    name = "system_info"
    description = "Obtiene informacion del sistema"
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Obtiene información del sistema operativo."""
        import platform

        data = {
            "os": platform.system(),
            "version": platform.version(),
            "machine": platform.machine(),
        }
        return ToolResult(success=True, data=data)


@ToolRegistry.register
class SystemTimeTool(BaseTool):
    """Tool that returns the current date and time in ISO format."""

    name = "system_time"
    description = "Obtiene la fecha y hora actual"
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Obtiene la fecha y hora actual en formato ISO."""
        from datetime import datetime

        return ToolResult(success=True, data={"now": datetime.now().isoformat()})
