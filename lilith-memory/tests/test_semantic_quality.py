from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lilith_memory.layers.semantic_memory import SemanticMemory


@pytest.mark.asyncio
async def test_exact_duplicates_corroborate_instead_of_multiplying(tmp_path: Path) -> None:
    memory = SemanticMemory(tmp_path / "memory.sqlite3")
    first = await memory.add(
        "Ainz prefiere respuestas directas",
        fact_type="preference",
        source="telegram",
        confidence=0.7,
        namespace="user:ainz",
        provenance={"message_id": "m1"},
    )
    second = await memory.add(
        "  ainz   PREFIERE respuestas directas ",
        fact_type="preference",
        source="cli",
        confidence=0.9,
        namespace="user:ainz",
        provenance={"message_id": "m2"},
    )
    assert second == first
    assert await memory.count() == 1
    rows = await memory.get_facts("preference", namespace="user:ainz")
    assert rows[0]["confidence"] == 0.9
    assert len(rows[0]["metadata"]["provenance"]) == 2
    assert (await memory.evidence(first))[0]["relation"] == "corroborates"


@pytest.mark.asyncio
async def test_namespaces_and_ttl_are_enforced_on_recall(tmp_path: Path) -> None:
    memory = SemanticMemory(tmp_path / "memory.sqlite3")
    await memory.add("same keyword private", namespace="project:a")
    await memory.add("same keyword public", namespace="project:b")
    expired = await memory.add("same keyword expired", namespace="project:a", ttl_seconds=0)

    project_a = await memory.search("same keyword", namespace="project:a")
    assert [row["content"] for row in project_a] == ["same keyword private"]
    including_expired = await memory.search(
        "same keyword", namespace="project:a", include_expired=True
    )
    assert {row["id"] for row in including_expired} >= {expired}
    assert await memory.prune_expired() == 1


@pytest.mark.asyncio
async def test_contradiction_preserves_audit_trail(tmp_path: Path) -> None:
    memory = SemanticMemory(tmp_path / "memory.sqlite3")
    old = await memory.add("El endpoint usa el puerto 8000", confidence=0.8)
    replacement = await memory.contradict(
        old, "El endpoint usa el puerto 8001", source="README actualizado"
    )
    rows = await memory.search("endpoint usa el puerto", include_expired=True)
    indexed = {row["id"]: row for row in rows}
    assert indexed[old]["confidence"] == pytest.approx(0.55)
    assert indexed[replacement]["supersedes_id"] == old
    evidence = await memory.evidence(old)
    assert evidence[0]["relation"] == "contradicts"
    assert replacement in evidence[0]["note"]


@pytest.mark.asyncio
async def test_old_schema_is_migrated_additively(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE semantic_memories (
                id TEXT PRIMARY KEY, content TEXT NOT NULL,
                fact_type TEXT NOT NULL DEFAULT 'fact', source TEXT,
                confidence REAL NOT NULL DEFAULT 0.7, metadata TEXT,
                timestamp REAL NOT NULL, access_count INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO semantic_memories VALUES ('old','legacy fact','fact',NULL,0.7,NULL,1,0)"
        )
    memory = SemanticMemory(path)
    result = await memory.search("legacy", namespace="global")
    assert result[0]["id"] == "old"
    assert result[0]["content_hash"]
