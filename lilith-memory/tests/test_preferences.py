"""Tests for lilith_memory.preferences (PreferenceStore)."""
import pytest
from pathlib import Path

from lilith_memory.preferences import PreferenceStore, VALID_PREFERENCE_TYPES


@pytest.fixture
def store(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "prefs.db")


@pytest.mark.asyncio
async def test_set_and_get(store: PreferenceStore):
    await store.set("communication_style", "terse")
    pref = await store.get("communication_style")
    assert pref is not None
    assert pref["value"] == "terse"
    assert pref["key"] == "communication_style"


@pytest.mark.asyncio
async def test_set_invalid_type(store: PreferenceStore):
    with pytest.raises(ValueError):
        await store.set("key", "value", preference_type="invalid")


@pytest.mark.asyncio
async def test_get_missing(store: PreferenceStore):
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_set_upserts(store: PreferenceStore):
    await store.set("lang", "en")
    await store.set("lang", "es", confidence=0.9)
    pref = await store.get("lang")
    assert pref["value"] == "es"
    assert pref["confidence"] == 0.9


@pytest.mark.asyncio
async def test_get_all(store: PreferenceStore):
    await store.set("a", "1")
    await store.set("b", "2")
    all_prefs = await store.get_all()
    assert len(all_prefs) == 2


@pytest.mark.asyncio
async def test_delete(store: PreferenceStore):
    await store.set("temp", "x")
    assert await store.delete("temp") is True
    assert await store.delete("temp") is False


@pytest.mark.asyncio
async def test_increase_confidence(store: PreferenceStore):
    await store.set("k", "v", confidence=0.5)
    assert await store.increase_confidence("k", 0.2) is True
    pref = await store.get("k")
    assert pref["confidence"] == 0.7


@pytest.mark.asyncio
async def test_increase_confidence_clamp(store: PreferenceStore):
    await store.set("k", "v", confidence=0.9)
    await store.increase_confidence("k", 0.5)
    pref = await store.get("k")
    assert pref["confidence"] == 1.0  # clamped


@pytest.mark.asyncio
async def test_increase_confidence_missing(store: PreferenceStore):
    assert await store.increase_confidence("missing", 0.1) is False


@pytest.mark.asyncio
async def test_get_communication_style(store: PreferenceStore):
    await store.set("communication_style", "direct")
    style = await store.get_communication_style()
    assert style == "direct"


@pytest.mark.asyncio
async def test_get_preferred_language(store: PreferenceStore):
    await store.set("preferred_language", "es")
    lang = await store.get_preferred_language()
    assert lang == "es"


@pytest.mark.asyncio
async def test_get_design_preferences(store: PreferenceStore):
    await store.set("design_color", "dark")
    await store.set("design_font", "monospace")
    await store.set("unrelated", "x")
    design = await store.get_design_preferences()
    assert "design_color" in design
    assert "design_font" in design
    assert "unrelated" not in design


def test_valid_types():
    assert "explicit" in VALID_PREFERENCE_TYPES
    assert "inferred" in VALID_PREFERENCE_TYPES
