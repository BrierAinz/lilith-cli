"""mem0-powered persistent memory backend."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .base import MemoryBackend


class Mem0Backend(MemoryBackend):
    """Memory backend backed by `mem0 <https://github.com/mem0ai/mem0>`_.

    mem0 provides persistent, long-term memory with automatic vector
    search (semantic similarity).  This backend:

    * Auto-configures from environment variables:
      ``MEM0_API_KEY`` for the managed cloud, or falls back to a
      local SQLite+Qdrant config when the key is absent.
    * Gracefully degrades: if ``mem0ai`` is not installed the constructor
      raises an ``ImportError`` with a helpful message, and callers can
      catch that to fall back to :class:`SQLiteBackend`.
    * Stores arbitrary *metadata* via mem0's native metadata dict.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise the Mem0Backend, connecting to mem0 cloud or local storage.

        Args:
            db_path: Optional path for local storage. Falls back to ``.mem0``
                     in the current directory.

        """
        try:
            from mem0 import Memory
        except ImportError as exc:
            raise ImportError(
                "mem0ai is required for Mem0Backend. "
                "Install it with: pip install lilith-memory[mem0]",
            ) from exc

        api_key = os.environ.get("MEM0_API_KEY")
        if api_key:
            # Cloud / managed mode
            self._mem = Memory.from_config(
                {
                    "llm": {"provider": "openai", "config": {"api_key": api_key}},
                    "embedder": {
                        "provider": "openai",
                        "config": {"api_key": api_key},
                    },
                    "vector_store": {
                        "provider": "openai",
                        "config": {"api_key": api_key},
                    },
                },
            )
        else:
            # Local mode - store data alongside the requested db_path
            local_dir = str(db_path.parent) if db_path else ".mem0"
            Path(local_dir).mkdir(parents=True, exist_ok=True)
            self._mem = Memory.from_config(
                {
                    "llm": {"provider": "ollama", "config": {"model": "llama3"}},
                    "embedder": {
                        "provider": "ollama",
                        "config": {"model": "nomic-embed-text"},
                    },
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {
                            "path": str(Path(local_dir) / "qdrant"),
                            "collection_name": "lilith_mem0",
                        },
                    },
                    "version_store": {
                        "provider": "sqlite",
                        "config": {
                            "path": str(Path(local_dir) / "mem0.db"),
                        },
                    },
                },
            )

        # Internal counter for fast count (mem0 doesn't expose count natively)
        self._local_db_path = db_path or Path(".mem0") / "mem0_meta.db"
        self._init_meta_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_meta_db(self) -> None:
        """Create a lightweight SQLite db to track entry count & id mapping."""
        self._local_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._local_db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS mem0_meta (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     mem0_id TEXT NOT NULL UNIQUE,
                     content TEXT NOT NULL,
                     metadata TEXT
                   )""",
            )
            conn.commit()

    # ------------------------------------------------------------------
    # MemoryBackend interface
    # ------------------------------------------------------------------

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Add a memory entry via mem0 and record it in the local meta db."""
        result = await asyncio.to_thread(self._mem.add, content, metadata=metadata or {})
        # mem0 returns a dict like {"id": "..."} or {"results": [...]}
        # Normalise: extract the first id.
        mem0_id: str = ""
        if isinstance(result, dict):
            mem0_id = result.get("id") or result.get("results", [{}])[0].get("id", "")
        if isinstance(result, list) and result:
            mem0_id = result[0].get("id", "")
        if not mem0_id:
            # Fallback: use the content hash as identifier
            mem0_id = str(hash(content))

        with sqlite3.connect(self._local_db_path) as conn:
            conn.execute(
                "INSERT INTO mem0_meta (mem0_id, content, metadata) VALUES (?, ?, ?)",
                (mem0_id, content, json.dumps(metadata) if metadata else None),
            )
            conn.commit()

        return mem0_id

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Semantic search via mem0's vector engine.

        Falls back to a substring scan if mem0 search raises.
        """
        try:
            results = await asyncio.to_thread(self._mem.search, query, limit=limit)
        except Exception:
            # Graceful degradation: scan the meta db
            return await self._local_search(query, limit)

        raw = results if isinstance(results, list) else results.get("results", [])
        entries: list[dict[str, Any]] = [
            {
                "id": item.get("id", ""),
                "content": item.get("memory", item.get("content", "")),
                "metadata": item.get("metadata"),
                "score": item.get("score"),
            }
            for item in raw
            if isinstance(item, dict)
        ]
        return entries[:limit]

    async def _local_search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Fallback substring search over the local meta db."""
        with sqlite3.connect(self._local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                "SELECT * FROM mem0_meta WHERE content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                (f"%{escaped}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent entries from the local meta db."""
        with sqlite3.connect(self._local_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM mem0_meta ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    async def delete(self, entry_id: str) -> bool:
        """Delete an entry from mem0 and the local meta db."""
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._mem.delete, entry_id)

        with sqlite3.connect(self._local_db_path) as conn:
            cursor = conn.execute("DELETE FROM mem0_meta WHERE mem0_id = ?", (entry_id,))
            conn.commit()
            return cursor.rowcount > 0

    async def clear(self) -> int:
        """Clear all entries from mem0 and the local meta db."""
        count = self.count()

        # Best-effort: try to clear mem0 entries
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._mem.reset)

        with sqlite3.connect(self._local_db_path) as conn:
            conn.execute("DELETE FROM mem0_meta")
            conn.commit()

        return count

    def count(self) -> int:
        """Return the total number of entries in the local meta db."""
        with sqlite3.connect(self._local_db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM mem0_meta").fetchone()
            return row[0] if row else 0
