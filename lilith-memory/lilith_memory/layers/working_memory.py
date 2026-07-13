"""Volatile, context-window sized working memory layer."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class WorkingMemoryItem:
    """A single item stored in working memory."""

    id: str
    content: str
    metadata: dict[str, Any]
    timestamp: float
    access_count: int = 0


class WorkingMemory:
    """Volatile, in-memory store with bounded capacity.

    Working memory is the shortest-lived layer: it holds the most recent
    conversational context and auto-evicts the oldest items when the
    configured capacity is exceeded.  It is **not** persisted across
    restarts.

    Thread-safety is provided by an ``asyncio.Lock`` so that concurrent
    coroutines can safely read/write the deque.
    """

    def __init__(self, max_items: int = 50) -> None:
        """Initialise working memory with a bounded deque.

        Args:
            max_items: Maximum number of items to keep.  When the deque
                       exceeds this limit, the oldest item is silently
                       discarded.

        """
        self._max_items = max_items
        self._items: deque[WorkingMemoryItem] = deque(maxlen=max_items)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Add a new item to working memory.

        Args:
            content: The text content to store.
            metadata: Optional dict of arbitrary metadata.

        Returns:
            The unique identifier assigned to the new item.

        """
        item = WorkingMemoryItem(
            id=str(uuid.uuid4()),
            content=content,
            metadata=metadata or {},
            timestamp=time.time(),
            access_count=0,
        )
        async with self._lock:
            self._items.append(item)
        return item.id

    async def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the *n* most recent items, newest first.

        Args:
            n: How many items to return.

        Returns:
            A list of dicts representing the most recent items.

        """
        async with self._lock:
            items = list(self._items)
        # Newest first
        items.reverse()
        results: list[dict[str, Any]] = []
        for item in items[:n]:
            item.access_count += 1
            results.append(
                {
                    "id": item.id,
                    "content": item.content,
                    "metadata": item.metadata,
                    "timestamp": item.timestamp,
                    "access_count": item.access_count,
                },
            )
        return results

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search working memory with a case-insensitive substring query.

        Args:
            query: Substring to search for.
            limit: Maximum number of results.

        Returns:
            A list of matching items ordered by most recent first.

        """
        query_lower = query.lower()
        async with self._lock:
            items = list(self._items)
        # Iterate newest first
        items.reverse()
        results: list[dict[str, Any]] = []
        for item in items:
            if query_lower in item.content.lower():
                item.access_count += 1
                results.append(
                    {
                        "id": item.id,
                        "content": item.content,
                        "metadata": item.metadata,
                        "timestamp": item.timestamp,
                        "access_count": item.access_count,
                    },
                )
                if len(results) >= limit:
                    break
        return results

    async def clear(self) -> int:
        """Remove all items from working memory.

        Returns:
            The number of items that were removed.

        """
        async with self._lock:
            count = len(self._items)
            self._items.clear()
        return count

    async def count(self) -> int:
        """Return the number of items currently in working memory."""
        async with self._lock:
            return len(self._items)
