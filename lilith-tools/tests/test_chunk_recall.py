"""Tests for :mod:`lilith_tools.chunk_recall`.

Covers:
    - Tool registration in the ToolRegistry
    - ChunkRecallTool: search, source filter, min_score, error paths
    - ChunkIngestTool: text + file ingest, strategy override
    - ChunkStoreStatsTool: stats output
    - End-to-end: ingest then recall
    - Cache reuse (same db_path returns same instance)
    - Default db_path behavior
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from lilith_tools.base import ToolResult
from lilith_tools.chunk_recall import (
    ChunkIngestTool,
    ChunkRecallTool,
    ChunkStoreStatsTool,
    _default_db_path,
    _get_recall,
    _reset_cache,
)
from lilith_tools.registry import ToolRegistry


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the tool's store cache before AND after every test."""
    _reset_cache()
    yield
    _reset_cache()


@pytest.fixture
def tmp_db_path(tmp_path):
    """Provide a unique DB path in a temp dir for each test."""
    p = tmp_path / "rag.db"
    yield str(p)
    # tmp_path is auto-cleaned


# ── Registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_all_three_tools_registered(self):
        names = ToolRegistry.list_tools()
        assert "chunk_recall" in names
        assert "chunk_ingest" in names
        assert "chunk_store_stats" in names

    def test_tool_classes_have_required_attrs(self):
        for cls in (ChunkRecallTool, ChunkIngestTool, ChunkStoreStatsTool):
            assert cls.name
            assert cls.description
            assert isinstance(cls.parameters, dict)


# ── Default DB path ─────────────────────────────────────────────────────


class TestDefaultDbPath:
    def test_default_creates_ygg_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = _default_db_path()
        assert os.path.basename(p) == "rag.db"
        assert os.path.basename(os.path.dirname(p)) == ".ygg"
        assert os.path.isdir(os.path.dirname(p))


# ── ChunkRecallTool ─────────────────────────────────────────────────────


class TestChunkRecallTool:
    def test_requires_query(self, tmp_db_path):
        t = ChunkRecallTool()
        r = t.execute(db_path=tmp_db_path)
        assert r.success is False
        assert "query" in r.error

    def test_empty_query_is_error(self, tmp_db_path):
        t = ChunkRecallTool()
        r = t.execute(query="   ", db_path=tmp_db_path)
        assert r.success is False

    def test_search_empty_store(self, tmp_db_path):
        t = ChunkRecallTool()
        r = t.execute(query="anything", db_path=tmp_db_path)
        assert r.success is True
        assert r.data["count"] == 0
        assert r.data["hits"] == []

    def test_recall_finds_ingested(self, tmp_db_path):
        # Ingest first
        ChunkIngestTool().execute(
            text="Yggdrasil is the world tree connecting nine realms",
            source_id="myth",
            db_path=tmp_db_path,
        )
        # Now recall
        t = ChunkRecallTool()
        r = t.execute(query="Yggdrasil nine realms", top_k=3, db_path=tmp_db_path)
        assert r.success is True
        assert r.data["count"] >= 1
        assert r.data["hits"][0]["source_id"] == "myth"

    def test_top_k(self, tmp_db_path):
        ingest = ChunkIngestTool()
        for i in range(10):
            ingest.execute(
                text=f"Document {i} about topic number {i}",
                source_id=f"d{i}",
                db_path=tmp_db_path,
            )
        t = ChunkRecallTool()
        r = t.execute(query="document", top_k=3, db_path=tmp_db_path)
        assert r.data["count"] == 3

    def test_source_filter(self, tmp_db_path):
        ingest = ChunkIngestTool()
        ingest.execute(text="Yggdrasil world tree", source_id="myth", db_path=tmp_db_path)
        ingest.execute(text="Python programming language", source_id="tech", db_path=tmp_db_path)
        t = ChunkRecallTool()
        r = t.execute(query="tree", top_k=5, source_id="myth", db_path=tmp_db_path)
        for hit in r.data["hits"]:
            assert hit["source_id"] == "myth"

    def test_min_score(self, tmp_db_path):
        ingest = ChunkIngestTool()
        ingest.execute(text="Yggdrasil world tree nine realms", source_id="a", db_path=tmp_db_path)
        ingest.execute(text="unrelated cooking recipe", source_id="b", db_path=tmp_db_path)
        t = ChunkRecallTool()
        r_loose = t.execute(query="Yggdrasil", top_k=5, min_score=0.0, db_path=tmp_db_path)
        r_strict = t.execute(query="Yggdrasil", top_k=5, min_score=0.5, db_path=tmp_db_path)
        assert r_strict.data["count"] <= r_loose.data["count"]

    def test_invalid_top_k(self, tmp_db_path):
        t = ChunkRecallTool()
        r = t.execute(query="hi", top_k=0, db_path=tmp_db_path)
        assert r.success is False

    def test_hit_structure(self, tmp_db_path):
        ChunkIngestTool().execute(
            text="Yggdrasil connects nine worlds in Norse mythology",
            source_id="myth",
            db_path=tmp_db_path,
        )
        t = ChunkRecallTool()
        r = t.execute(query="Yggdrasil", top_k=1, db_path=tmp_db_path)
        hit = r.data["hits"][0]
        for key in ("chunk", "score", "vector_id", "source_id"):
            assert key in hit
        assert -1.0 <= hit["score"] <= 1.0


# ── ChunkIngestTool ─────────────────────────────────────────────────────


class TestChunkIngestTool:
    def test_requires_text_or_file(self, tmp_db_path):
        t = ChunkIngestTool()
        r = t.execute(db_path=tmp_db_path)
        assert r.success is False
        assert "text" in r.error or "file_path" in r.error

    def test_ingest_text(self, tmp_db_path):
        t = ChunkIngestTool()
        r = t.execute(text="Hello world this is a test", source_id="d1", db_path=tmp_db_path)
        assert r.success is True
        assert r.data["chunk_count"] >= 1
        assert r.data["source_id"] == "d1"
        assert r.data["chars"] == len("Hello world this is a test")

    def test_ingest_file(self, tmp_db_path, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("File content for RAG ingestion test.", encoding="utf-8")
        t = ChunkIngestTool()
        r = t.execute(file_path=str(f), source_id="file_doc", db_path=tmp_db_path)
        assert r.success is True
        assert r.data["chunk_count"] >= 1

    def test_ingest_file_missing(self, tmp_db_path):
        t = ChunkIngestTool()
        r = t.execute(file_path="/nonexistent/path.txt", db_path=tmp_db_path)
        assert r.success is False
        assert "not found" in r.error

    def test_ingest_with_strategy(self, tmp_db_path):
        t = ChunkIngestTool()
        code = (
            "def foo():\n    return 1\n\n"
            "def bar():\n    return 2\n\n"
            "class Baz:\n    pass\n"
        )
        r = t.execute(text=code, source_id="py", strategy="code", db_path=tmp_db_path)
        assert r.success is True

    def test_ingest_invalid_metadata(self, tmp_db_path):
        # Non-dict metadata is ignored
        t = ChunkIngestTool()
        r = t.execute(text="hello", source_id="d", metadata="not a dict", db_path=tmp_db_path)
        assert r.success is True


# ── ChunkStoreStatsTool ─────────────────────────────────────────────────


class TestChunkStoreStatsTool:
    def test_empty_store(self, tmp_db_path):
        t = ChunkStoreStatsTool()
        r = t.execute(db_path=tmp_db_path)
        assert r.success is True
        assert r.data["stats"]["vectors"] == 0
        assert r.data["sources"] == []

    def test_after_ingest(self, tmp_db_path):
        ingest = ChunkIngestTool()
        ingest.execute(text="hello world", source_id="a", db_path=tmp_db_path)
        ingest.execute(text="another document", source_id="b", db_path=tmp_db_path)
        t = ChunkStoreStatsTool()
        r = t.execute(db_path=tmp_db_path)
        assert r.success is True
        assert r.data["stats"]["vectors"] >= 1
        sources = {s["source_id"] for s in r.data["sources"]}
        assert "a" in sources
        assert "b" in sources


# ── End-to-end + cache ──────────────────────────────────────────────────


class TestEndToEnd:
    def test_ingest_then_recall(self, tmp_db_path):
        # Ingest a few docs
        ingest = ChunkIngestTool()
        ingest.execute(
            text="Yggdrasil is the immense and sacred tree at the center of Norse cosmology. "
            "It connects the nine worlds including Asgard, Midgard, and Helheim.",
            source_id="norse",
            db_path=tmp_db_path,
        )
        ingest.execute(
            text="Python is a high-level programming language created by Guido van Rossum.",
            source_id="tech",
            db_path=tmp_db_path,
        )
        # Recall across topics
        recall = ChunkRecallTool()
        r1 = recall.execute(query="Tell me about Yggdrasil the world tree", top_k=3, db_path=tmp_db_path)
        assert r1.data["hits"][0]["source_id"] == "norse"
        r2 = recall.execute(query="What language did Guido create?", top_k=3, db_path=tmp_db_path)
        assert r2.data["hits"][0]["source_id"] == "tech"

    def test_cache_reuses_instance(self, tmp_db_path):
        a = _get_recall(tmp_db_path)
        b = _get_recall(tmp_db_path)
        assert a is b

    def test_different_paths_get_different_instances(self, tmp_path):
        p1 = str(tmp_path / "a.db")
        p2 = str(tmp_path / "b.db")
        a = _get_recall(p1)
        b = _get_recall(p2)
        assert a is not b
