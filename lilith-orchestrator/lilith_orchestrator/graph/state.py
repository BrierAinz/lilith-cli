"""State management for LangGraph flows.

Provides NodeType enum, GraphState TypedDict, GraphCheckpoint model,
and a SQLite-backed Checkpointer for persisting conversation state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field


if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger("lilith.graph.state")


# ── NodeType ──────────────────────────────────────────────────────────────


class NodeType(StrEnum):
    """Enumeration of node types in the conversation graph."""

    ROUTER = "router"
    AGENT = "agent"
    TOOL = "tool"
    MEMORY = "memory"
    PERSONA = "persona"
    OUTPUT = "output"


# ── GraphState ─────────────────────────────────────────────────────────────


class GraphState(dict):
    """State dictionary that flows through the conversation graph.

    Each key is a well-known field; nodes read from and write to this
    state as the graph executes.

    Fields:
        messages: Conversation history (list of dicts with role/content/timestamp).
        current_node: Name of the currently active node.
        context: Shared context (user_mood, project_type, etc.).
        memory_results: Results from memory lookups.
        tool_results: Results from tool executions.
        errors: Accumulated error messages.
        metadata: Arbitrary metadata (session_id, timestamp, etc.).
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            messages=kwargs.get("messages", []),
            current_node=kwargs.get("current_node", ""),
            context=kwargs.get("context", {}),
            memory_results=kwargs.get("memory_results", []),
            tool_results=kwargs.get("tool_results", []),
            errors=kwargs.get("errors", []),
            metadata=kwargs.get("metadata", {}),
        )

    # -- Convenience property accessors --

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.get("messages", [])

    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        self["messages"] = value

    @property
    def current_node(self) -> str:
        return self.get("current_node", "")

    @current_node.setter
    def current_node(self, value: str) -> None:
        self["current_node"] = value

    @property
    def context(self) -> dict[str, Any]:
        return self.get("context", {})

    @context.setter
    def context(self, value: dict[str, Any]) -> None:
        self["context"] = value

    @property
    def memory_results(self) -> list[dict[str, Any]]:
        return self.get("memory_results", [])

    @memory_results.setter
    def memory_results(self, value: list[dict[str, Any]]) -> None:
        self["memory_results"] = value

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        return self.get("tool_results", [])

    @tool_results.setter
    def tool_results(self, value: list[dict[str, Any]]) -> None:
        self["tool_results"] = value

    @property
    def errors(self) -> list[str]:
        return self.get("errors", [])

    @errors.setter
    def errors(self, value: list[str]) -> None:
        self["errors"] = value

    @property
    def metadata(self) -> dict[str, Any]:
        return self.get("metadata", {})

    @metadata.setter
    def metadata(self, value: dict[str, Any]) -> None:
        self["metadata"] = value

    def copy_with(self, **overrides: Any) -> GraphState:
        """Return a new GraphState with the given fields overridden."""
        merged = {
            "messages": self.messages,
            "current_node": self.current_node,
            "context": dict(self.context),
            "memory_results": list(self.memory_results),
            "tool_results": list(self.tool_results),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }
        merged.update(overrides)
        return GraphState(**merged)


# ── GraphCheckpoint ────────────────────────────────────────────────────────


class GraphCheckpoint(BaseModel):
    """A snapshot of a GraphState at a given point in time."""

    model_config = {"arbitrary_types_allowed": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    state: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
    node_name: str = ""


# ── Checkpointer (SQLite-backed) ───────────────────────────────────────────


class Checkpointer:
    """SQLite-backed checkpoint storage for conversation graph state.

    Uses WAL mode for safe concurrent access. All SQLite operations
    are wrapped in ``asyncio.to_thread`` for async-compatible usage.

    Args:
        db_path: Path to the SQLite database file.  ``None`` creates an
            in-memory database (useful for testing).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # -- Private helpers --

    def _get_conn(self) -> sqlite3.Connection:
        """Return a persistent connection.

        For in-memory databases, the same connection is reused because
        ``:memory:`` databases are per-connection.  For on-disk databases,
        a single persistent connection is also used but with
        ``check_same_thread=False`` so that ``asyncio.to_thread`` workers
        can safely access it (SQLite serialises writes via its internal
        lock when WAL mode is enabled).
        """
        if self._conn is not None:
            return self._conn

        if self._db_path is None:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)

        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """\
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                timestamp REAL NOT NULL,
                node_name TEXT NOT NULL DEFAULT '',
                session_id TEXT
            )"""
        )
        self._conn.commit()
        return self._conn

    def _init_db(self) -> None:
        """Ensure the database schema exists."""
        self._get_conn()

    def _close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- Sync API --

    def save(self, checkpoint: GraphCheckpoint) -> str:
        """Save a checkpoint and return its ID."""
        conn = self._get_conn()
        session_id = checkpoint.state.get("metadata", {}).get("session_id")
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints (id, state, timestamp, node_name, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                checkpoint.id,
                json.dumps(checkpoint.state, default=str),
                checkpoint.timestamp,
                checkpoint.node_name,
                session_id,
            ),
        )
        conn.commit()
        return checkpoint.id

    def load(self, checkpoint_id: str) -> GraphCheckpoint | None:
        """Load a checkpoint by ID, or return None if not found."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, state, timestamp, node_name FROM checkpoints WHERE id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return None
        cp_id, state_json, timestamp, node_name = row
        return GraphCheckpoint(
            id=cp_id,
            state=json.loads(state_json),
            timestamp=timestamp,
            node_name=node_name,
        )

    def list_checkpoints(self, session_id: str | None = None) -> list[GraphCheckpoint]:
        """List checkpoints, optionally filtered by session_id."""
        conn = self._get_conn()
        if session_id is not None:
            rows = conn.execute(
                "SELECT id, state, timestamp, node_name FROM checkpoints "
                "WHERE session_id = ? ORDER BY timestamp DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, state, timestamp, node_name FROM checkpoints ORDER BY timestamp DESC"
            ).fetchall()
        return [
            GraphCheckpoint(
                id=row[0],
                state=json.loads(row[1]),
                timestamp=row[2],
                node_name=row[3],
            )
            for row in rows
        ]

    def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint by ID. Returns True if a row was deleted."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM checkpoints WHERE id = ?",
            (checkpoint_id,),
        )
        conn.commit()
        return cursor.rowcount > 0

    # -- Async API (wraps sync in asyncio.to_thread) --

    async def save_async(self, checkpoint: GraphCheckpoint) -> str:
        """Async version of :meth:`save`."""
        return await asyncio.to_thread(self.save, checkpoint)

    async def load_async(self, checkpoint_id: str) -> GraphCheckpoint | None:
        """Async version of :meth:`load`."""
        return await asyncio.to_thread(self.load, checkpoint_id)

    async def list_checkpoints_async(self, session_id: str | None = None) -> list[GraphCheckpoint]:
        """Async version of :meth:`list_checkpoints`."""
        return await asyncio.to_thread(self.list_checkpoints, session_id)

    async def delete_async(self, checkpoint_id: str) -> bool:
        """Async version of :meth:`delete`."""
        return await asyncio.to_thread(self.delete, checkpoint_id)
