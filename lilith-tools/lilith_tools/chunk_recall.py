"""RAG (Retrieval-Augmented Generation) tools.

Tools provided:
- chunk_recall: chunk a query, embed both query and stored chunks,
  return top-k cosine matches from a persistent vector index.
- chunk_ingest: ingest a text (or a file) into the vector store for
  later recall.
- chunk_store_stats: count vectors / sources / avg chars in the store.

These are thin tool wrappers over :mod:`lilith_memory.vector_recall` and
:mod:`lilith_memory.chunker`. They are stateless apart from the SQLite
file they point at, so the same tool can be shared across agents.

The tools are designed to be called by any agent that needs to:
  * Recall prior knowledge ("What did we decide about X?")
  * Ground a response in long documents (RAG)
  * Index new content for later retrieval

Default DB path: ``<cwd>/.ygg/rag.db`` so each project has its own
store by default. Override with the ``db_path`` parameter for
shared/global stores.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


# Thread-local store cache so multiple tools share the same VectorRecall
# instance per db_path (avoids reopening SQLite on every call).
_STORE_CACHE: dict[str, Any] = {}
_STORE_LOCK = threading.Lock()


def _default_db_path() -> str:
    """Return the default RAG DB path: ``<cwd>/.ygg/rag.db``."""
    cwd = Path(os.getcwd())
    ygg = cwd / ".ygg"
    ygg.mkdir(parents=True, exist_ok=True)
    return str(ygg / "rag.db")


def _get_recall(db_path: str):
    """Get (or create) a cached :class:`VectorRecall` for ``db_path``."""
    from lilith_memory.vector_recall import HashEmbedder, VectorRecall
    with _STORE_LOCK:
        if db_path in _STORE_CACHE:
            return _STORE_CACHE[db_path]
        # Standard embedder + chunker; default chunker matches lilith-memory
        recall = VectorRecall(
            db_path,
            embedder=HashEmbedder(dim=1024),
        )
        _STORE_CACHE[db_path] = recall
        return recall


def _reset_cache() -> None:
    """Clear the tool-level recall cache. Used by tests."""
    with _STORE_LOCK:
        _STORE_CACHE.clear()


# ── Tool: chunk_recall ──────────────────────────────────────────────────


@ToolRegistry.register
class ChunkRecallTool(BaseTool):
    """Top-k semantic recall from the RAG vector store."""

    name = "chunk_recall"
    description = (
        "Busca los chunks más relevantes a una query en el vector store "
        "de RAG. Devuelve top-k hits ordenados por cosine similarity."
    )
    parameters = {
        "query": {"type": "string", "required": True},
        "top_k": {"type": "integer", "required": False, "default": 5},
        "source_id": {"type": "string", "required": False},
        "min_score": {"type": "number", "required": False, "default": 0.0},
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                success=False,
                data=None,
                error="query is required and must be a non-empty string",
            )
        # Coerce top_k with care: 0 must NOT be replaced by the default.
        raw_k = kwargs.get("top_k")
        if raw_k is None:
            top_k = 5
        else:
            try:
                top_k = int(raw_k)
            except (TypeError, ValueError):
                return ToolResult(success=False, data=None, error="top_k must be an integer")
        if top_k < 1:
            return ToolResult(success=False, data=None, error="top_k must be >= 1")
        source_id = kwargs.get("source_id") or None
        min_score = float(kwargs.get("min_score") or 0.0)
        db_path = kwargs.get("db_path") or _default_db_path()

        try:
            recall = _get_recall(db_path)
            hits = recall.search(
                query,
                top_k=top_k,
                source_id=source_id,
                min_score=min_score,
            )
        except Exception as e:  # pragma: no cover — defensive
            return ToolResult(success=False, data=None, error=str(e))

        return ToolResult(
            success=True,
            data={
                "query": query,
                "top_k": top_k,
                "hits": [h.to_dict() for h in hits],
                "count": len(hits),
                "db_path": db_path,
            },
            error="",
        )


# ── Tool: chunk_ingest ──────────────────────────────────────────────────


@ToolRegistry.register
class ChunkIngestTool(BaseTool):
    """Ingest a text into the RAG vector store."""

    name = "chunk_ingest"
    description = (
        "Indexa un texto en el vector store de RAG: lo divide en chunks, "
        "los embebe y los guarda con un source_id. Devuelve los ids."
    )
    parameters = {
        "text": {"type": "string", "required": False},
        "file_path": {"type": "string", "required": False},
        "source_id": {"type": "string", "required": False, "default": "default"},
        "strategy": {"type": "string", "required": False},
        "metadata": {"type": "object", "required": False},
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text")
        file_path = kwargs.get("file_path")
        if not text and not file_path:
            return ToolResult(
                success=False,
                data=None,
                error="either 'text' or 'file_path' is required",
            )
        if file_path and not text:
            try:
                p = Path(file_path)
                if not p.is_file():
                    return ToolResult(
                        success=False,
                        data=None,
                        error=f"file not found: {file_path}",
                    )
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return ToolResult(success=False, data=None, error=f"read failed: {e}")

        source_id = kwargs.get("source_id") or "default"
        strategy = kwargs.get("strategy") or None
        metadata = kwargs.get("metadata") or None
        db_path = kwargs.get("db_path") or _default_db_path()

        try:
            recall = _get_recall(db_path)
            vids = recall.add_text(
                text,
                source_id=source_id,
                metadata=metadata if isinstance(metadata, dict) else None,
                strategy=strategy,
            )
        except Exception as e:  # pragma: no cover — defensive
            return ToolResult(success=False, data=None, error=str(e))

        return ToolResult(
            success=True,
            data={
                "source_id": source_id,
                "vector_ids": vids,
                "chunk_count": len(vids),
                "chars": len(text),
                "db_path": db_path,
            },
            error="",
        )


# ── Tool: chunk_store_stats ─────────────────────────────────────────────


@ToolRegistry.register
class ChunkStoreStatsTool(BaseTool):
    """Return stats for the RAG vector store."""

    name = "chunk_store_stats"
    description = (
        "Devuelve estadísticas del vector store de RAG: número de "
        "vectores, sources, chars promedio, dim del embedder."
    )
    parameters = {
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        db_path = kwargs.get("db_path") or _default_db_path()
        try:
            recall = _get_recall(db_path)
            stats = recall.stats()
            sources = recall.list_sources()
        except Exception as e:  # pragma: no cover — defensive
            return ToolResult(success=False, data=None, error=str(e))
        return ToolResult(
            success=True,
            data={"db_path": db_path, "stats": stats, "sources": sources},
            error="",
        )
