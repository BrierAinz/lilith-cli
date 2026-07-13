"""Tests for lilith_memory.store (MemoryStore)."""
import pytest
from pathlib import Path
import tempfile

from lilith_memory.read_guard import guard
from lilith_memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "test_memory.db")


def test_store_init_creates_table(store: MemoryStore):
    assert store.count() == 0


def test_store_entry(store: MemoryStore):
    entry_id = store.store("session1", "user", "hello", {"key": "value"})
    assert entry_id > 0
    assert store.count() == 1


def test_recall_returns_stored(store: MemoryStore):
    store.store("session1", "user", "msg1")
    store.store("session1", "assistant", "reply1")
    items = store.recall("session1", limit=10)
    assert len(items) == 2
    # Newest first
    assert items[0]["content"] == "reply1"


def test_recall_respects_session(store: MemoryStore):
    store.store("a", "user", "in a")
    store.store("b", "user", "in b")
    assert len(store.recall("a")) == 1
    assert store.recall("a")[0]["content"] == "in a"


def test_search_substring(store: MemoryStore):
    store.store("s1", "user", "the quick brown fox")
    store.store("s1", "user", "lazy dog")
    results = store.search("fox")
    assert len(results) == 1
    assert "fox" in results[0]["content"]


def test_count_sessions(store: MemoryStore):
    store.store("a", "user", "x")
    store.store("a", "user", "y")
    store.store("b", "user", "z")
    sessions = store.sessions()
    assert set(sessions) == {"a", "b"}


def test_add_convenience(store: MemoryStore):
    eid = store.add("test content", role="user", session_id="s")
    assert eid > 0
    assert store.count() == 1


def test_count_entries_alias(store: MemoryStore):
    store.add("a")
    store.add("b")
    assert store.count_entries() == 2


def test_recent_alias(store: MemoryStore):
    store.add("first", session_id="default")
    store.add("second", session_id="default")
    items = store.recent(limit=5)
    assert items[0]["content"] == "second"


def test_delete_entry(store: MemoryStore):
    eid = store.add("deleteme")
    assert store.delete(eid) is True
    assert store.delete(eid) is False
    assert store.count() == 0


def test_clear(store: MemoryStore):
    store.add("a")
    store.add("b")
    removed = store.clear()
    assert removed == 2
    assert store.count() == 0


def test_len(store: MemoryStore):
    assert len(store) == 0
    store.add("a")
    store.add("b")
    assert len(store) == 2


def test_context_manager(tmp_path: Path):
    with MemoryStore(tmp_path / "ctx.db") as s:
        s.add("inside")
        assert s.count() == 1


def test_read_guard_allow_all_no_change():
    results = [{"id": 1}, {"id": 2}]
    assert guard(results, requester="odin", policy=None) == results


def test_read_guard_policy_filters_by_requester():
    results = [
        {"content": "visible", "requester": "odin"},
        {"content": "hidden", "requester": "mimir"},
    ]
    filtered = guard(
        results,
        requester="odin",
        policy=lambda item, requester: item["requester"] == requester,
    )
    assert filtered == [results[0]]


def test_recall_requester_passes_through_read_guard(store: MemoryStore):
    store.store("session1", "user", "odin")
    store.store("session1", "user", "mimir")
    seen_requesters = []

    def policy(item, requester):
        seen_requesters.append(requester)
        return item["content"] == requester

    items = store.recall("session1", requester="odin", policy=policy)
    assert [item["content"] for item in items] == ["odin"]
    assert seen_requesters == ["odin", "odin"]


def test_recall_scope_filters_metadata(store: MemoryStore):
    store.store("session1", "user", "visible", {"scope": "public"})
    store.store("session1", "user", "hidden", {"scope": "private"})
    items = store.recall("session1", scope="public")
    assert [item["content"] for item in items] == ["visible"]
