"""Environment and system inspection tools for Lilith."""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


@ToolRegistry.register
class EnvGetTool(BaseTool):
    """Tool that returns the value of a single environment variable."""

    name = "env_get"
    description = "Obtiene el valor de una variable de entorno"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre de la variable de entorno",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        """Obtiene el valor de una variable de entorno."""
        name = kwargs.get("name", "")
        if not name:
            return ToolResult(success=False, data=None, error="El nombre de la variable es requerido")

        value = os.environ.get(name)
        if value is None:
            return ToolResult(
                success=False,
                data=None,
                error=f"Variable de entorno no definida: {name}",
            )
        return ToolResult(success=True, data={name: value})


@ToolRegistry.register
class EnvListTool(BaseTool):
    """Tool that lists environment variables with an optional prefix filter."""

    name = "env_list"
    description = "Lista variables de entorno con filtro opcional por prefijo"
    parameters = {
        "prefix": {
            "type": "string",
            "required": False,
            "description": "Prefijo para filtrar variables de entorno",
        },
        "limit": {
            "type": "integer",
            "required": False,
            "description": "Número máximo de variables a devolver",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        """Lista variables de entorno, opcionalmente filtradas por prefijo."""
        prefix = kwargs.get("prefix", "")
        limit = kwargs.get("limit", 50)
        try:
            limit = int(limit) if limit is not None else 50
        except (TypeError, ValueError):
            return ToolResult(success=False, data=None, error="limit debe ser un número entero")

        if limit < 1:
            limit = 1

        prefix_str = str(prefix) if prefix else ""
        env_vars = sorted(os.environ.items())
        if prefix_str:
            env_vars = [(k, v) for k, v in env_vars if k.startswith(prefix_str)]

        total = len(env_vars)
        env_vars = env_vars[:limit]

        return ToolResult(
            success=True,
            data={
                "variables": {k: v for k, v in env_vars},
                "total": total,
                "returned": len(env_vars),
                "prefix": prefix_str,
                "limit": limit,
            },
        )


@ToolRegistry.register
class SysInfoTool(BaseTool):
    """Tool that returns basic operating system and Python runtime information."""

    name = "sys_info"
    description = "Obtiene informacion del sistema: Python, SO, arquitectura y disco"
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Obtiene información del sistema operativo, Python y espacio en disco."""
        try:
            total, used, free = shutil.disk_usage(".")
            disk_info = {
                "total": total,
                "used": used,
                "free": free,
                "total_gb": round(total / (1024**3), 2),
                "used_gb": round(used / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
            }
        except Exception as exc:  # pragma: no cover — defensive
            disk_info = {"error": str(exc)}

        data = {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "os": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "platform": platform.platform(),
            "node": platform.node(),
            "disk": disk_info,
        }
        return ToolResult(success=True, data=data)
