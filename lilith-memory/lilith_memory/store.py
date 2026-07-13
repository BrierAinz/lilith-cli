"""SQLite-backed memory store for Yggdrasil."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from lilith_memory.read_guard import ReadPolicy, guard


Scope = str | dict[str, Any]


def _metadata_dict(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if not isinstance(metadata, str) or not metadata:
        return {}
    try:
        parsed = json.loads(metadata)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _matches_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (list, tuple, set)):
        if isinstance(expected, (list, tuple, set)):
            return set(expected).issubset(set(actual))
        return expected in actual
    return actual == expected


def _matches_scope(item: dict[str, Any], scope: Scope | None) -> bool:
    if scope is None:
        return True
    metadata = _metadata_dict(item)
    if isinstance(scope, str):
        candidates = (
            item.get("session_id"),
            item.get("role"),
            metadata.get("scope"),
            metadata.get("namespace"),
            metadata.get("agent"),
        )
        tags = metadata.get("tags", metadata.get("tag"))
        return scope in candidates or _matches_value(tags, scope)
    return all(
        _matches_value(item.get(key, metadata.get(key)), value)
        for key, value in scope.items()
    )


class MemoryStore:
    """Persistent memory store using SQLite."""

    def __init__(self, db_path: str | Path = "chat_memory.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session ON memories(session_id)
            """)
            conn.commit()

    # ── Core API ──────────────────────────────────────────────────────

    def store(self, session_id: str, role: str, content: str, metadata: dict | None = None) -> int:
        """Store a memory entry."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO memories (session_id, role, content, metadata) VALUES (?, ?, ?, ?)",
                (session_id, role, content, json.dumps(metadata or {})),
            )
            conn.commit()
            return cur.lastrowid

    def recall(
        self,
        session_id: str,
        limit: int = 10,
        requester: str | None = None,
        policy: ReadPolicy[dict[str, Any]] | None = None,
        scope: Scope | None = None,
    ) -> list[dict[str, Any]]:
        """Recall memories for a session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memories WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            results = [dict(r) for r in rows]
        scoped = [item for item in results if _matches_scope(item, scope)]
        return guard(scoped, requester=requester, policy=policy)

    def search(
        self,
        query: str,
        limit: int = 5,
        requester: str | None = None,
        policy: ReadPolicy[dict[str, Any]] | None = None,
        scope: Scope | None = None,
    ) -> list[dict[str, Any]]:
        """Simple text search across memories."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memories WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            results = [dict(r) for r in rows]
        scoped = [item for item in results if _matches_scope(item, scope)]
        return guard(scoped, requester=requester, policy=policy)

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def sessions(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT DISTINCT session_id FROM memories").fetchall()
            return [r[0] for r in rows]

    # ── Convenience aliases ───────────────────────────────────────────

    def add(
        self,
        content: str,
        role: str = "user",
        session_id: str = "default",
        metadata: dict | None = None,
    ) -> int:
        """Convenience wrapper around store()."""
        return self.store(session_id, role, content, metadata)

    def count_entries(self) -> int:
        """Alias for count()."""
        return self.count()

    def recent(
        self,
        limit: int = 10,
        requester: str | None = None,
        policy: ReadPolicy[dict[str, Any]] | None = None,
        scope: Scope | None = None,
    ) -> list[dict[str, Any]]:
        """Alias for recall() with default session."""
        return self.recall("default", limit, requester, policy, scope)

    def delete(self, entry_id: int) -> bool:
        """Delete an entry by id. Returns True if deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        """Remove all entries. Returns count removed."""
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.execute("DELETE FROM memories")
            conn.commit()
            return count

    # ── Dunder methods ────────────────────────────────────────────────

    def __len__(self) -> int:
        return self.count()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False
