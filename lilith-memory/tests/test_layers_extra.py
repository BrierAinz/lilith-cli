"""Tests for lilith_memory.layers."""
import pytest
from pathlib import Path

from lilith_memory.layers.working_memory import WorkingMemory
from lilith_memory.layers.episodic_memory import EpisodicMemory
from lilith_memory.layers.semantic_memory import SemanticMemory, VALID_FACT_TYPES


# ── WorkingMemory ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_working_memory_add_and_get_recent():
    wm = WorkingMemory(max_items=10)
    item_id = await wm.add("hello", {"src": "user"})
    assert isinstance(item_id, str)
    items = await wm.get_recent(n=5)
    assert len(items) == 1
    assert items[0]["content"] == "hello"
    assert items[0]["metadata"] == {"src": "user"}


@pytest.mark.asyncio
async def test_working_memory_eviction():
    wm = WorkingMemory(max_items=3)
    for i in range(5):
        await wm.add(f"item-{i}")
    assert await wm.count() == 3
    items = await wm.get_recent(n=10)
    # Oldest should be evicted
    contents = [it["content"] for it in items]
    assert "item-0" not in contents
    assert "item-1" not in contents
    assert "item-4" in contents


@pytest.mark.asyncio
async def test_working_memory_search():
    wm = WorkingMemory()
    await wm.add("the quick brown fox")
    await wm.add("lazy dog")
    results = await wm.search("fox")
    assert len(results) == 1
    assert "fox" in results[0]["content"]


@pytest.mark.asyncio
async def test_working_memory_clear():
    wm = WorkingMemory()
    await wm.add("a")
    await wm.add("b")
    removed = await wm.clear()
    assert removed == 2
    assert await wm.count() == 0


# ── EpisodicMemory ────────────────────────────────────────────


@pytest.fixture
def episodic_db(tmp_path: Path) -> Path:
    return tmp_path / "episodic.db"


@pytest.mark.asyncio
async def test_episodic_add_and_search(episodic_db: Path):
    em = EpisodicMemory(episodic_db, decay_seconds=86400)
    eid = await em.add("user asked about Yggdrasil", session_id="s1")
    assert isinstance(eid, str)
    results = await em.search("Yggdrasil")
    assert len(results) == 1
    assert results[0]["content"] == "user asked about Yggdrasil"


@pytest.mark.asyncio
async def test_episodic_recent(episodic_db: Path):
    em = EpisodicMemory(episodic_db, decay_seconds=86400)
    await em.add("first")
    await em.add("second")
    items = await em.recent(limit=5)
    assert items[0]["content"] == "second"


@pytest.mark.asyncio
async def test_episodic_count(episodic_db: Path):
    em = EpisodicMemory(episodic_db, decay_seconds=86400)
    assert await em.count() == 0
    await em.add("a")
    await em.add("b")
    assert await em.count() == 2


@pytest.mark.asyncio
async def test_episodic_prune_expired(episodic_db: Path):
    em = EpisodicMemory(episodic_db, decay_seconds=0.001)
    await em.add("will-expire")
    import asyncio
    await asyncio.sleep(0.05)
    pruned = await em.prune_expired()
    assert pruned >= 1


@pytest.mark.asyncio
async def test_episodic_metadata(episodic_db: Path):
    em = EpisodicMemory(episodic_db, decay_seconds=86400)
    await em.add("meta-test", metadata={"tag": "test"}, session_id="s")
    results = await em.search("meta-test")
    assert results[0]["metadata"] == {"tag": "test"}


# ── SemanticMemory ────────────────────────────────────────────


@pytest.fixture
def semantic_db(tmp_path: Path) -> Path:
    return tmp_path / "semantic.db"


@pytest.mark.asyncio
async def test_semantic_add_and_search(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    fid = await sm.add("user likes dark mode", fact_type="preference")
    assert isinstance(fid, str)
    results = await sm.search("dark mode")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_semantic_invalid_fact_type(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    with pytest.raises(ValueError):
        await sm.add("x", fact_type="invalid")


@pytest.mark.asyncio
async def test_semantic_get_facts(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    await sm.add("pref 1", fact_type="preference")
    await sm.add("fact 1", fact_type="fact")
    await sm.add("pref 2", fact_type="preference")
    prefs = await sm.get_facts("preference")
    assert len(prefs) == 2


@pytest.mark.asyncio
async def test_semantic_update_confidence(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    fid = await sm.add("test", confidence=0.5)
    assert await sm.update_confidence(fid, 0.3) is True
    results = await sm.search("test")
    assert abs(results[0]["confidence"] - 0.8) < 0.001


@pytest.mark.asyncio
async def test_semantic_update_confidence_clamp(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    fid = await sm.add("clamp", confidence=0.95)
    await sm.update_confidence(fid, 0.5)
    results = await sm.search("clamp")
    assert results[0]["confidence"] == 1.0


@pytest.mark.asyncio
async def test_semantic_update_confidence_missing(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    assert await sm.update_confidence("nonexistent-id", 0.1) is False


@pytest.mark.asyncio
async def test_semantic_get_preferences(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    await sm.add("p1", fact_type="preference")
    await sm.add("f1", fact_type="fact")
    prefs = await sm.get_preferences()
    assert len(prefs) == 1
    assert prefs[0]["fact_type"] == "preference"


@pytest.mark.asyncio
async def test_semantic_delete(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    fid = await sm.add("delete-me")
    assert await sm.delete(fid) is True
    assert await sm.delete(fid) is False


@pytest.mark.asyncio
async def test_semantic_clear(semantic_db: Path):
    sm = SemanticMemory(semantic_db)
    await sm.add("a")
    await sm.add("b")
    removed = await sm.clear()
    assert removed == 2
    assert await sm.count() == 0


def test_valid_fact_types():
    assert "preference" in VALID_FACT_TYPES
    assert "fact" in VALID_FACT_TYPES
    assert "identity" in VALID_FACT_TYPES
