"""Abstract base class for memory backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryBackend(ABC):
    """Interface that every memory backend must implement.

    All mutating methods are async so that network-based backends
    (e.g. mem0 cloud) can perform I/O without blocking the event loop.
    Synchronous backends (like SQLite) simply wrap calls with
    ``asyncio.to_thread`` or equivalent helpers.
    """

    @abstractmethod
    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Insert a new memory entry.

        Args:
            content: The text content to store.
            metadata: Optional dict of arbitrary metadata.

        Returns:
            The unique identifier (entry_id) of the stored entry.

        """

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search memories matching *query*.

        Args:
            query: Search text (semantic or substring depending on backend).
            limit: Maximum number of results to return.

        Returns:
            A list of dicts representing matching entries.

        """

    @abstractmethod
    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent memory entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            A list of dicts ordered by most recent first.

        """

    @abstractmethod
    async def delete(self, entry_id: str) -> bool:
        """Delete a memory entry by its identifier.

        Args:
            entry_id: The unique identifier of the entry to delete.

        Returns:
            ``True`` if an entry was deleted, ``False`` otherwise.

        """

    @abstractmethod
    async def clear(self) -> int:
        """Remove all entries from the backend.

        Returns:
            The number of entries that were removed.

        """

    @abstractmethod
    def count(self) -> int:
        """Return the total number of entries (synchronous)."""
