"""Persistent operator-memory tools for the main Lilith session."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

_STORE_CACHE: dict[str, Any] = {}
_VECTOR_CACHE: dict[str, Any] = {}
_SEMANTIC_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def _default_db_path() -> str:
    root = Path(os.environ.get("YGGDRASIL_HOME", Path.home() / ".yggdrasil"))
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "memory.db")


def _get_stores(db_path: str):
    from lilith_memory import HashEmbedder, MemoryStore, VectorRecall
    from lilith_memory.layers import SemanticMemory

    key = str(Path(db_path))
    with _CACHE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = MemoryStore(key)
        if key not in _VECTOR_CACHE:
            _VECTOR_CACHE[key] = VectorRecall(key, embedder=HashEmbedder(dim=1024))
        if key not in _SEMANTIC_CACHE:
            _SEMANTIC_CACHE[key] = SemanticMemory(Path(key))
        return _STORE_CACHE[key], _VECTOR_CACHE[key], _SEMANTIC_CACHE[key]


def _await_sync(awaitable: Any) -> Any:
    """Resolve a memory coroutine from sync tools, even inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="lilith-memory") as pool:
        return pool.submit(asyncio.run, awaitable).result()


def _reset_cache() -> None:
    with _CACHE_LOCK:
        _STORE_CACHE.clear()
        _VECTOR_CACHE.clear()
        _SEMANTIC_CACHE.clear()


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
    description = (
        "Guarda memoria persistente; fact_type activa deduplicacion semantica, "
        "procedencia, confianza, namespace y TTL."
    )
    parameters = {
        "text": {"type": "string", "required": True},
        "tags": {"type": "array", "items": {"type": "string"}, "required": False},
        "fact_type": {"type": "string", "required": False},
        "namespace": {"type": "string", "required": False, "default": "operator"},
        "source": {"type": "string", "required": False, "default": "operator"},
        "confidence": {"type": "number", "required": False, "default": 0.8},
        "ttl_seconds": {"type": "number", "required": False},
        "provenance": {"type": "object", "required": False},
        "supersedes_id": {"type": "string", "required": False},
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text")
        if not isinstance(text, str) or not text.strip():
            return ToolResult(False, None, "text is required and must be non-empty")
        try:
            tags = _tags(kwargs.get("tags"))
            db_path = str(kwargs.get("db_path") or _default_db_path())
            store, recall, semantic = _get_stores(db_path)
            timestamp = datetime.now(timezone.utc).isoformat()
            namespace = str(kwargs.get("namespace") or "operator").strip() or "operator"
            source = str(kwargs.get("source") or "operator").strip() or "operator"
            confidence = max(0.0, min(1.0, float(kwargs.get("confidence", 0.8))))
            ttl_raw = kwargs.get("ttl_seconds")
            ttl_seconds = float(ttl_raw) if ttl_raw is not None else None
            if ttl_seconds is not None and ttl_seconds <= 0:
                raise ValueError("ttl_seconds must be > 0")
            expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
            provenance = dict(kwargs.get("provenance") or {})
            provenance.setdefault("source", source)
            provenance.setdefault("recorded_at", timestamp)
            fact_type = kwargs.get("fact_type")
            if fact_type and str(fact_type) not in {
                "preference", "fact", "procedure", "relationship", "identity"
            }:
                raise ValueError("invalid fact_type")
            metadata = {
                "tags": tags, "timestamp": timestamp, "scope": "operator_note",
                "namespace": namespace, "source": source, "confidence": confidence,
                "expires_at": expires_at, "provenance": provenance,
            }
            entry_id = store.store("main", "operator", text.strip(), metadata)
            recall.add_text(
                text.strip(),
                source_id=f"memory:{entry_id}",
                metadata={**metadata, "memory_id": entry_id},
            )
            semantic_id = None
            if fact_type:
                semantic_id = _await_sync(semantic.add(
                    text.strip(),
                    fact_type=str(fact_type),
                    source=source,
                    confidence=confidence,
                    metadata={"tags": tags, "operator_memory_id": entry_id},
                    namespace=namespace,
                    ttl_seconds=ttl_seconds,
                    provenance=provenance,
                    supersedes_id=kwargs.get("supersedes_id"),
                ))
        except Exception as exc:
            return ToolResult(False, None, str(exc))
        return ToolResult(
            True,
            {
                "id": entry_id, "semantic_id": semantic_id, "text": text.strip(),
                "tags": tags, "timestamp": timestamp, "namespace": namespace,
                "source": source, "confidence": confidence, "expires_at": expires_at,
                "db_path": db_path,
            },
        )


@ToolRegistry.register
class MemoryRecallTool(BaseTool):
    name = "memory_recall"
    description = "Recupera los pasajes persistentes más relevantes para una consulta."
    parameters = {
        "query": {"type": "string", "required": True},
        "k": {"type": "integer", "required": False, "default": 5},
        "namespace": {"type": "string", "required": False},
        "min_confidence": {"type": "number", "required": False, "default": 0.0},
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
            store, recall, semantic = _get_stores(db_path)
            namespace = kwargs.get("namespace")
            min_confidence = max(
                0.0, min(1.0, float(kwargs.get("min_confidence", 0.0)))
            )
            now = time.time()
            hits = recall.search(query.strip(), top_k=k, scope="operator_note")
            passages = []
            for hit in hits:
                meta = hit.chunk.metadata
                if namespace is not None and meta.get("namespace") != namespace:
                    continue
                if float(meta.get("confidence", 0.8)) < min_confidence:
                    continue
                if meta.get("expires_at") is not None and float(meta["expires_at"]) <= now:
                    continue
                passages.append({
                    "text": hit.chunk.text, "score": hit.score,
                    "source_id": hit.source_id, "tags": meta.get("tags", []),
                    "timestamp": meta.get("timestamp"), "namespace": meta.get("namespace"),
                    "source": meta.get("source"), "confidence": meta.get("confidence"),
                    "provenance": meta.get("provenance"),
                })
            if not passages:
                rows = store.search(query.strip(), limit=k, scope="operator_note")
                for row in rows:
                    metadata = json.loads(row.get("metadata") or "{}")
                    if namespace is not None and metadata.get("namespace") != namespace:
                        continue
                    if float(metadata.get("confidence", 0.8)) < min_confidence:
                        continue
                    expires_at = metadata.get("expires_at")
                    if expires_at is not None and float(expires_at) <= now:
                        continue
                    passages.append(
                        {
                            "text": row["content"],
                            "score": None,
                            "source_id": f"memory:{row['id']}",
                            "tags": metadata.get("tags", []),
                            "timestamp": metadata.get("timestamp") or row.get("created_at"),
                            "namespace": metadata.get("namespace"),
                            "source": metadata.get("source"),
                            "confidence": metadata.get("confidence"),
                            "provenance": metadata.get("provenance"),
                        }
                    )
            semantic_hits = _await_sync(semantic.search(
                query.strip(), limit=k, namespace=namespace,
                min_confidence=min_confidence,
            ))
            by_text = {" ".join(p["text"].casefold().split()): p for p in passages}
            for fact in semantic_hits:
                key = " ".join(str(fact["content"]).casefold().split())
                target = by_text.get(key)
                fact_meta = fact.get("metadata") or {}
                semantic_data = {
                    "semantic_id": fact["id"], "confidence": fact["confidence"],
                    "namespace": fact.get("namespace"), "source": fact.get("source"),
                    "provenance": fact_meta.get("provenance", []),
                }
                if target is not None:
                    target.update(semantic_data)
                elif len(passages) < k:
                    target = {
                        "text": fact["content"], "score": None,
                        "source_id": f"semantic:{fact['id']}",
                        "tags": fact_meta.get("tags", []),
                        "timestamp": fact.get("timestamp"), **semantic_data,
                    }
                    passages.append(target)
                    by_text[key] = target
        except Exception as exc:
            return ToolResult(False, None, str(exc))
        return ToolResult(
            True,
            {"query": query.strip(), "k": k, "passages": passages, "count": len(passages), "db_path": db_path},
        )


@ToolRegistry.register
class MemoryEvidenceTool(BaseTool):
    name = "memory_evidence"
    description = "Corrobora o contradice una memoria semantica con evidencia auditable."
    parameters = {
        "semantic_id": {"type": "string", "required": True},
        "supports": {"type": "boolean", "required": True},
        "source": {"type": "string", "required": False},
        "note": {"type": "string", "required": False},
        "weight": {"type": "number", "required": False, "default": 0.1},
        "db_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        semantic_id = str(kwargs.get("semantic_id") or "").strip()
        if not semantic_id:
            return ToolResult(False, None, "semantic_id is required")
        try:
            db_path = str(kwargs.get("db_path") or _default_db_path())
            _, _, semantic = _get_stores(db_path)
            recorded = _await_sync(semantic.record_evidence(
                semantic_id,
                supports=bool(kwargs.get("supports")),
                source=kwargs.get("source"),
                note=str(kwargs.get("note") or ""),
                weight=float(kwargs.get("weight", 0.1)),
            ))
            if not recorded:
                return ToolResult(False, None, "semantic memory not found")
            evidence = _await_sync(semantic.evidence(semantic_id))
        except Exception as exc:
            return ToolResult(False, None, str(exc))
        return ToolResult(True, {
            "semantic_id": semantic_id, "recorded": True,
            "evidence": evidence, "db_path": db_path,
        })
