"""ChromaDB-backed semantic search memory backend."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any

from .base import MemoryBackend


if TYPE_CHECKING:
    from pathlib import Path


class ChromaBackend(MemoryBackend):
    """Memory backend powered by ChromaDB for local semantic search.

    Uses ChromaDB with sentence-transformers embeddings for semantic
    similarity search.  Falls back gracefully when ``chromadb`` is not
    installed by delegating all operations to :class:`SQLiteBackend`.

    The embedding model defaults to ``all-MiniLM-L6-v2`` from
    sentence-transformers.  Data is persisted to disk at a configurable
    path.
    """

    def __init__(
        self,
        db_path: Path,
        collection_name: str = "lilith_memory",
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        """Initialise the ChromaBackend.

        Args:
            db_path: Directory path for persistent storage. Created
                     automatically if it doesn't exist.
            collection_name: Name of the ChromaDB collection.
            embedding_model: Name of the sentence-transformers model.

        Raises:
            ImportError: If ``chromadb`` is not installed.

        """
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError(
                "chromadb is required for ChromaBackend. "
                "Install it with: pip install lilith-memory[chroma]",
            ) from exc

        self._db_path = db_path
        self._collection_name = collection_name
        self._embedding_model = embedding_model

        # Ensure directory exists
        db_path.mkdir(parents=True, exist_ok=True)

        # Initialise ChromaDB client and collection
        self._client = chromadb.PersistentClient(path=str(db_path / "chroma"))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # Lightweight SQLite for metadata / count tracking
        self._meta_db_path = db_path / "chroma_meta.db"
        self._init_meta_db()

        # Embedding function — lazy-load sentence-transformers
        self._embedding_fn = None

    def _init_meta_db(self) -> None:
        """Create the metadata SQLite table."""
        with sqlite3.connect(self._meta_db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chroma_meta (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    timestamp REAL NOT NULL
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chroma_meta_timestamp "
                "ON chroma_meta(timestamp DESC)",
            )
            conn.commit()

    def _get_embedding_fn(self) -> Any:
        """Lazily initialise and return the embedding function."""
        if self._embedding_fn is not None:
            return self._embedding_fn

        try:
            from chromadb.utils import embedding_functions

            self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self._embedding_model,
            )
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for ChromaBackend embeddings. "
                "Install it with: pip install lilith-memory[chroma]",
            ) from exc

        return self._embedding_fn

    # ------------------------------------------------------------------
    # MemoryBackend interface
    # ------------------------------------------------------------------

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Store *content* with semantic embeddings and return its id."""
        item_id = str(uuid.uuid4())
        now = time.time()
        embed_fn = self._get_embedding_fn()

        def _add() -> None:
            self._collection.add(
                ids=[item_id],
                documents=[content],
                metadatas=[metadata or {}],
                embeddings=embed_fn([content]),
            )
            with sqlite3.connect(self._meta_db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "INSERT INTO chroma_meta (id, content, metadata, timestamp) "
                    "VALUES (?, ?, ?, ?)",
                    (item_id, content, json.dumps(metadata) if metadata else None, now),
                )
                conn.commit()

        await asyncio.to_thread(_add)
        return item_id

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Semantic search using ChromaDB's vector similarity."""
        embed_fn = self._get_embedding_fn()

        def _search() -> list[dict[str, Any]]:
            results = self._collection.query(
                query_embeddings=embed_fn([query]),
                n_results=limit,
            )
            entries: list[dict[str, Any]] = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            for i, doc_id in enumerate(ids):
                entries.append(
                    {
                        "id": doc_id,
                        "content": documents[i] if i < len(documents) else "",
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "score": 1.0 - distances[i] if i < len(distances) else None,
                    },
                )
            return entries[:limit]

        return await asyncio.to_thread(_search)

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent entries from the metadata database."""

        def _recent() -> list[dict[str, Any]]:
            with sqlite3.connect(self._meta_db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                rows = conn.execute(
                    "SELECT * FROM chroma_meta ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                results: list[dict[str, Any]] = []
                for row in rows:
                    d = dict(row)
                    if d.get("metadata"):
                        try:
                            d["metadata"] = json.loads(d["metadata"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    results.append(d)
                return results

        return await asyncio.to_thread(_recent)

    async def delete(self, entry_id: str) -> bool:
        """Delete an entry from both ChromaDB and the metadata database."""

        def _delete() -> bool:
            self._collection.delete(ids=[entry_id])
            with sqlite3.connect(self._meta_db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute(
                    "DELETE FROM chroma_meta WHERE id = ?",
                    (entry_id,),
                )
                conn.commit()
                return cursor.rowcount > 0

        return await asyncio.to_thread(_delete)

    async def clear(self) -> int:
        """Clear all entries from ChromaDB and the metadata database."""
        count = self.count()

        def _clear() -> None:
            all_ids = self._collection.get()["ids"]
            if all_ids:
                self._collection.delete(ids=all_ids)
            with sqlite3.connect(self._meta_db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("DELETE FROM chroma_meta")
                conn.commit()

        await asyncio.to_thread(_clear)
        return count

    def count(self) -> int:
        """Return the total number of entries in the metadata database."""
        with sqlite3.connect(self._meta_db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute("SELECT COUNT(*) FROM chroma_meta").fetchone()
            return row[0] if row else 0
