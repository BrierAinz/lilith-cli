"""Todo management tools for Lilith.

Provides a persistent todo list stored in ``~/.yggdrasil/todos.json``.
The list is exposed to the LLM via ``todo_add``, ``todo_done``,
``todo_list``, and ``todo_remove`` tools, and to the user via the
``/todos`` slash command in the REPL.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


_TODO_PATH: Path = Path("~/.yggdrasil/todos.json").expanduser()


@dataclass
class TodoItem:
    """A single todo entry."""

    text: str
    done: bool = False
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"text": self.text, "done": self.done}
        if self.evidence:
            data["evidence"] = self.evidence
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TodoItem":
        return cls(
            text=str(data.get("text", "")),
            done=bool(data.get("done", False)),
            evidence=str(data.get("evidence", "")),
        )


class TodoManager:
    """Persistent todo manager backed by ``~/.yggdrasil/todos.json``."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else _TODO_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[TodoItem]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [TodoItem.from_dict(item) for item in data if isinstance(item, dict)]
        except Exception:
            return []

    def _save(self, todos: list[TodoItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([todo.to_dict() for todo in todos], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list(self) -> list[TodoItem]:
        """Return all todos (done and pending)."""
        return self._load()

    def add(self, text: str) -> TodoItem:
        """Add a new todo and persist it."""
        todos = self._load()
        item = TodoItem(text=text)
        todos.append(item)
        self._save(todos)
        return item

    def done(self, index: int, evidence: str = "") -> TodoItem | None:
        """Mark the todo at ``index`` (1-based) as done, optionally
        recording the verified result that backs the completion."""
        todos = self._load()
        if index < 1 or index > len(todos):
            return None
        todos[index - 1].done = True
        if evidence:
            todos[index - 1].evidence = evidence
        self._save(todos)
        return todos[index - 1]

    def remove(self, index: int) -> TodoItem | None:
        """Remove the todo at ``index`` (1-based)."""
        todos = self._load()
        if index < 1 or index > len(todos):
            return None
        item = todos.pop(index - 1)
        self._save(todos)
        return item

    def clear(self) -> int:
        """Remove all todos. Returns the number of cleared items."""
        todos = self._load()
        count = len(todos)
        self._save([])
        return count


# ── Tool implementations ───────────────────────────────────────────


@ToolRegistry.register
class TodoListTool(BaseTool):
    """Tool that lists all stored todos."""

    name = "todo_list"
    description = "Lista todos los todos pendientes y completados"
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Lista todos los todos almacenados."""
        todos = TodoManager().list()
        data = [
            {"index": i + 1, "text": t.text, "done": t.done}
            for i, t in enumerate(todos)
        ]
        return ToolResult(success=True, data={"todos": data, "count": len(data)})


@ToolRegistry.register
class TodoAddTool(BaseTool):
    """Tool that adds a new todo."""

    name = "todo_add"
    description = "Agrega una nueva tarea pendiente"
    parameters = {
        "text": {
            "type": "string",
            "required": True,
            "description": "Texto descriptivo de la tarea",
        },
    }

    def execute(self, text: str = "", **_kwargs: Any) -> ToolResult:
        """Agrega una tarea a la lista de todos."""
        if not text.strip():
            return ToolResult(success=False, data=None, error="El texto del todo no puede estar vacío")
        item = TodoManager().add(text)
        return ToolResult(success=True, data={"index": len(TodoManager().list()), "todo": item.to_dict()})


@ToolRegistry.register
class TodoDoneTool(BaseTool):
    """Tool that marks a todo as done by its 1-based index."""

    name = "todo_done"
    description = (
        "Marca una tarea como completada por su número de índice. "
        "SOLO marcar done después de VERIFICAR que la operación fue exitosa "
        "(un tool result con error NO cuenta como completado); pasá en "
        "'evidence' el resultado verificado que lo respalda."
    )
    parameters = {
        "index": {
            "type": "integer",
            "required": True,
            "description": "Número de índice del todo (1-based)",
        },
        "evidence": {
            "type": "string",
            "required": False,
            "description": (
                "Resultado verificado que respalda el done "
                "(ej: 'returncode 0', 'commit abc123', 'archivo existe con 1483 bytes')"
            ),
        },
    }

    def execute(self, index: int = 0, evidence: str = "", **_kwargs: Any) -> ToolResult:
        """Marca un todo como completado."""
        if not isinstance(index, int) or index < 1:
            return ToolResult(success=False, data=None, error="El índice debe ser un entero mayor a 0")
        item = TodoManager().done(index, evidence=evidence)
        if item is None:
            return ToolResult(success=False, data=None, error=f"No existe un todo con índice {index}")
        return ToolResult(success=True, data={"index": index, "todo": item.to_dict()})


@ToolRegistry.register
class TodoRemoveTool(BaseTool):
    """Tool that removes a todo by its 1-based index."""

    name = "todo_remove"
    description = "Elimina una tarea por su número de índice"
    parameters = {
        "index": {
            "type": "integer",
            "required": True,
            "description": "Número de índice del todo (1-based)",
        },
    }

    def execute(self, index: int = 0, **_kwargs: Any) -> ToolResult:
        """Elimina un todo de la lista."""
        if not isinstance(index, int) or index < 1:
            return ToolResult(success=False, data=None, error="El índice debe ser un entero mayor a 0")
        item = TodoManager().remove(index)
        if item is None:
            return ToolResult(success=False, data=None, error=f"No existe un todo con índice {index}")
        return ToolResult(success=True, data={"index": index, "removed": item.to_dict()})


__all__ = [
    "TodoItem",
    "TodoManager",
    "TodoListTool",
    "TodoAddTool",
    "TodoDoneTool",
    "TodoRemoveTool",
]
