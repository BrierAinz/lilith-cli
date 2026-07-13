"""Vector recall: chunker + hashed-embedding + SQLite-backed cosine search.

This module completes the RAG primitive stack in ``lilith-memory``:

    text -> :class:`SemanticChunker` -> chunks -> :class:`HashEmbedder` -> vectors
                                                                              |
                                                                              v
                                                                       SQLite store
                                                                              |
                                                                              v
                                                                  cosine top-k retrieval

The embedder is a pure-stdlib bag-of-hashed-tokens: each token (word or
char n-gram) is hashed to a fixed-dim slot, weighted by frequency. This is
a classic "hashing trick" (Weinberger et al., 2009) — fast, dependency-free,
and deterministic, so identical inputs always produce identical vectors.

We deliberately avoid numpy / faiss / sentence-transformers so the module
runs anywhere Python 3.11+ runs (Windows, Linux, macOS, WSL) with no
extra install. The trade-off: this is **lexical** similarity, not
**semantic** similarity. For "cat"/"kitten" matching you still need a real
embedding model — that's a future extension (``HashEmbedder`` will be
replaced by an ``EmbeddingBackend`` interface).

API::

    from lilith_memory.vector_recall import (
        HashEmbedder, VectorRecall, RecallHit,
    )
    from lilith_memory.chunker import SemanticChunker

    embedder = HashEmbedder(dim=1024)
    recall = VectorRecall("rag.db", embedder=embedder)
    chunker = SemanticChunker(target_size=512, overlap=64)

    recall.add_document("doc-1", long_text, chunker=chunker)
    hits = recall.search("What is Yggdrasil?", top_k=5)

    for hit in hits:
        print(hit.score, hit.chunk.text)

The module is designed to be:

* **Pure stdlib** — no numpy, no faiss. Just ``sqlite3`` and ``hashlib``.
* **Deterministic** — same input always produces same vector.
* **Composable** — uses the existing :class:`SemanticChunker` from
  ``lilith_memory.chunker``.
* **Auditable** — every entry is stored with its chunk id, source_id,
  text, and a JSON metadata blob. No silent data loss.
* **Incremental** — chunks are upserted by stable id (sha256 of
  text + index), so re-adding the same document is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from lilith_memory.chunker import Chunk, SemanticChunker, chunk_text
from lilith_memory.read_guard import ReadPolicy, guard


# ── Embedder ────────────────────────────────────────────────────────────


# Default tokenizer: lowercase, strip non-alphanumeric, split on whitespace.
# Keeps unicode word characters via ``\w`` (in Python 3 the ``re`` module
# is Unicode-aware by default).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Lowercase + tokenize text into word-level tokens.

    Strips punctuation, collapses case, splits on word boundaries. Empty
    tokens (numeric-only) are kept because they still carry discriminative
    information ("2025" vs "2024").
    """
    return _TOKEN_RE.findall(text.lower())


class HashEmbedder:
    """Bag-of-hashed-tokens embedder (the "hashing trick").

    Each token is mapped to one of ``dim`` slots by ``hashlib.sha256``; the
    slot value is incremented by the token count. To reduce hash-collision
    noise, the slot sign is taken from a second hash of the same token —
    tokens with the same slot can have opposite signs depending on token
    identity. The final vector is L2-normalized so cosine similarity
    collapses to a dot product.

    The result is a deterministic, fixed-dimension dense vector that
    captures the lexical content of the input. Similarity is purely
    lexical — it will not understand "cat"/"kitten" as related unless
    both words appear in training data.

    Args:
        dim: Vector dimensionality. Larger = less collision, more memory.
            1024 is a reasonable default; 2048 is safer for large corpora;
            256 is fast but noisy.
        ngram_min: Minimum token n-gram size (1 = unigrams only).
        ngram_max: Maximum token n-gram size (1 = unigrams only,
            2 = unigrams + bigrams, etc.).
        normalize: If True (default), L2-normalize the output so cosine
            similarity is equivalent to a dot product.
    """

    __slots__ = ("dim", "ngram_min", "ngram_max", "normalize")

    def __init__(
        self,
        dim: int = 1024,
        ngram_min: int = 1,
        ngram_max: int = 1,
        normalize: bool = True,
    ) -> None:
        if dim < 1:
            raise ValueError("dim must be >= 1")
        if ngram_min < 1 or ngram_max < ngram_min:
            raise ValueError(
                f"ngram range invalid: min={ngram_min}, max={ngram_max}"
            )
        self.dim = dim
        self.ngram_min = ngram_min
        self.ngram_max = ngram_max
        self.normalize = normalize

    # ── Public API ──────────────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        """Embed a single text into a fixed-dim dense vector."""
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self.dim

        # Build n-gram counts
        counts: Counter[str] = Counter()
        for n in range(self.ngram_min, self.ngram_max + 1):
            if n == 1:
                for tok in tokens:
                    counts[tok] += 1
            else:
                for i in range(len(tokens) - n + 1):
                    ng = " ".join(tokens[i : i + n])
                    counts[ng] += 1

        # Project into dim slots via the hashing trick
        vec = [0.0] * self.dim
        for token, count in counts.items():
            slot = self._slot(token)
            sign = self._sign(token)
            vec[slot] += sign * float(count)

        if self.normalize:
            self._l2_normalize(vec)
        return vec

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        """Embed multiple texts. Order is preserved."""
        return [self.embed(t) for t in texts]

    def similarity(self, a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors. Returns 0.0 for zero vectors."""
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    # ── Hashing helpers ─────────────────────────────────────────────────

    def _slot(self, token: str) -> int:
        """Hash a token to a slot in [0, dim)."""
        h = hashlib.sha256(f"slot::{token}".encode("utf-8")).digest()
        return int.from_bytes(h[:8], "little") % self.dim

    def _sign(self, token: str) -> int:
        """Hash a token to a sign in {-1, +1}."""
        h = hashlib.sha256(f"sign::{token}".encode("utf-8")).digest()
        return 1 if (h[0] & 1) else -1

    def _l2_normalize(self, vec: list[float]) -> None:
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            inv = 1.0 / norm
            for i in range(len(vec)):
                vec[i] *= inv

    # ── Diagnostic helpers (used by tests + introspection) ─────────────

    def token_slot(self, token: str) -> int:
        """Public alias for the hashing trick's slot function."""
        return self._slot(token)

    def token_sign(self, token: str) -> int:
        """Public alias for the hashing trick's sign function."""
        return self._sign(token)


# ── Recall result ───────────────────────────────────────────────────────


@dataclass
class RecallHit:
    """A single search hit returned by :meth:`VectorRecall.search`.

    Attributes:
        chunk: The matched :class:`lilith_memory.chunker.Chunk`.
        score: Cosine similarity in [-1.0, 1.0]. For normalized
            non-negative vectors, will be in [0.0, 1.0].
        vector_id: The SQLite rowid of the stored vector.
        source_id: The source document this chunk came from.
    """

    chunk: Chunk
    score: float
    vector_id: int
    source_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict(),
            "score": round(self.score, 6),
            "vector_id": self.vector_id,
            "source_id": self.source_id,
        }


# ── VectorRecall ────────────────────────────────────────────────────────


def _matches_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (list, tuple, set)):
        if isinstance(expected, (list, tuple, set)):
            return set(expected).issubset(set(actual))
        return expected in actual
    return actual == expected


def _matches_scope(hit: RecallHit, scope: str | dict[str, Any] | None) -> bool:
    if scope is None:
        return True
    metadata = hit.chunk.metadata or {}
    if isinstance(scope, str):
        candidates = (
            hit.source_id,
            metadata.get("scope"),
            metadata.get("namespace"),
            metadata.get("agent"),
        )
        tags = metadata.get("tags", metadata.get("tag"))
        return scope in candidates or _matches_value(tags, scope)
    values = {"source_id": hit.source_id, **metadata}
    return all(_matches_value(values.get(key), value) for key, value in scope.items())


class VectorRecall:
    """SQLite-backed vector store with cosine top-k retrieval.

    Persists chunk vectors and metadata in a single SQLite file. The
    schema is:

    * ``vectors``: id, source_id, chunk_id, text, vector_json, metadata,
      created_at.
    * ``vector_index``: optional Numpy-free acceleration table (just a
      B-tree on source_id and created_at for now).

    All operations are synchronous. For very large corpora, swap this
    for a real ANN index — the public API stays the same.

    Args:
        db_path: Path to the SQLite database file. Created if missing.
        embedder: A :class:`HashEmbedder` (or compatible object with an
            ``embed(text) -> list[float]`` method). Defaults to
            ``HashEmbedder(dim=1024)``.
        chunker: A :class:`SemanticChunker`. Defaults to
            ``SemanticChunker(target_size=512, overlap=64)`` — the same
            defaults as ``chunk_text()``.
    """

    def __init__(
        self,
        db_path: str | Path,
        embedder: HashEmbedder | None = None,
        chunker: SemanticChunker | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        # For in-memory DBs (":memory:"), we need a long-lived connection
        # because each sqlite3.connect(":memory:") creates a NEW database.
        # For file-based DBs, the standard connect-per-call pattern works fine.
        self._in_memory: bool = str(db_path) == ":memory:"
        if not self._in_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or HashEmbedder(dim=1024)
        self.chunker = chunker or SemanticChunker(target_size=512, overlap=64)
        if self._in_memory:
            self._conn = sqlite3.connect(":memory:")
        else:
            self._conn = None
        self._init_db()

    def _connect(self):
        """Return a connection. For :memory:, reuses the long-lived one."""
        if self._in_memory:
            return self._conn
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source ON vectors(source_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created ON vectors(created_at)
            """)
            conn.commit()

    # ── Write API ───────────────────────────────────────────────────────

    def add(
        self,
        chunk: Chunk,
        source_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Add a single chunk to the store. Returns the rowid.

        Re-adding a chunk with the same ``chunk.id`` updates the existing
        row in place (idempotent upsert).
        """
        vec = self.embedder.embed(chunk.text)
        merged_meta = dict(chunk.metadata or {})
        if metadata:
            merged_meta.update(metadata)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vectors (source_id, chunk_id, text, vector_json, metadata)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    text = excluded.text,
                    vector_json = excluded.vector_json,
                    metadata = excluded.metadata
                """,
                (
                    source_id,
                    chunk.id,
                    chunk.text,
                    json.dumps(vec),
                    json.dumps(merged_meta),
                ),
            )
            cur = conn.execute(
                "SELECT id FROM vectors WHERE chunk_id = ?", (chunk.id,)
            )
            rowid = cur.fetchone()[0]
            conn.commit()
            return rowid

    def add_text(
        self,
        text: str,
        source_id: str = "default",
        metadata: dict[str, Any] | None = None,
        strategy: str | None = None,
    ) -> list[int]:
        """Chunk a text and add all chunks. Returns the rowids in order.

        Args:
            text: Source text to chunk + embed.
            source_id: Logical source identifier (e.g. document id).
            metadata: Optional metadata applied to every chunk from this
                call. Per-chunk metadata (strategy, offsets) is preserved.
            strategy: Optional strategy override (``"auto"``, ``"markdown"``,
                ``"code"`` etc). Defaults to the chunker's default.
        """
        from lilith_memory.chunker import ChunkStrategy  # lazy, avoid cycle
        if strategy is not None:
            chunks = self.chunker.chunk(text, strategy=ChunkStrategy(strategy))
        else:
            chunks = self.chunker.chunk(text)
        return [self.add(c, source_id=source_id, metadata=metadata) for c in chunks]

    def add_document(
        self,
        source_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        strategy: str | None = None,
    ) -> list[int]:
        """Alias for :meth:`add_text` with a required ``source_id``."""
        return self.add_text(text, source_id=source_id, metadata=metadata, strategy=strategy)

    def delete_source(self, source_id: str) -> int:
        """Delete all vectors for a source. Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM vectors WHERE source_id = ?", (source_id,))
            conn.commit()
            return cur.rowcount

    def delete_chunk(self, chunk_id: str) -> bool:
        """Delete a single chunk by id. Returns True if deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM vectors WHERE chunk_id = ?", (chunk_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── Read API ────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        source_id: str | None = None,
        min_score: float = 0.0,
        requester: str | None = None,
        policy: ReadPolicy[RecallHit] | None = None,
        scope: str | dict[str, Any] | None = None,
    ) -> list[RecallHit]:
        """Top-k cosine similarity search.

        Args:
            query: Free-form query text.
            top_k: Maximum number of hits to return.
            source_id: Optional filter — only return hits from this source.
            min_score: Drop hits below this score. Default 0.0 (return
                anything with positive similarity).
            requester: Optional caller identity passed to the read guard.
            policy: Optional read policy applied before returning hits.
            scope: Optional source_id/metadata filter applied before the guard.

        Returns:
            A list of :class:`RecallHit` ordered by descending score.
        """
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        qvec = self.embedder.embed(query)
        if not any(qvec):
            return []  # all-zero query → no signal

        sql = "SELECT id, source_id, chunk_id, text, vector_json, metadata FROM vectors"
        params: tuple = ()
        if source_id is not None:
            sql += " WHERE source_id = ?"
            params = (source_id,)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        hits: list[RecallHit] = []
        for row in rows:
            vid, sid, cid, text, vec_json, meta_json = row
            try:
                vec = json.loads(vec_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if len(vec) != self.embedder.dim:
                continue  # dim mismatch — skip
            score = self.embedder.similarity(qvec, vec)
            if score < min_score:
                continue
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            chunk = Chunk(
                id=cid,
                text=text,
                index=meta.get("chunk_index", 0),
                start_offset=meta.get("start_offset", 0),
                end_offset=meta.get("end_offset", len(text)),
                strategy=meta.get("strategy", "unknown"),
                metadata=meta,
                token_estimate=meta.get("token_estimate", max(1, len(text) // 4)),
            )
            hits.append(RecallHit(chunk=chunk, score=score, vector_id=vid, source_id=sid))

        hits.sort(key=lambda h: h.score, reverse=True)
        scoped = [hit for hit in hits if _matches_scope(hit, scope)]
        return guard(scoped, requester=requester, policy=policy)[:top_k]

    def get(
        self,
        chunk_id: str,
        requester: str | None = None,
        policy: ReadPolicy[RecallHit] | None = None,
        scope: str | dict[str, Any] | None = None,
    ) -> RecallHit | None:
        """Fetch a single chunk by id (with its vector)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, source_id, chunk_id, text, vector_json, metadata FROM vectors WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        if not row:
            return None
        vid, sid, cid, text, vec_json, meta_json = row
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        # Reconstruct a Chunk from stored metadata
        chunk = Chunk(
            id=cid,
            text=text,
            index=meta.get("chunk_index", 0),
            start_offset=meta.get("start_offset", 0),
            end_offset=meta.get("end_offset", len(text)),
            strategy=meta.get("strategy", "unknown"),
            metadata=meta,
            token_estimate=meta.get("token_estimate", max(1, len(text) // 4)),
        )
        # Score is undefined for direct fetch — use 1.0 to indicate "exact match"
        hit = RecallHit(chunk=chunk, score=1.0, vector_id=vid, source_id=sid)
        if not _matches_scope(hit, scope):
            return None
        allowed = guard([hit], requester=requester, policy=policy)
        return allowed[0] if allowed else None

    def list_sources(self) -> list[dict[str, Any]]:
        """List all distinct source_ids with chunk counts."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_id, COUNT(*) FROM vectors GROUP BY source_id ORDER BY source_id"
            ).fetchall()
        return [{"source_id": sid, "count": n} for sid, n in rows]

    def count(self) -> int:
        """Total number of vectors stored."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def clear(self) -> None:
        """Remove all vectors. Schema is preserved."""
        with self._connect() as conn:
            conn.execute("DELETE FROM vectors")
            conn.commit()

    def stats(self) -> dict[str, Any]:
        """Return a stats dict: count, source count, avg chars, etc."""
        with self._connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            n_src = conn.execute(
                "SELECT COUNT(DISTINCT source_id) FROM vectors"
            ).fetchone()[0]
            if n:
                total_chars = conn.execute(
                    "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM vectors"
                ).fetchone()[0]
                avg_chars = total_chars / n
            else:
                avg_chars = 0.0
        return {
            "vectors": n,
            "sources": n_src,
            "avg_chars": round(avg_chars, 2),
            "embedder_dim": self.embedder.dim,
        }


# ── Convenience function ────────────────────────────────────────────────


def chunk_and_recall(
    db_path: str | Path,
    documents: dict[str, str],
    query: str,
    top_k: int = 5,
    embedder: HashEmbedder | None = None,
    chunker: SemanticChunker | None = None,
) -> list[RecallHit]:
    """One-shot helper: ingest ``documents`` then return top-k hits for ``query``.

    Args:
        db_path: Where to put the SQLite file.
        documents: Mapping of source_id -> text. Each value is chunked
            and indexed.
        query: The query to run.
        top_k: Hits to return.
        embedder: Optional embedder (default :class:`HashEmbedder`).
        chunker: Optional chunker (default :class:`SemanticChunker`).

    Returns:
        Top-k :class:`RecallHit` for ``query``.
    """
    recall = VectorRecall(db_path, embedder=embedder, chunker=chunker)
    for sid, text in documents.items():
        recall.add_document(sid, text)
    return recall.search(query, top_k=top_k)


__all__ = [
    "HashEmbedder",
    "RecallHit",
    "VectorRecall",
    "chunk_and_recall",
]
