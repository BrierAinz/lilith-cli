from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_tools.memory import (
    MemoryEvidenceTool,
    MemoryRecallTool,
    MemorySaveTool,
    _reset_cache,
)
from lilith_tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def reset_memory_tool_cache():
    _reset_cache()
    yield
    _reset_cache()


def test_memory_save_persists_operator_note_with_tags(tmp_path: Path):
    db_path = tmp_path / "memory.db"

    result = MemorySaveTool().execute(
        text="El gateway no se toca.", tags=["operator", "gateway"], db_path=str(db_path)
    )

    assert result.success is True
    assert result.data["id"] > 0
    assert result.data["tags"] == ["operator", "gateway"]
    assert result.data["timestamp"]

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id, role, content, metadata FROM memories WHERE id = ?",
            (result.data["id"],),
        ).fetchone()
    metadata = json.loads(row[3])
    assert row[:3] == ("main", "operator", "El gateway no se toca.")
    assert metadata["tags"] == ["operator", "gateway"]
    assert metadata["timestamp"] == result.data["timestamp"]


def test_memory_recall_returns_relevant_passages_from_same_db(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    save = MemorySaveTool()
    save.execute(text="Usar pytest con semillas aleatorias.", tags=["tests"], db_path=str(db_path))
    save.execute(text="El proveedor MiniMax usa Anthropic tools.", tags=["providers"], db_path=str(db_path))

    result = MemoryRecallTool().execute(query="MiniMax Anthropic", k=1, db_path=str(db_path))

    assert result.success is True
    assert result.data["count"] == 1
    assert "MiniMax" in result.data["passages"][0]["text"]
    assert result.data["passages"][0]["tags"] == ["providers"]


def test_memory_tools_are_registered():
    ToolRegistry.register(MemorySaveTool)
    ToolRegistry.register(MemoryRecallTool)
    assert ToolRegistry.get("memory_save") is MemorySaveTool
    assert ToolRegistry.get("memory_recall") is MemoryRecallTool


def test_semantic_memory_roundtrip_has_provenance_and_evidence(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    saved = MemorySaveTool().execute(
        text="Ainz prefiere respuestas concisas.",
        fact_type="preference",
        namespace="ainz",
        source="explicit_user_statement",
        confidence=0.95,
        provenance={"conversation": "test"},
        db_path=str(db_path),
    )

    assert saved.success is True
    assert saved.data["semantic_id"]
    recalled = MemoryRecallTool().execute(
        query="respuestas concisas", namespace="ainz", min_confidence=0.9,
        db_path=str(db_path),
    )
    assert recalled.success is True
    assert recalled.data["count"] == 1
    passage = recalled.data["passages"][0]
    assert passage["semantic_id"] == saved.data["semantic_id"]
    assert passage["source"] == "explicit_user_statement"
    assert passage["provenance"]

    evidence = MemoryEvidenceTool().execute(
        semantic_id=saved.data["semantic_id"], supports=False,
        source="later_user_correction", note="preference changed", weight=0.2,
        db_path=str(db_path),
    )
    assert evidence.success is True
    assert evidence.data["evidence"][0]["relation"] == "contradicts"
