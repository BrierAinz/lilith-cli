"""Tests for the SQLite-backend adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lilith_memory.backends import SQLiteBackend
from lilith_memory.backends.base import MemoryBackend
from lilith_memory.store import MemoryStore


if TYPE_CHECKING:
    from pathlib import Path


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_adapter_implements_interface():
    """SQLiteBackend should be a subclass of MemoryBackend."""
    assert issubclass(SQLiteBackend, MemoryBackend)


def test_adapter_inherits_abstract_methods():
    """SQLiteBackend should implement all abstract methods from MemoryBackend."""
    abstract = set(getattr(MemoryBackend, "__abstractmethods__", set()))
    implemented = set(dir(SQLiteBackend))
    # Every abstract method must be defined on the concrete class
    for method in abstract:
        assert method in implemented, f"SQLiteBackend missing abstract method: {method}"


@pytest.mark.asyncio
async def test_add_returns_valid_id(tmp_path: Path):
    """add() should return a non-empty string id."""
    db = tmp_path / "adapter_add.db"
    adapter = SQLiteBackend(db)

    entry_id = await adapter.add("hello from adapter", metadata={"origin": "test"})
    assert entry_id  # non-empty
    assert isinstance(entry_id, str)


@pytest.mark.asyncio
async def test_search_finds_entry(tmp_path: Path):
    """search() should find the inserted content."""
    db = tmp_path / "adapter_search.db"
    adapter = SQLiteBackend(db)

    await adapter.add("unique needle content")
    results = await adapter.search("needle")
    assert len(results) >= 1
    assert any("needle" in r["content"] for r in results)


@pytest.mark.asyncio
async def test_recent_and_count(tmp_path: Path):
    """recent() should return entries in reverse order; count() should reflect total."""
    db = tmp_path / "adapter_recent.db"
    adapter = SQLiteBackend(db)

    await adapter.add("first entry")
    await adapter.add("second entry")

    assert adapter.count() == 2

    recent = await adapter.recent(limit=2)
    assert len(recent) == 2
    # Most recent first
    assert "second" in recent[0]["content"]


@pytest.mark.asyncio
async def test_delete_and_clear(tmp_path: Path):
    """delete() should remove one entry; clear() should remove all remaining."""
    db = tmp_path / "adapter_del.db"
    adapter = SQLiteBackend(db)

    entry_id = await adapter.add("to be deleted")
    await adapter.add("will remain")
    assert adapter.count() == 2

    # Delete the first entry
    deleted = await adapter.delete(entry_id)
    assert deleted is True
    assert adapter.count() == 1

    # Clear everything
    removed = await adapter.clear()
    assert removed == 1
    assert adapter.count() == 0


@pytest.mark.asyncio
async def test_consistency_with_memory_store(tmp_path: Path):
    """SQLiteBackend should produce the same results as the raw MemoryStore."""
    db_path = tmp_path / "compat.db"
    raw = MemoryStore(db_path)
    adapter = SQLiteBackend(db_path)

    # Add via adapter, verify via raw store
    entry_id = await adapter.add("consistency test", metadata={"key": "value"})
    assert raw.count_entries() >= 1

    # Search via adapter
    results = await adapter.search("consistency")
    assert len(results) >= 1
    assert any("consistency" in r["content"] for r in results)

    # Delete via adapter
    await adapter.delete(entry_id)
    assert adapter.count() == 0
    assert raw.count_entries() == 0
