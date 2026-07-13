"""Lilith Memory - Vector memory store with SQLite backend + semantic chunker + ontology graph."""

__version__ = "1.4.0"

from lilith_memory.chunker import (
    Chunk,
    ChunkStrategy,
    SemanticChunker,
    chunk_text,
)
from lilith_memory.consolidation import consolidate_session
from lilith_memory.read_guard import guard
from lilith_memory.store import MemoryStore
from lilith_memory.vector_recall import (
    HashEmbedder,
    RecallHit,
    VectorRecall,
    chunk_and_recall,
)
from lilith_memory.ontology_graph import (
    Entity,
    EntityType,
    GraphPath,
    OntologyGraph,
    Relation,
    RelationType,
    SubGraph,
)


__all__ = [
    "Chunk",
    "ChunkStrategy",
    "Entity",
    "EntityType",
    "GraphPath",
    "HashEmbedder",
    "MemoryStore",
    "OntologyGraph",
    "RecallHit",
    "Relation",
    "RelationType",
    "SemanticChunker",
    "SubGraph",
    "VectorRecall",
    "chunk_and_recall",
    "chunk_text",
    "consolidate_session",
    "guard",
]
