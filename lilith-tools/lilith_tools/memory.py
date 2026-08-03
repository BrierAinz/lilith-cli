"""Persistent operator-memory tools for the main Lilith session."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

_STORE_CACHE: dict[str, Any] = {}
_VECTOR_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def _default_db_path() -> str:
    root = Path(os.environ.get("YGGDRASIL_HOME", Path.home() / ".yggdrasil"))
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "memory.db")


def _get_stores(db_path: str):
    from lilith_memory import HashEmbedder, MemoryStore, VectorRecall

    key = str(Path(db_path))
    with _CACHE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = MemoryStore(key)
        if key not in _VECTOR_CACHE:
            _VECTOR_CACHE[key] = VectorRecall(key, embedder=HashEmbedder(dim=1024))
        return _STORE_CACHE[key], _VECTOR_CACHE[key]


def _reset_cache() -> None:
    with _CACHE_LOCK:
        _STORE_CACHE.clear()
        _VECTOR_CACHE.clear()


def _tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [tag.strip() for tag in raw.split(",") if tag.strip()]
    if isinstance(raw, (list, tuple, set)):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    raise ValueError("tags must be a list or comma-separated string")


@ToolRegistry.register
class MemorySaveTool(BaseTool):
    name = "memory_save"
    description = "Guarda una nota persistente del operador con timestamp y tags."
    parameters = {
        "text": {"type": "string", "required": True},
        "tags": {"type": "array", "items": {"type": "string"}, "required": False},
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text")
        if not isinstance(text, str) or not text.strip():
            return ToolResult(False, None, "text is required and must be non-empty")
        try:
            tags = _tags(kwargs.get("tags"))
            db_path = str(kwargs.get("db_path") or _default_db_path())
            store, recall = _get_stores(db_path)
            timestamp = datetime.now(timezone.utc).isoformat()
            metadata = {"tags": tags, "timestamp": timestamp, "scope": "operator_note"}
            entry_id = store.store("main", "operator", text.strip(), metadata)
            recall.add_text(
                text.strip(),
                source_id=f"memory:{entry_id}",
                metadata={**metadata, "memory_id": entry_id},
            )
        except Exception as exc:
            return ToolResult(False, None, str(exc))
        return ToolResult(
            True,
            {"id": entry_id, "text": text.strip(), "tags": tags, "timestamp": timestamp, "db_path": db_path},
        )


@ToolRegistry.register
class MemoryRecallTool(BaseTool):
    name = "memory_recall"
    description = "Recupera los pasajes persistentes más relevantes para una consulta."
    parameters = {
        "query": {"type": "string", "required": True},
        "k": {"type": "integer", "required": False, "default": 5},
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(False, None, "query is required and must be non-empty")
        try:
            k = int(kwargs.get("k", 5))
        except (TypeError, ValueError):
            return ToolResult(False, None, "k must be an integer")
        if k < 1:
            return ToolResult(False, None, "k must be >= 1")

        db_path = str(kwargs.get("db_path") or _default_db_path())
        try:
            store, recall = _get_stores(db_path)
            hits = recall.search(query.strip(), top_k=k, scope="operator_note")
            passages = [
                {
                    "text": hit.chunk.text,
                    "score": hit.score,
                    "source_id": hit.source_id,
                    "tags": hit.chunk.metadata.get("tags", []),
                    "timestamp": hit.chunk.metadata.get("timestamp"),
                }
                for hit in hits
            ]
            if not passages:
                rows = store.search(query.strip(), limit=k, scope="operator_note")
                for row in rows:
                    metadata = json.loads(row.get("metadata") or "{}")
                    passages.append(
                        {
                            "text": row["content"],
                            "score": None,
                            "source_id": f"memory:{row['id']}",
                            "tags": metadata.get("tags", []),
                            "timestamp": metadata.get("timestamp") or row.get("created_at"),
                        }
                    )
        except Exception as exc:
            return ToolResult(False, None, str(exc))
        return ToolResult(
            True,
            {"query": query.strip(), "k": k, "passages": passages, "count": len(passages), "db_path": db_path},
        )
