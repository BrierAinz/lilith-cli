"""Snippet management tools for Lilith.

Provides persistent reusable code snippets stored in ``~/.yggdrasil/snippets.json``.
The snippets are exposed to the LLM via ``snippet_save``, ``snippet_get``,
``snippet_list`` and ``snippet_delete`` tools, and to the user via the
``/snippets`` slash command in the REPL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


_SNIPPETS_PATH: Path = Path("~/.yggdrasil/snippets.json").expanduser()


class SnippetManager:
    """Persistent snippet manager backed by ``~/.yggdrasil/snippets.json``."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else _SNIPPETS_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            return {}

    def _save(self, snippets: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(snippets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save(self, name: str, content: str, description: str = "") -> dict[str, Any]:
        """Save or overwrite a snippet."""
        snippets = self._load()
        snippets[name] = {
            "name": name,
            "content": content,
            "description": description or "",
        }
        self._save(snippets)
        return snippets[name]

    def get(self, name: str) -> dict[str, Any] | None:
        """Return a snippet by name or None if it does not exist."""
        snippets = self._load()
        return snippets.get(name)

    def list(self) -> list[dict[str, Any]]:
        """Return all stored snippets."""
        return list(self._load().values())

    def delete(self, name: str) -> dict[str, Any] | None:
        """Delete a snippet by name. Returns the removed snippet or None."""
        snippets = self._load()
        if name not in snippets:
            return None
        removed = snippets.pop(name)
        self._save(snippets)
        return removed


# ── Tool implementations ───────────────────────────────────────────


@ToolRegistry.register
class SnippetSaveTool(BaseTool):
    """Tool that saves or updates a reusable code snippet."""

    name = "snippet_save"
    description = "Guarda un fragmento de codigo reutilizable"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre del snippet",
        },
        "content": {
            "type": "string",
            "required": True,
            "description": "Contenido del snippet",
        },
        "description": {
            "type": "string",
            "required": False,
            "description": "Descripcion opcional del snippet",
        },
    }

    def execute(
        self,
        name: str = "",
        content: str = "",
        description: str = "",
        **_kwargs: Any,
    ) -> ToolResult:
        """Guarda un snippet de codigo."""
        if not name.strip():
            return ToolResult(success=False, data=None, error="El nombre del snippet es requerido")
        if not content.strip():
            return ToolResult(success=False, data=None, error="El contenido del snippet no puede estar vacio")
        snippet = SnippetManager().save(name.strip(), content, description)
        return ToolResult(success=True, data={"snippet": snippet})


@ToolRegistry.register
class SnippetGetTool(BaseTool):
    """Tool that retrieves a single snippet by name."""

    name = "snippet_get"
    description = "Recupera un fragmento de codigo por su nombre"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre del snippet a recuperar",
        },
    }

    def execute(self, name: str = "", **_kwargs: Any) -> ToolResult:
        """Recupera un snippet por nombre."""
        if not name.strip():
            return ToolResult(success=False, data=None, error="El nombre del snippet es requerido")
        snippet = SnippetManager().get(name.strip())
        if snippet is None:
            return ToolResult(success=False, data=None, error=f"Snippet no encontrado: {name}")
        return ToolResult(success=True, data={"snippet": snippet})


@ToolRegistry.register
class SnippetListTool(BaseTool):
    """Tool that lists all stored snippets."""

    name = "snippet_list"
    description = "Lista todos los snippets guardados"
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Lista todos los snippets almacenados."""
        snippets = SnippetManager().list()
        return ToolResult(success=True, data={"snippets": snippets, "count": len(snippets)})


@ToolRegistry.register
class SnippetDeleteTool(BaseTool):
    """Tool that deletes a snippet by name."""

    name = "snippet_delete"
    description = "Elimina un snippet por su nombre"
    parameters = {
        "name": {
            "type": "string",
            "required": True,
            "description": "Nombre del snippet a eliminar",
        },
    }

    def execute(self, name: str = "", **_kwargs: Any) -> ToolResult:
        """Elimina un snippet por nombre."""
        if not name.strip():
            return ToolResult(success=False, data=None, error="El nombre del snippet es requerido")
        removed = SnippetManager().delete(name.strip())
        if removed is None:
            return ToolResult(success=False, data=None, error=f"Snippet no encontrado: {name}")
        return ToolResult(success=True, data={"deleted": removed})


__all__ = [
    "SnippetManager",
    "SnippetSaveTool",
    "SnippetGetTool",
    "SnippetListTool",
    "SnippetDeleteTool",
]
