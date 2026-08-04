"""Memory e2e: ``memory_save`` then ``memory_recall`` round-trip.

The two memory tools default to ``~/.yggdrasil/memory.db`` and they
cache stores in module-level dicts. To avoid polluting the real
operator memory we point ``YGGDRASIL_HOME`` at a temp dir via the
``isolated_memory_db`` fixture; the default path derived from that
env is unique per test.

This e2e test does NOT need a provider API key — both tools are
local SQLite + a HashEmbedder. It is still marked ``@pytest.mark.e2e``
so it stays opt-in via ``-m e2e`` (and so it doesn't run every time
during the fast default suite).
"""

from __future__ import annotations

import pytest

from lilith_tools import ToolRegistry
from lilith_tools import memory as memory_mod


@pytest.fixture
def memory_save_tool():
    tool_cls = ToolRegistry.get("memory_save")
    assert tool_cls is not None, "memory_save not registered"
    return tool_cls()


@pytest.fixture
def memory_recall_tool():
    tool_cls = ToolRegistry.get("memory_recall")
    assert tool_cls is not None, "memory_recall not registered"
    return tool_cls()


@pytest.fixture(autouse=True)
def _reset_memory_caches() -> None:
    """Drop the in-process memory store / vector caches between tests.

    The memory tools keep a module-level cache keyed on db_path so
    cross-test reuse doesn't risk test pollution. Caches are safe
    per-path, but a per-test teardown keeps the suite honest.
    """
    memory_mod._reset_cache()
    yield
    memory_mod._reset_cache()


def test_guardar_y_recordar_recupera_el_texto(
    memory_save_tool,
    memory_recall_tool,
    isolated_memory_db,
) -> None:
    """Round-trip a distinctive phrase and confirm recall returns it."""
    save = memory_save_tool.execute(
        text="El colibrí zunzuncito de Cuba late 80 veces por segundo.",
        tags=["e2e", "test", "fauna"],
    )
    assert save.success, f"memory_save failed — error={save.error!r}"
    save_data = save.data or {}
    saved_id = save_data.get("id")
    assert isinstance(saved_id, (int, str)) and saved_id, save_data

    # Use a query whose cosine-most-similar passage is unambiguously
    # the one we just saved. The HashEmbedder is term-based, so a
    # direct lexical match must work too.
    recall = memory_recall_tool.execute(query="colibrí zunzuncito Cuba latido", k=3)
    assert recall.success, f"memory_recall failed — error={recall.error!r}"

    passages = (recall.data or {}).get("passages") or []
    assert passages, (
        f"memory_recall returned no passages — db_path={isolated_memory_db}\n"
        f"recall.data={recall.data!r}"
    )

    # The saved text must appear in the top-k passages (case insensitive
    # because embeddings and chunkers may lowercase).
    found = any(
        "colibrí" in (p.get("text") or "").lower()
        or "zunzuncito" in (p.get("text") or "").lower()
        for p in passages
    )
    assert found, (
        f"saved text not in top-k passages — got passages={passages!r}"
    )


def test_guardar_persiste_en_dos_llamadas_recall(
    memory_save_tool,
    memory_recall_tool,
    isolated_memory_db,
) -> None:
    """A note saved in one call must still be retrievable by a later recall."""
    marker = "MARKER-e2e-NOCHE-LILITH-V7-XYZZY"
    save = memory_save_tool.execute(text=f"{marker} Nota persistente de prueba.")
    assert save.success, f"memory_save failed — error={save.error!r}"

    # Two separate recall calls. The VectorRecall uses an in-memory
    # index built on first use — the second call exercises that
    # persistence isn't reliant on a transient cache.
    for call in (1, 2):
        out = memory_recall_tool.execute(query=marker, k=5)
        assert out.success, f"recall #{call} failed — error={out.error!r}"
        passages = (out.data or {}).get("passages") or []
        assert any(marker in (p.get("text") or "") for p in passages), (
            f"recall #{call} did not surface marker — passages={passages!r}"
        )
