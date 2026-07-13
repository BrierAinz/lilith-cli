"""SQLite-backed adapter that wraps the existing MemoryStore."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..store import MemoryStore
from .base import MemoryBackend


if TYPE_CHECKING:
    from pathlib import Path


class SQLiteBackend(MemoryBackend):
    """Async adapter over :class:`MemoryStore`.

    This backend delegates all storage to the battle-tested SQLite
    implementation while exposing the unified async
    :class:`MemoryBackend` interface.  Blocking SQLite calls are
    offloaded to a thread via ``asyncio.to_thread`` so that they
    never stall the event loop.
    """

    def __init__(self, db_path: Path) -> None:
        """Initialise the SQLiteBackend with a path to the database file.

        Args:
            db_path: Path to the SQLite database file.

        """
        self._store = MemoryStore(db_path)

    # ------------------------------------------------------------------
    # MemoryBackend interface
    # ------------------------------------------------------------------

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Store *content* and return its integer id as a string."""
        entry_id = await asyncio.to_thread(self._store.add, content=content, metadata=metadata)
        return str(entry_id)

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Substring search via SQLite LIKE."""
        return await asyncio.to_thread(self._store.search, query, limit)

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent entries."""
        return await asyncio.to_thread(self._store.recent, limit)

    async def delete(self, entry_id: str) -> bool:
        """Delete by integer id."""
        return await asyncio.to_thread(self._store.delete, int(entry_id))

    async def clear(self) -> int:
        """Clear all entries and return the count removed."""
        return await asyncio.to_thread(self._store.clear)

    def count(self) -> int:
        """Return the total number of entries."""
        return self._store.count_entries()
