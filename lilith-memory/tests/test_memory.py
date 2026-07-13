import sqlite3

from lilith_memory.store import MemoryStore


# ------------------------------------------------------------------
# Existing tests (kept for regression)
# ------------------------------------------------------------------


def test_add_and_search(tmp_path):
    store = MemoryStore(tmp_path / "test.db")
    store.add("Hola mundo", metadata={"source": "test"})
    results = store.search("mundo")
    assert len(results) == 1
    assert results[0]["content"] == "Hola mundo"


def test_recent(tmp_path):
    store = MemoryStore(tmp_path / "test.db")
    store.add("Primero")
    store.add("Segundo")
    recent = store.recent(limit=2)
    assert len(recent) == 2


# ------------------------------------------------------------------
# New tests
# ------------------------------------------------------------------


def test_count_entries(tmp_path):
    """count_entries() should return 0 initially and increment after adds."""
    store = MemoryStore(tmp_path / "count.db")
    assert store.count_entries() == 0
    store.add("entry one")
    assert store.count_entries() == 1
    store.add("entry two")
    assert store.count_entries() == 2


def test_store_alias(tmp_path):
    """store() should behave identically to add()."""
    store = MemoryStore(tmp_path / "alias.db")
    store.store("default", "user", "alias content", metadata={"via": "store"})
    results = store.search("alias")
    assert len(results) == 1
    assert results[0]["content"] == "alias content"


def test_delete(tmp_path):
    """delete() should remove an entry and return True; returns False for missing id."""
    store = MemoryStore(tmp_path / "del.db")
    store.add("will be deleted")
    store.add("will remain")
    assert store.count_entries() == 2

    # Grab the id of the first entry
    first = store.recent(limit=1)[0]
    remaining_id = first["id"]

    deleted = store.delete(remaining_id)
    assert deleted is True
    assert store.count_entries() == 1

    # Deleting a non-existent id should return False
    deleted = store.delete(999999)
    assert deleted is False


def test_clear(tmp_path):
    """clear() should remove all rows and return the count removed."""
    store = MemoryStore(tmp_path / "clear.db")
    store.add("a")
    store.add("b")
    store.add("c")
    assert store.count_entries() == 3

    removed = store.clear()
    assert removed == 3
    assert store.count_entries() == 0


def test_len(tmp_path):
    """len() should delegate to count_entries()."""
    store = MemoryStore(tmp_path / "len.db")
    assert len(store) == 0
    store.add("x")
    assert len(store) == 1


def test_context_manager(tmp_path):
    """Using MemoryStore as a context manager should work."""
    with MemoryStore(tmp_path / "ctx.db") as store:
        store.add("inside context")
        assert store.count_entries() == 1
        assert len(store) == 1

    # Data should still be accessible after exiting context
    assert store.count_entries() == 1


def test_wal_mode(tmp_path):
    """The database should be opened in WAL journal mode."""
    store = MemoryStore(tmp_path / "wal.db")
    conn = sqlite3.connect(store.db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    # WAL mode is reported as "wal" (lowercase)
    assert mode == "wal"
