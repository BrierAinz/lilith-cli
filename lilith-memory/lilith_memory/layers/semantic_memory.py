"""Permanent fact-based semantic memory layer."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path


# Valid fact types stored in semantic memory
VALID_FACT_TYPES = {"preference", "fact", "procedure", "relationship", "identity"}


class SemanticMemory:
    """Permanent, fact-based memory with no time-based decay.

    Semantic memory stores facts, preferences, procedures, relationships,
    and identity information.  Unlike episodic memory, entries here do **not**
    decay over time.  Confidence scores can be adjusted as facts are
    corroborated or contradicted.

    Persistence is via a dedicated SQLite table (``semantic_memories``).
    """

    def __init__(self, db_path: Path) -> None:
        """Initialise semantic memory.

        Args:
            db_path: Path to the SQLite database file.

        """
        self._db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the semantic_memories table and indexes."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    fact_type TEXT NOT NULL DEFAULT 'fact',
                    source TEXT,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    metadata TEXT,
                    timestamp REAL NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_type ON semantic_memories(fact_type)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_timestamp "
                "ON semantic_memories(timestamp DESC)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_confidence "
                "ON semantic_memories(confidence DESC)",
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to a dict, parsing metadata JSON."""
        d = dict(row)
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(
        self,
        content: str,
        fact_type: str = "fact",
        source: str | None = None,
        confidence: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add a new semantic memory entry.

        Args:
            content: The factual text to store.
            fact_type: One of 'preference', 'fact', 'procedure',
                       'relationship', 'identity'. Defaults to 'fact'.
            source: Optional source description (e.g. "user statement").
            confidence: Initial confidence between 0.0 and 1.0.
            metadata: Optional dict of arbitrary metadata.

        Returns:
            The unique identifier of the new entry.

        Raises:
            ValueError: If *fact_type* is not one of the valid types.

        """
        if fact_type not in VALID_FACT_TYPES:
            raise ValueError(
                f"Invalid fact_type '{fact_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_FACT_TYPES))}",
            )

        item_id = str(uuid.uuid4())
        now = time.time()

        def _insert() -> None:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    INSERT INTO semantic_memories
                        (id, content, fact_type, source,
                         confidence, metadata, timestamp,
                         access_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        item_id,
                        content,
                        fact_type,
                        source,
                        confidence,
                        json.dumps(metadata) if metadata else None,
                        now,
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_insert)
        return item_id

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search semantic memories by substring, ordered by confidence.

        Args:
            query: Substring to search for (case-insensitive).
            limit: Maximum number of results.

        Returns:
            A list of dicts representing matching entries.

        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        def _search() -> list[dict[str, Any]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                # Increment access_count for matched items
                conn.execute(
                    "UPDATE semantic_memories SET access_count = access_count + 1 "
                    "WHERE content LIKE ? ESCAPE '\\'",
                    (f"%{escaped}%",),
                )
                conn.commit()
                rows = conn.execute(
                    "SELECT * FROM semantic_memories "
                    "WHERE content LIKE ? ESCAPE '\\' "
                    "ORDER BY confidence DESC, timestamp DESC LIMIT ?",
                    (f"%{escaped}%", limit),
                ).fetchall()
                return [self._row_to_dict(row) for row in rows]

        return await asyncio.to_thread(_search)

    async def get_facts(self, fact_type: str) -> list[dict[str, Any]]:
        """Retrieve all semantic memories of a given *fact_type*.

        Args:
            fact_type: One of the valid fact types.

        Returns:
            A list of dicts for the matching fact type.

        Raises:
            ValueError: If *fact_type* is not valid.

        """
        if fact_type not in VALID_FACT_TYPES:
            raise ValueError(
                f"Invalid fact_type '{fact_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_FACT_TYPES))}",
            )

        def _get() -> list[dict[str, Any]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                rows = conn.execute(
                    "SELECT * FROM semantic_memories WHERE fact_type = ? "
                    "ORDER BY confidence DESC, timestamp DESC",
                    (fact_type,),
                ).fetchall()
                return [self._row_to_dict(row) for row in rows]

        return await asyncio.to_thread(_get)

    async def update_confidence(self, item_id: str, delta: float) -> bool:
        """Adjust the confidence score of a semantic memory entry.

        Args:
            item_id: The unique identifier of the entry.
            delta: Amount to add to the current confidence (can be
                   negative).  The resulting confidence is clamped to
                   [0.0, 1.0].

        Returns:
            ``True`` if the entry was found and updated, ``False`` otherwise.

        """

        def _update() -> bool:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                # Fetch current confidence
                row = conn.execute(
                    "SELECT confidence FROM semantic_memories WHERE id = ?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    return False
                new_conf = max(0.0, min(1.0, row[0] + delta))
                conn.execute(
                    "UPDATE semantic_memories SET confidence = ? WHERE id = ?",
                    (new_conf, item_id),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_update)

    async def get_preferences(self) -> list[dict[str, Any]]:
        """Return all preference-type facts.

        Convenience wrapper around :meth:`get_facts` with
        ``fact_type='preference'``.

        Returns:
            A list of preference dicts.

        """
        return await self.get_facts("preference")

    async def count(self) -> int:
        """Return the total number of semantic memory entries."""

        def _count() -> int:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                row = conn.execute("SELECT COUNT(*) FROM semantic_memories").fetchone()
                return row[0] if row else 0

        return await asyncio.to_thread(_count)

    async def delete(self, item_id: str) -> bool:
        """Delete a semantic memory entry by its identifier.

        Args:
            item_id: The unique identifier of the entry.

        Returns:
            ``True`` if an entry was deleted, ``False`` otherwise.

        """

        def _delete() -> bool:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute(
                    "DELETE FROM semantic_memories WHERE id = ?",
                    (item_id,),
                )
                conn.commit()
                return cursor.rowcount > 0

        return await asyncio.to_thread(_delete)

    async def clear(self) -> int:
        """Remove all semantic memory entries.

        Returns:
            The number of entries removed.

        """

        def _clear() -> int:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("DELETE FROM semantic_memories")
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_clear)
