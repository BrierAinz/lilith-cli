from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_tools.memory import MemoryRecallTool, MemorySaveTool, _reset_cache
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
