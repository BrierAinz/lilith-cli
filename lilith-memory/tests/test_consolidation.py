"""Tests for lilith_memory.consolidation (MemoryConsolidator)."""
import pytest
from pathlib import Path

from lilith_memory.layers.working_memory import WorkingMemory
from lilith_memory.layers.episodic_memory import EpisodicMemory
from lilith_memory.layers.semantic_memory import SemanticMemory
from lilith_memory.consolidation import ExtractedFact, MemoryConsolidator, consolidate_session
from lilith_memory.store import MemoryStore


# ── Pattern extraction (sync) ─────────────────────────────────


def test_extract_preference_positive():
    facts = MemoryConsolidator.extract_facts("I really like pizza")
    assert any(f.fact_type == "preference" for f in facts)
    assert any("pizza" in f.content.lower() for f in facts)


def test_extract_preference_negative():
    facts = MemoryConsolidator.extract_facts("I hate broccoli")
    assert any(f.fact_type == "preference" for f in facts)
    assert any("dislikes" in f.content.lower() for f in facts)


def test_extract_identity():
    facts = MemoryConsolidator.extract_facts("My name is Alice")
    assert any(f.fact_type == "identity" for f in facts)
    assert any("Alice" in f.content for f in facts)


def test_extract_procedure():
    facts = MemoryConsolidator.extract_facts("How to bake bread: mix flour, water, yeast, then bake")
    assert any(f.fact_type == "procedure" for f in facts)


def test_extract_no_match():
    facts = MemoryConsolidator.extract_facts("The weather is nice today.")
    assert all(f.fact_type not in ("preference", "identity", "procedure") for f in facts) or facts == []


def test_extracted_fact_dataclass():
    f = ExtractedFact(content="x", fact_type="preference", source="test", confidence=0.5)
    assert f.confidence == 0.5
    assert f.source == "test"


# ── Full pipeline (async) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidation_pipeline_empty(tmp_path: Path):
    wm = WorkingMemory()
    em = EpisodicMemory(tmp_path / "ep.db", decay_seconds=86400)
    sm = SemanticMemory(tmp_path / "sm.db")
    consolidator = MemoryConsolidator(wm, em, sm)
    result = await consolidator.consolidate_session("s1")
    assert result["moved_to_episodic"] == 0
    assert result["facts_extracted"] == 0


@pytest.mark.asyncio
async def test_consolidation_pipeline_with_working_data(tmp_path: Path):
    wm = WorkingMemory()
    em = EpisodicMemory(tmp_path / "ep.db", decay_seconds=86400)
    sm = SemanticMemory(tmp_path / "sm.db")
    consolidator = MemoryConsolidator(wm, em, sm)

    await wm.add("I love dark mode", {"src": "user"})
    await wm.add("My name is Bob", {"src": "user"})

    result = await consolidator.consolidate_session("s1")
    assert result["moved_to_episodic"] == 2
    # Facts should have been extracted and stored in semantic
    assert await sm.count() >= 1
    # Working memory should be cleared
    assert await wm.count() == 0


def test_consolidate_session_lilith_recuerda_entre_sesiones(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    store.store("session-a", "user", "Lilith recuerda la runa Algiz", {"tags": ["runa"]})
    store.store("session-a", "assistant", "Algiz queda guardada")

    result = consolidate_session(store, session_id="session-a")

    assert result["consolidated"] == 2
    reopened = MemoryStore(db_path)
    memories = reopened.recall("session-a", limit=10, scope="long_term")
    contents = [item["content"] for item in memories]
    assert "Lilith recuerda la runa Algiz" in contents
    assert "Algiz queda guardada" in contents

    result_again = consolidate_session(reopened, session_id="session-a")
    assert result_again["consolidated"] == 0
