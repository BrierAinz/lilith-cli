"""Medium-term episodic memory with time-based decay."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path


# Default decay period in seconds (7 days)
_DEFAULT_DECAY_SECONDS = 7 * 24 * 60 * 60  # 604_800


class EpisodicMemory:
    """Medium-term memory with configurable time-based decay.

    Episodic memory bridges the gap between volatile working memory and
    permanent semantic memory.  Items are stored in a dedicated SQLite
    table (``episodic_memories``) and are subject to automatic decay:
    each item's ``decay_score`` diminishes over time based on when it
    was last accessed.  When the score drops to zero (or below), the
    item is eligible for pruning.

    High-value episodic memories can be promoted to the semantic layer
    via the :meth:`consolidate` method.
    """

    def __init__(
        self,
        db_path: Path,
        decay_seconds: float = _DEFAULT_DECAY_SECONDS,
    ) -> None:
        """Initialise episodic memory.

        Args:
            db_path: Path to the SQLite database file.
            decay_seconds: Number of seconds before an unread memory
                           fully decays. Defaults to 7 days.

        """
        self._db_path = db_path
        self._decay_seconds = decay_seconds
        self._init_db()

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the episodic_memories table and indexes."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    timestamp REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    decay_score REAL NOT NULL DEFAULT 1.0,
                    session_id TEXT
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodic_timestamp "
                "ON episodic_memories(timestamp DESC)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic_memories(session_id)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodic_decay ON episodic_memories(decay_score)",
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

    def _compute_decay_score(self, last_accessed: float, timestamp: float) -> float:
        """Compute the current decay score based on elapsed time.

        The score starts at 1.0 and linearly decreases to 0.0 over
        ``decay_seconds`` since the item was created.  Each access
        resets ``last_accessed`` and boosts the score.

        Args:
            last_accessed: Epoch time of last access.
            timestamp: Epoch time when the item was created.

        Returns:
            A float between 0.0 and 1.0.

        """
        now = time.time()
        # Time since creation
        age = now - timestamp
        # Decay factor: 1.0 at creation, 0.0 at decay_seconds
        decay = max(0.0, 1.0 - (age / self._decay_seconds))
        # Access boost: accessing recently slows decay
        time_since_access = now - last_accessed
        access_penalty = min(time_since_access / self._decay_seconds, 1.0)
        return decay * (1.0 - 0.5 * access_penalty)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Add a new episodic memory entry.

        Args:
            content: The text content to store.
            metadata: Optional dict of arbitrary metadata.
            session_id: Optional session identifier for grouping.

        Returns:
            The unique identifier of the new entry.

        """
        item_id = str(uuid.uuid4())
        now = time.time()

        def _insert() -> None:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "PRAGMA journal_mode=WAL",
                )
                conn.execute(
                    """
                    INSERT INTO episodic_memories
                        (id, content, metadata, timestamp, last_accessed, decay_score, session_id)
                    VALUES (?, ?, ?, ?, ?, 1.0, ?)
                    """,
                    (
                        item_id,
                        content,
                        json.dumps(metadata) if metadata else None,
                        now,
                        now,
                        session_id,
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_insert)
        return item_id

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search episodic memories by substring, ordered by relevance.

        Items with a higher decay_score are prioritised.

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
                rows = conn.execute(
                    "SELECT * FROM episodic_memories "
                    "WHERE content LIKE ? ESCAPE '\\' "
                    "ORDER BY decay_score DESC, timestamp DESC LIMIT ?",
                    (f"%{escaped}%", limit),
                ).fetchall()
                return [self._row_to_dict(row) for row in rows]

        return await asyncio.to_thread(_search)

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent episodic memories, updating their access time.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            A list of dicts ordered by most recent first.

        """
        now = time.time()

        def _recent() -> list[dict[str, Any]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                rows = conn.execute(
                    "SELECT * FROM episodic_memories ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                results = [self._row_to_dict(row) for row in rows]
                # Update last_accessed for fetched items
                for r in results:
                    conn.execute(
                        "UPDATE episodic_memories SET last_accessed = ? WHERE id = ?",
                        (now, r["id"]),
                    )
                conn.commit()
                return results

        return await asyncio.to_thread(_recent)

    async def consolidate(self, threshold: float = 0.6) -> list[dict[str, Any]]:
        """Return items whose decay_score exceeds *threshold* for promotion.

        This method does **not** remove items from episodic memory.
        It merely identifies candidates suitable for the semantic layer.

        Args:
            threshold: Minimum decay_score for an item to be considered
                       high-value.  Defaults to 0.6.

        Returns:
            A list of dicts representing high-value entries.

        """

        def _consolidate() -> list[dict[str, Any]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                # Recompute decay scores and select high-value entries
                rows = conn.execute(
                    "SELECT * FROM episodic_memories ORDER BY decay_score DESC",
                ).fetchall()
                results: list[dict[str, Any]] = []
                for row in rows:
                    item = self._row_to_dict(row)
                    current_score = self._compute_decay_score(
                        item["last_accessed"],
                        item["timestamp"],
                    )
                    if current_score >= threshold:
                        item["current_decay_score"] = current_score
                        results.append(item)
                return results

        return await asyncio.to_thread(_consolidate)

    async def prune_expired(self) -> int:
        """Remove episodic memories whose decay_score has reached zero.

        Returns:
            The number of entries pruned.

        """
        now = time.time()
        cutoff = now - self._decay_seconds

        def _prune() -> int:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute(
                    "DELETE FROM episodic_memories WHERE timestamp < ?",
                    (cutoff,),
                )
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_prune)

    async def count(self) -> int:
        """Return the total number of episodic memory entries."""

        def _count() -> int:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                row = conn.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()
                return row[0] if row else 0

        return await asyncio.to_thread(_count)
