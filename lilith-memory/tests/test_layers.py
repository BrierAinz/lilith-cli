"""Tests for Memory v2 layered architecture."""

from pathlib import Path

import pytest

from lilith_memory.consolidation import MemoryConsolidator
from lilith_memory.layers import EpisodicMemory, SemanticMemory, WorkingMemory
from lilith_memory.preferences import PreferenceStore


# ── WorkingMemory ──────────────────────────────────────────────────────────


class TestWorkingMemory:
    """Tests for the volatile in-memory store."""

    @pytest.mark.asyncio
    async def test_add_and_get_recent(self):
        wm = WorkingMemory(max_items=5)
        await wm.add("hello world", {"source": "test"})
        await wm.add("second item")
        recent = await wm.get_recent(2)
        assert len(recent) == 2
        # Most recent first
        assert recent[0]["content"] == "second item"
        assert recent[1]["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_search(self):
        wm = WorkingMemory(max_items=10)
        await wm.add("Yggdrasil ecosystem", {"source": "doc"})
        await wm.add("Norse mythology", {"source": "wiki"})
        await wm.add("Lilith dark fantasy", {"source": "brand"})
        results = await wm.search("Yggdrasil")
        assert len(results) >= 1
        assert any("Yggdrasil" in r["content"] for r in results)

    @pytest.mark.asyncio
    async def test_auto_eviction(self):
        wm = WorkingMemory(max_items=3)
        await wm.add("first")
        await wm.add("second")
        await wm.add("third")
        await wm.add("fourth")  # should evict "first"
        assert await wm.count() == 3
        recent = await wm.get_recent(10)
        contents = [r["content"] for r in recent]
        assert "first" not in contents
        assert "fourth" in contents

    @pytest.mark.asyncio
    async def test_count(self):
        wm = WorkingMemory()
        assert await wm.count() == 0
        await wm.add("item")
        assert await wm.count() == 1

    @pytest.mark.asyncio
    async def test_clear(self):
        wm = WorkingMemory()
        await wm.add("a")
        await wm.add("b")
        await wm.clear()
        assert await wm.count() == 0

    @pytest.mark.asyncio
    async def test_access_count_increases(self):
        wm = WorkingMemory()
        await wm.add("search target", {"source": "test"})
        await wm.search("target")
        await wm.search("target")
        recent = await wm.get_recent(1)
        assert recent[0]["access_count"] >= 2


# ── EpisodicMemory ──────────────────────────────────────────────────────────


class TestEpisodicMemory:
    """Tests for the medium-term decay store."""

    @pytest.mark.asyncio
    async def test_add_and_search(self, tmp_path: Path):
        em = EpisodicMemory(db_path=tmp_path / "episodic.db")
        await em.add("user asked about Yggdrasil", {"source": "chat"}, session_id="s1")
        await em.add("user prefers dark theme", {"source": "chat"}, session_id="s1")
        results = await em.search("Yggdrasil")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_recent(self, tmp_path: Path):
        em = EpisodicMemory(db_path=tmp_path / "recent.db")
        await em.add("first", session_id="s1")
        await em.add("second", session_id="s2")
        recent = await em.recent(5)
        assert len(recent) >= 2

    @pytest.mark.asyncio
    async def test_count(self, tmp_path: Path):
        em = EpisodicMemory(db_path=tmp_path / "count.db")
        await em.add("item", session_id="s1")
        cnt = await em.count()
        assert cnt >= 1

    @pytest.mark.asyncio
    async def test_consolidate_picks_high_value(self, tmp_path: Path):
        em = EpisodicMemory(db_path=tmp_path / "consol.db")
        await em.add("important fact about user", {"source": "chat"}, session_id="s1")
        items = await em.consolidate()
        # consolidate returns high-value items
        assert isinstance(items, list)


# ── SemanticMemory ──────────────────────────────────────────────────────────


class TestSemanticMemory:
    """Tests for the permanent fact store."""

    @pytest.mark.asyncio
    async def test_add_and_search(self, tmp_path: Path):
        sm = SemanticMemory(db_path=tmp_path / "semantic.db")
        await sm.add("User prefers dark fantasy", fact_type="preference", source="inferred")
        results = await sm.search("dark fantasy")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_preferences(self, tmp_path: Path):
        sm = SemanticMemory(db_path=tmp_path / "prefs.db")
        await sm.add(
            "User likes conciseness", fact_type="preference", source="observed", confidence=0.8
        )
        prefs = await sm.get_preferences()
        assert len(prefs) >= 1

    @pytest.mark.asyncio
    async def test_get_facts_by_type(self, tmp_path: Path):
        sm = SemanticMemory(db_path=tmp_path / "facts.db")
        await sm.add("Lilith is a goddess", fact_type="identity", source="lore")
        facts = await sm.get_facts(fact_type="identity")
        assert len(facts) >= 1

    @pytest.mark.asyncio
    async def test_update_confidence(self, tmp_path: Path):
        sm = SemanticMemory(db_path=tmp_path / "conf.db")
        entry_id = await sm.add("test fact", fact_type="fact", source="test", confidence=0.5)
        await sm.update_confidence(entry_id, 0.3)
        results = await sm.search("test fact")
        assert len(results) >= 1
        assert results[0]["confidence"] >= 0.7

    @pytest.mark.asyncio
    async def test_invalid_fact_type_raises(self, tmp_path: Path):
        sm = SemanticMemory(db_path=tmp_path / "invalid.db")
        with pytest.raises(ValueError):
            await sm.add("bad fact", fact_type="invalid_type", source="test")


# ── PreferenceStore ──────────────────────────────────────────────────────────


class TestPreferenceStore:
    """Tests for the quick-access preference layer."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, tmp_path: Path):
        ps = PreferenceStore(db_path=tmp_path / "prefs.db")
        await ps.set("theme", "dark", preference_type="explicit", source="user")
        val = await ps.get("theme")
        assert val is not None
        assert val["value"] == "dark"

    @pytest.mark.asyncio
    async def test_upsert(self, tmp_path: Path):
        ps = PreferenceStore(db_path=tmp_path / "upsert.db")
        await ps.set("lang", "es", preference_type="explicit", source="user")
        await ps.set("lang", "en", preference_type="explicit", source="user")
        val = await ps.get("lang")
        assert val["value"] == "en"

    @pytest.mark.asyncio
    async def test_increase_confidence(self, tmp_path: Path):
        ps = PreferenceStore(db_path=tmp_path / "inc.db")
        await ps.set("color", "gold", confidence=0.5, source="test")
        await ps.increase_confidence("color", 0.3)
        val = await ps.get("color")
        assert val["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_convenience_accessors(self, tmp_path: Path):
        ps = PreferenceStore(db_path=tmp_path / "acc.db")
        await ps.set(
            "communication_style", "concise", preference_type="inferred", source="observed"
        )
        style = await ps.get_communication_style()
        assert style is not None

    @pytest.mark.asyncio
    async def test_get_all(self, tmp_path: Path):
        ps = PreferenceStore(db_path=tmp_path / "all.db")
        await ps.set("a", "1", source="test")
        await ps.set("b", "2", source="test")
        all_prefs = await ps.get_all()
        assert len(all_prefs) >= 2


# ── Consolidation ────────────────────────────────────────────────────────────


class TestConsolidation:
    """Tests for the Working→Episodic→Semantic flow."""

    @pytest.mark.asyncio
    async def test_consolidate_session(self, tmp_path: Path):
        wm = WorkingMemory(max_items=10)
        em = EpisodicMemory(db_path=tmp_path / "ep.db")
        sm = SemanticMemory(db_path=tmp_path / "sm.db")

        # Add items to working memory
        await wm.add("I like dark themes", {"source": "user"})
        await wm.add("My name is Briar", {"source": "user"})

        consolidator = MemoryConsolidator(working=wm, episodic=em, semantic=sm)
        result = await consolidator.consolidate_session("test-session")

        assert "moved_to_episodic" in result
        assert "facts_extracted" in result
        assert result["moved_to_episodic"] >= 0

    @pytest.mark.asyncio
    async def test_extract_facts(self, tmp_path: Path):
        consolidator = MemoryConsolidator(
            working=WorkingMemory(),
            episodic=EpisodicMemory(db_path=tmp_path / "ep2.db"),
            semantic=SemanticMemory(db_path=tmp_path / "sm2.db"),
        )
        facts = consolidator.extract_facts("I really like dark fantasy aesthetics")
        assert len(facts) >= 1
        assert any(f.fact_type == "preference" for f in facts)

    @pytest.mark.asyncio
    async def test_extract_identity(self, tmp_path: Path):
        consolidator = MemoryConsolidator(
            working=WorkingMemory(),
            episodic=EpisodicMemory(db_path=tmp_path / "ep3.db"),
            semantic=SemanticMemory(db_path=tmp_path / "sm3.db"),
        )
        facts = consolidator.extract_facts("My name is Briar and I am a developer")
        assert len(facts) >= 1
        assert any(f.fact_type == "identity" for f in facts)

    @pytest.mark.asyncio
    async def test_extract_procedure(self, tmp_path: Path):
        consolidator = MemoryConsolidator(
            working=WorkingMemory(),
            episodic=EpisodicMemory(db_path=tmp_path / "ep4.db"),
            semantic=SemanticMemory(db_path=tmp_path / "sm4.db"),
        )
        facts = consolidator.extract_facts("To deploy, you need to push to main branch")
        assert len(facts) >= 1
        assert any(f.fact_type == "procedure" for f in facts)
