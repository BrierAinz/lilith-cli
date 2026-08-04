"""Search tools for history and code.

Provides utilities for searching within the current session history,
inside a single file, and across multiple files in a directory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


@ToolRegistry.register
class SearchHistoryTool(BaseTool):
    """Search the current session history for matching messages."""

    name = "search_history"
    description = "Busca en el historial de la sesion actual"
    parameters = {
        "query": {"type": "string", "required": True, "description": "Texto a buscar"},
        "role": {
            "type": "string",
            "required": False,
            "default": "all",
            "description": "Rol a filtrar: all, user, assistant, system, tool",
        },
        "limit": {
            "type": "integer",
            "required": False,
            "default": 10,
            "description": "Maximo de resultados a devolver",
        },
    }

    def execute(
        self,
        query: str = "",
        role: str = "all",
        limit: int = 10,
        **_: Any,
    ) -> ToolResult:
        """Busca en el historial de la sesion."""
        if not query:
            return ToolResult(success=False, data=None, error="query es requerido")
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 10

        # Import session history lazily. The tool is expected to be called
        # from the CLI where session.history is available; for standalone use
        # the global _session_history_ref may be set.
        history = _get_session_history()
        if history is None:
            return ToolResult(success=False, data=None, error="No hay historial de sesion disponible")

        query_lower = query.lower()
        role_filter = role.lower().strip() if role else "all"
        results: list[dict[str, Any]] = []

        for i, msg in enumerate(history):
            msg_role = msg.get("role", "?")
            if role_filter != "all" and msg_role.lower() != role_filter:
                continue
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls")
            # Search in content (or tool call names for assistant messages).
            text_to_search = content
            if msg_role == "assistant" and tool_calls:
                names = " ".join(tc.get("function", {}).get("name", "") for tc in tool_calls)
                text_to_search = f"{content} {names}".strip()

            if query_lower in text_to_search.lower():
                results.append(
                    {
                        "index": i,
                        "role": msg_role,
                        "content": content[:250] + ("…" if len(content) > 250 else ""),
                    }
                )
                if len(results) >= limit:
                    break

        return ToolResult(success=True, data={"matches": results, "count": len(results)})


@ToolRegistry.register
class SearchInFileTool(BaseTool):
    """Search for a regex pattern inside a single file with line context."""

    name = "search_in_file"
    description = "Busca un patron regex dentro de un archivo con contexto de lineas"
    parameters = {
        "path": {"type": "string", "required": True, "description": "Ruta del archivo"},
        "query": {"type": "string", "required": True, "description": "Patron a buscar (texto plano o regex)"},
        "context_lines": {
            "type": "integer",
            "required": False,
            "default": 2,
            "description": "Lineas de contexto alrededor de cada coincidencia",
        },
    }

    def execute(
        self,
        path: str = "",
        query: str = "",
        context_lines: int = 2,
        **_: Any,
    ) -> ToolResult:
        """Busca un patron dentro de un archivo."""
        if not path:
            return ToolResult(success=False, data=None, error="path es requerido")
        if not query:
            return ToolResult(success=False, data=None, error="query es requerido")

        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult(success=False, data=None, error=f"Archivo no encontrado: {p}")
        if not p.is_file():
            return ToolResult(success=False, data=None, error=f"No es un archivo: {p}")

        try:
            compiled = re.compile(query)
        except re.error as exc:
            return ToolResult(success=False, data=None, error=f"Regex invalido: {exc}")

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

        lines = text.splitlines()
        matches: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            if compiled.search(line):
                start = max(0, line_number - context_lines - 1)
                end = min(len(lines), line_number + context_lines)
                context = [(i + 1, lines[i]) for i in range(start, end)]
                matches.append(
                    {
                        "line_number": line_number,
                        "line_text": line,
                        "context": context,
                    }
                )

        return ToolResult(success=True, data={"matches": matches, "count": len(matches)})


@ToolRegistry.register
class SearchAcrossFilesTool(BaseTool):
    """Search for a regex pattern across many files in a directory."""

    name = "search_across_files"
    description = "Busca un patron regex en multiples archivos de un directorio"
    parameters = {
        "pattern": {"type": "string", "required": True, "description": "Patron regex a buscar"},
        "path": {"type": "string", "required": False, "default": ".", "description": "Directorio raiz"},
        "file_glob": {"type": "string", "required": False, "default": "*", "description": "Filtro de archivos (ej: *.py)"},
        "limit": {
            "type": "integer",
            "required": False,
            "default": 20,
            "description": "Maximo de coincidencias a devolver",
        },
    }

    def execute(
        self,
        pattern: str = "",
        path: str = ".",
        file_glob: str = "*",
        limit: int = 20,
        **_: Any,
    ) -> ToolResult:
        """Busca un patron en multiples archivos."""
        if not pattern:
            return ToolResult(success=False, data=None, error="pattern es requerido")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return ToolResult(success=False, data=None, error=f"Regex invalido: {exc}")

        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 20

        directory = Path(path).expanduser()
        if not directory.exists():
            return ToolResult(success=False, data=None, error=f"Directorio no encontrado: {directory}")
        if not directory.is_dir():
            return ToolResult(success=False, data=None, error=f"No es un directorio: {directory}")

        glob = file_glob.strip() if file_glob else "*"
        results: list[dict[str, Any]] = []
        try:
            for file_path in directory.rglob(glob):
                if not file_path.is_file():
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if compiled.search(line):
                        results.append(
                            {
                                "file": str(file_path.resolve()),
                                "line_number": line_number,
                                "line_text": line,
                            }
                        )
                        if len(results) >= limit:
                            return ToolResult(success=True, data={"matches": results, "count": len(results)})
            return ToolResult(success=True, data={"matches": results, "count": len(results)})
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# Global weak reference to session history. SearchHistoryTool needs a way to
# access the current AgentSession history without importing the CLI module.
# The CLI registers the history reference when it starts.
_session_history_ref: list[dict[str, Any]] | None = None


def set_session_history_ref(history: list[dict[str, Any]] | None) -> None:
    """Set the session history reference for SearchHistoryTool."""
    global _session_history_ref
    _session_history_ref = history


def _get_session_history() -> list[dict[str, Any]] | None:
    """Return the current session history, if available."""
    if _session_history_ref is not None:
        return _session_history_ref
    # Fallback: try to inspect the current frame stack for an AgentSession.
    try:
        import inspect

        for frame in inspect.stack()[1:]:
            local = frame.frame.f_locals
            for value in local.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    if any("role" in m and "content" in m for m in value[:3]):
                        return value
    except Exception:
        pass
    return None
