"""Permanent fact-based semantic memory layer."""

from __future__ import annotations

import asyncio
import hashlib
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
                    access_count INTEGER NOT NULL DEFAULT 0,
                    namespace TEXT NOT NULL DEFAULT 'global',
                    expires_at REAL,
                    content_hash TEXT,
                    supersedes_id TEXT,
                    updated_at REAL
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
            # Additive migration for databases created before v8.
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(semantic_memories)")
            }
            migrations = {
                "namespace": "TEXT NOT NULL DEFAULT 'global'",
                "expires_at": "REAL",
                "content_hash": "TEXT",
                "supersedes_id": "TEXT",
                "updated_at": "REAL",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE semantic_memories ADD COLUMN {name} {declaration}"
                    )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_evidence (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    source TEXT,
                    note TEXT,
                    weight REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES semantic_memories(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_namespace_hash "
                "ON semantic_memories(namespace, fact_type, content_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_expiry "
                "ON semantic_memories(expires_at)"
            )
            # Backfill deterministic hashes without removing historical rows.
            for item_id, content in conn.execute(
                "SELECT id, content FROM semantic_memories WHERE content_hash IS NULL"
            ).fetchall():
                conn.execute(
                    "UPDATE semantic_memories SET content_hash=?, updated_at=COALESCE(updated_at, timestamp) "
                    "WHERE id=?",
                    (self._content_hash(content), item_id),
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

    @staticmethod
    def _content_hash(content: str) -> str:
        normalized = " ".join(str(content).strip().casefold().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

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
        namespace: str = "global",
        ttl_seconds: float | None = None,
        provenance: dict[str, Any] | None = None,
        supersedes_id: str | None = None,
        deduplicate: bool = True,
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

        content = str(content).strip()
        if not content:
            raise ValueError("content must not be empty")
        namespace = str(namespace or "global").strip() or "global"
        confidence = max(0.0, min(1.0, float(confidence)))
        item_id = str(uuid.uuid4())
        now = time.time()
        expires_at = now + float(ttl_seconds) if ttl_seconds is not None else None
        digest = self._content_hash(content)
        merged_metadata = dict(metadata or {})
        if provenance:
            merged_metadata.setdefault("provenance", []).append(dict(provenance))

        def _insert() -> str:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN IMMEDIATE")
                if deduplicate:
                    existing = conn.execute(
                        "SELECT * FROM semantic_memories WHERE namespace=? "
                        "AND fact_type=? AND content_hash=? "
                        "AND (expires_at IS NULL OR expires_at > ?) "
                        "ORDER BY confidence DESC, timestamp DESC LIMIT 1",
                        (namespace, fact_type, digest, now),
                    ).fetchone()
                    if existing is not None:
                        old_metadata: dict[str, Any] = {}
                        if existing["metadata"]:
                            try:
                                old_metadata = json.loads(existing["metadata"])
                            except (json.JSONDecodeError, TypeError):
                                old_metadata = {}
                        for key, value in merged_metadata.items():
                            if key == "provenance":
                                old_metadata.setdefault("provenance", []).extend(value)
                            else:
                                old_metadata[key] = value
                        conn.execute(
                            "UPDATE semantic_memories SET confidence=?, metadata=?, "
                            "source=COALESCE(?, source), expires_at=COALESCE(?, expires_at), "
                            "updated_at=? WHERE id=?",
                            (
                                max(float(existing["confidence"]), confidence),
                                json.dumps(old_metadata) if old_metadata else None,
                                source,
                                expires_at,
                                now,
                                existing["id"],
                            ),
                        )
                        conn.execute(
                            "INSERT INTO semantic_evidence "
                            "(id, memory_id, relation, source, note, weight, timestamp) "
                            "VALUES (?, ?, 'corroborates', ?, ?, ?, ?)",
                            (
                                str(uuid.uuid4()), existing["id"], source,
                                "exact-content deduplication", 0.05, now,
                            ),
                        )
                        conn.commit()
                        return str(existing["id"])
                conn.execute(
                    """
                    INSERT INTO semantic_memories
                        (id, content, fact_type, source,
                         confidence, metadata, timestamp,
                         access_count, namespace, expires_at,
                         content_hash, supersedes_id, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        content,
                        fact_type,
                        source,
                        confidence,
                        json.dumps(merged_metadata) if merged_metadata else None,
                        now,
                        namespace,
                        expires_at,
                        digest,
                        supersedes_id,
                        now,
                    ),
                )
                if supersedes_id:
                    conn.execute(
                        "UPDATE semantic_memories SET confidence=MAX(0, confidence - 0.25), "
                        "updated_at=? WHERE id=?",
                        (now, supersedes_id),
                    )
                    conn.execute(
                        "INSERT INTO semantic_evidence "
                        "(id, memory_id, relation, source, note, weight, timestamp) "
                        "VALUES (?, ?, 'contradicts', ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()), supersedes_id, source,
                            f"superseded by {item_id}", -0.25, now,
                        ),
                    )
                conn.commit()
                return item_id

        return await asyncio.to_thread(_insert)

    async def search(
        self,
        query: str,
        limit: int = 5,
        *,
        namespace: str | None = None,
        min_confidence: float = 0.0,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
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
                filters = ["content LIKE ? ESCAPE '\\'", "confidence >= ?"]
                params: list[Any] = [f"%{escaped}%", max(0.0, min(1.0, min_confidence))]
                if namespace is not None:
                    filters.append("namespace = ?")
                    params.append(namespace)
                if not include_expired:
                    filters.append("(expires_at IS NULL OR expires_at > ?)")
                    params.append(time.time())
                where = " AND ".join(filters)
                # Increment access_count for matched items
                conn.execute(
                    "UPDATE semantic_memories SET access_count = access_count + 1 "
                    f"WHERE {where}",
                    tuple(params),
                )
                conn.commit()
                rows = conn.execute(
                    f"SELECT * FROM semantic_memories WHERE {where} "
                    "ORDER BY confidence DESC, timestamp DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
                return [self._row_to_dict(row) for row in rows]

        return await asyncio.to_thread(_search)

    async def get_facts(
        self, fact_type: str, *, namespace: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
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
                filters = ["fact_type = ?"]
                params: list[Any] = [fact_type]
                if namespace is not None:
                    filters.append("namespace = ?")
                    params.append(namespace)
                if not include_expired:
                    filters.append("(expires_at IS NULL OR expires_at > ?)")
                    params.append(time.time())
                rows = conn.execute(
                    "SELECT * FROM semantic_memories WHERE "
                    + " AND ".join(filters)
                    + " ORDER BY confidence DESC, timestamp DESC",
                    tuple(params),
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
                    "UPDATE semantic_memories SET confidence = ?, updated_at = ? WHERE id = ?",
                    (new_conf, time.time(), item_id),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_update)

    async def record_evidence(
        self,
        item_id: str,
        *,
        supports: bool,
        source: str | None = None,
        note: str = "",
        weight: float = 0.1,
    ) -> bool:
        """Attach auditable evidence and adjust confidence atomically."""
        magnitude = max(0.0, min(1.0, abs(float(weight))))
        delta = magnitude if supports else -magnitude

        def _record() -> bool:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT confidence FROM semantic_memories WHERE id=?", (item_id,)
                ).fetchone()
                if row is None:
                    return False
                confidence = max(0.0, min(1.0, float(row[0]) + delta))
                now = time.time()
                conn.execute(
                    "UPDATE semantic_memories SET confidence=?, updated_at=? WHERE id=?",
                    (confidence, now, item_id),
                )
                conn.execute(
                    "INSERT INTO semantic_evidence "
                    "(id, memory_id, relation, source, note, weight, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), item_id,
                        "corroborates" if supports else "contradicts",
                        source, note, delta, now,
                    ),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_record)

    async def evidence(self, item_id: str) -> list[dict[str, Any]]:
        def _read() -> list[dict[str, Any]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM semantic_evidence WHERE memory_id=? "
                    "ORDER BY timestamp DESC", (item_id,)
                ).fetchall()
                return [dict(row) for row in rows]

        return await asyncio.to_thread(_read)

    async def contradict(
        self,
        item_id: str,
        replacement: str,
        *,
        source: str | None = None,
        confidence: float = 0.7,
        namespace: str = "global",
    ) -> str:
        return await self.add(
            replacement,
            fact_type="fact",
            source=source,
            confidence=confidence,
            namespace=namespace,
            supersedes_id=item_id,
        )

    async def prune_expired(self) -> int:
        def _prune() -> int:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                cursor = conn.execute(
                    "DELETE FROM semantic_memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (time.time(),),
                )
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_prune)

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
                conn.execute("PRAGMA foreign_keys=ON")
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
                conn.execute("PRAGMA foreign_keys=ON")
                cursor = conn.execute("DELETE FROM semantic_memories")
                conn.commit()
                return cursor.rowcount

        return await asyncio.to_thread(_clear)
