"""Ontology Memory Graph — knowledge graph for structured memory.

Inspired by Symbio's ontology memory pattern. Stores entities (nodes) and
relations (edges) as a queryable graph structure layered on top of SQLite.

Features:
- Entity types: concept, person, tool, project, event, location, custom
- Relation types: is_a, has_a, uses, created, depends_on, related_to, custom
- Graph traversal: BFS/DFS neighbors, shortest path, subgraph extraction
- Confidence scoring on both entities and relations
- Integration with SemanticMemory (auto-extract entities from facts)
- Graph export for visualization (adjacency list, DOT format)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────

class EntityType(str, Enum):
    """Standard entity types in the ontology."""
    CONCEPT = "concept"
    PERSON = "person"
    TOOL = "tool"
    PROJECT = "project"
    EVENT = "event"
    LOCATION = "location"
    AGENT = "agent"
    SKILL = "skill"
    FILE = "file"
    CUSTOM = "custom"


class RelationType(str, Enum):
    """Standard relation types in the ontology."""
    IS_A = "is_a"
    HAS_A = "has_a"
    USES = "uses"
    CREATED = "created"
    DEPENDS_ON = "depends_on"
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    CONTAINS = "contains"
    PRECEDES = "precedes"
    CAUSES = "causes"
    CUSTOM = "custom"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class Entity:
    """A node in the ontology graph."""
    id: str
    name: str
    entity_type: str
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    created_at: float = 0.0
    access_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": self.properties,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "access_count": self.access_count,
            "metadata": self.metadata,
        }


@dataclass
class Relation:
    """An edge in the ontology graph."""
    id: str
    source_id: str
    target_id: str
    relation_type: str
    properties: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    confidence: float = 0.8
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "properties": self.properties,
            "weight": self.weight,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


@dataclass
class GraphPath:
    """A path through the graph."""
    entities: list[Entity]
    relations: list[Relation]
    total_weight: float = 0.0

    @property
    def length(self) -> int:
        """Number of edges (relations) in the path."""
        return len(self.relations)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "entities": [e.to_dict() for e in self.entities],
            "relations": [r.to_dict() for r in self.relations],
            "total_weight": self.total_weight,
            "length": self.length,
        }
        # Add source_id/target_id aliases for API consumers
        if self.entities:
            result["source_id"] = self.entities[0].id
            result["target_id"] = self.entities[-1].id
        return result


@dataclass
class SubGraph:
    """A subgraph extraction result."""
    entities: list[Entity]
    relations: list[Relation]
    center_id: str
    depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "relations": [r.to_dict() for r in self.relations],
            "center_id": self.center_id,
            "depth": self.depth,
            "entity_count": len(self.entities),
            "relation_count": len(self.relations),
        }


# ── Main graph class ────────────────────────────────────────────────────

class OntologyGraph:
    """SQLite-backed ontology memory graph.

    Stores entities (nodes) and relations (edges) with confidence scoring,
    graph traversal, and export capabilities.
    """

    def __init__(self, db_path: str | Any = ":memory:") -> None:
        from pathlib import Path
        self._db_path = str(db_path) if not isinstance(db_path, str) else db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    entity_type TEXT NOT NULL DEFAULT 'concept',
                    properties TEXT DEFAULT '{}',
                    confidence REAL NOT NULL DEFAULT 0.8,
                    created_at REAL NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT 'related_to',
                    properties TEXT DEFAULT '{}',
                    weight REAL NOT NULL DEFAULT 1.0,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (source_id) REFERENCES entities(id) ON DELETE CASCADE,
                    FOREIGN KEY (target_id) REFERENCES entities(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_type
                ON entities(entity_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_name
                ON entities(name COLLATE NOCASE)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_relation_source
                ON relations(source_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_relation_target
                ON relations(target_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_relation_type
                ON relations(relation_type)
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_relation_unique
                ON relations(source_id, target_id, relation_type)
            """)
            conn.commit()

    # ── Entity CRUD ──────────────────────────────────────────────────

    def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        properties: dict[str, Any] | None = None,
        confidence: float = 0.8,
        metadata: dict[str, Any] | None = None,
    ) -> Entity:
        """Add an entity to the graph. Returns existing if name+type match."""
        existing = self.find_entity(name, entity_type)
        if existing:
            return existing

        entity = Entity(
            id=str(uuid.uuid4()),
            name=name,
            entity_type=entity_type,
            properties=properties or {},
            confidence=confidence,
            created_at=time.time(),
            metadata=metadata or {},
        )

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO entities
                   (id, name, entity_type, properties, confidence, created_at, access_count, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    entity.id, entity.name, entity.entity_type,
                    json.dumps(entity.properties), entity.confidence,
                    entity.created_at, json.dumps(entity.metadata),
                ),
            )
            conn.commit()

        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        """Get entity by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE entities SET access_count = access_count + 1 WHERE id = ?",
                    (entity_id,),
                )
                conn.commit()
                # Re-fetch to get updated access_count
                row = conn.execute(
                    "SELECT * FROM entities WHERE id = ?", (entity_id,)
                ).fetchone()
                if row:
                    return self._row_to_entity(row)
        return None

    def find_entity(self, name: str, entity_type: str | None = None) -> Entity | None:
        """Find entity by name (case-insensitive) and optional type."""
        with self._get_conn() as conn:
            if entity_type:
                row = conn.execute(
                    "SELECT * FROM entities WHERE LOWER(name) = LOWER(?) AND entity_type = ?",
                    (name, entity_type),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM entities WHERE LOWER(name) = LOWER(?)",
                    (name,),
                ).fetchone()
            if row:
                return self._row_to_entity(row)
        return None

    def search_entities(
        self, query: str, entity_type: str | None = None, limit: int = 10
    ) -> list[Entity]:
        """Search entities by substring in name or properties."""
        with self._get_conn() as conn:
            pattern = f"%{query}%"
            if entity_type:
                rows = conn.execute(
                    """SELECT * FROM entities
                       WHERE (name LIKE ? OR properties LIKE ?) AND entity_type = ?
                       ORDER BY confidence DESC, access_count DESC LIMIT ?""",
                    (pattern, pattern, entity_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM entities
                       WHERE name LIKE ? OR properties LIKE ?
                       ORDER BY confidence DESC, access_count DESC LIMIT ?""",
                    (pattern, pattern, limit),
                ).fetchall()
            return [self._row_to_entity(r) for r in rows]

    def list_entities(
        self, entity_type: str | None = None, limit: int = 50
    ) -> list[Entity]:
        """List all entities, optionally filtered by type."""
        with self._get_conn() as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE entity_type = ? ORDER BY name LIMIT ?",
                    (entity_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entities ORDER BY name LIMIT ?", (limit,)
                ).fetchall()
            return [self._row_to_entity(r) for r in rows]

    def delete_entity(self, entity_id: str) -> bool:
        """Delete entity and all its relations. Returns True if found."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Relation CRUD ────────────────────────────────────────────────

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str = "related_to",
        properties: dict[str, Any] | None = None,
        weight: float = 1.0,
        confidence: float = 0.8,
    ) -> Relation | None:
        """Add a relation between two entities. Returns None if entities don't exist."""
        # Verify both entities exist
        src = self.get_entity(source_id)
        tgt = self.get_entity(target_id)
        if not src or not tgt:
            return None

        # Check for existing relation
        existing = self.find_relation(source_id, target_id, relation_type)
        if existing:
            return existing

        relation = Relation(
            id=str(uuid.uuid4()),
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            properties=properties or {},
            weight=weight,
            confidence=confidence,
            created_at=time.time(),
        )

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO relations
                   (id, source_id, target_id, relation_type, properties, weight, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    relation.id, relation.source_id, relation.target_id,
                    relation.relation_type, json.dumps(relation.properties),
                    relation.weight, relation.confidence, relation.created_at,
                ),
            )
            conn.commit()

        return relation

    def find_relation(
        self, source_id: str, target_id: str, relation_type: str
    ) -> Relation | None:
        """Find a specific relation by source, target, and type."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM relations
                   WHERE source_id = ? AND target_id = ? AND relation_type = ?""",
                (source_id, target_id, relation_type),
            ).fetchone()
            if row:
                return self._row_to_relation(row)
        return None

    def get_relations(
        self,
        entity_id: str,
        direction: str = "both",
        relation_type: str | None = None,
        limit: int = 50,
    ) -> list[Relation]:
        """Get all relations for an entity.

        Args:
            entity_id: The entity to query.
            direction: 'outgoing', 'incoming', or 'both'.
            relation_type: Optional filter by relation type.
            limit: Max results.
        """
        with self._get_conn() as conn:
            conditions = []
            params: list[Any] = []

            if direction == "outgoing":
                conditions.append("source_id = ?")
                params.append(entity_id)
            elif direction == "incoming":
                conditions.append("target_id = ?")
                params.append(entity_id)
            else:
                conditions.append("(source_id = ? OR target_id = ?)")
                params.extend([entity_id, entity_id])

            if relation_type:
                conditions.append("relation_type = ?")
                params.append(relation_type)

            where = " AND ".join(conditions)
            params.append(limit)

            rows = conn.execute(
                f"SELECT * FROM relations WHERE {where} ORDER BY confidence DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_relation(r) for r in rows]

    def delete_relation(self, relation_id: str) -> bool:
        """Delete a relation by ID."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Graph Traversal ──────────────────────────────────────────────

    def neighbors(
        self,
        entity_id: str,
        depth: int = 1,
        relation_type: str | None = None,
    ) -> SubGraph:
        """Get all neighbors within N hops of an entity (BFS).

        Args:
            entity_id: Center entity.
            depth: Max hops (default 1).
            relation_type: Optional filter by relation type.

        Returns:
            SubGraph with all reachable entities and relations.
        """
        visited_entities: dict[str, Entity] = {}
        visited_relations: dict[str, Relation] = {}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        seen: set[str] = {entity_id}

        # Add center entity
        center = self.get_entity(entity_id)
        if center:
            visited_entities[entity_id] = center

        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            rels = self.get_relations(current_id, direction="both",
                                       relation_type=relation_type, limit=100)
            for rel in rels:
                visited_relations[rel.id] = rel
                neighbor_id = rel.target_id if rel.source_id == current_id else rel.source_id
                if neighbor_id not in seen:
                    seen.add(neighbor_id)
                    neighbor = self.get_entity(neighbor_id)
                    if neighbor:
                        visited_entities[neighbor_id] = neighbor
                        queue.append((neighbor_id, current_depth + 1))

        return SubGraph(
            entities=list(visited_entities.values()),
            relations=list(visited_relations.values()),
            center_id=entity_id,
            depth=depth,
        )

    def shortest_path(
        self, source_id: str, target_id: str, max_depth: int = 6
    ) -> GraphPath | None:
        """Find shortest path between two entities (BFS).

        Args:
            source_id: Start entity.
            target_id: End entity.
            max_depth: Maximum search depth.

        Returns:
            GraphPath if found, None otherwise.
        """
        if source_id == target_id:
            entity = self.get_entity(source_id)
            if entity:
                return GraphPath(entities=[entity], relations=[], total_weight=0.0)
            return None

        # BFS with parent tracking
        parent: dict[str, tuple[str, Relation]] = {}
        queue: deque[tuple[str, int]] = deque([(source_id, 0)])
        seen: set[str] = {source_id}

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            rels = self.get_relations(current_id, direction="both", limit=100)
            for rel in rels:
                neighbor_id = rel.target_id if rel.source_id == current_id else rel.source_id
                if neighbor_id not in seen:
                    seen.add(neighbor_id)
                    parent[neighbor_id] = (current_id, rel)

                    if neighbor_id == target_id:
                        # Reconstruct path
                        return self._reconstruct_path(source_id, target_id, parent)

                    queue.append((neighbor_id, depth + 1))

        return None

    def _reconstruct_path(
        self, source_id: str, target_id: str, parent: dict[str, tuple[str, Relation]]
    ) -> GraphPath:
        """Reconstruct path from BFS parent map."""
        entities: list[Entity] = []
        relations: list[Relation] = []
        total_weight = 0.0

        current = target_id
        while current != source_id:
            prev_id, rel = parent[current]
            relations.append(rel)
            entity = self.get_entity(current)
            if entity:
                entities.append(entity)
            total_weight += rel.weight
            current = prev_id

        # Add source entity
        src_entity = self.get_entity(source_id)
        if src_entity:
            entities.append(src_entity)

        entities.reverse()
        relations.reverse()

        return GraphPath(
            entities=entities,
            relations=relations,
            total_weight=total_weight,
        )

    # ── Graph Statistics ─────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        with self._get_conn() as conn:
            entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            relation_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]

            # Entity type distribution
            type_rows = conn.execute(
                "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type"
            ).fetchall()
            entity_types = {r["entity_type"]: r["cnt"] for r in type_rows}

            # Relation type distribution
            rel_rows = conn.execute(
                "SELECT relation_type, COUNT(*) as cnt FROM relations GROUP BY relation_type"
            ).fetchall()
            relation_types = {r["relation_type"]: r["cnt"] for r in rel_rows}

            # Most connected entities
            top_rows = conn.execute("""
                SELECT e.name, e.entity_type,
                       (SELECT COUNT(*) FROM relations WHERE source_id = e.id OR target_id = e.id) as connections
                FROM entities e
                ORDER BY connections DESC
                LIMIT 5
            """).fetchall()
            most_connected = [
                {"name": r["name"], "type": r["entity_type"], "connections": r["connections"]}
                for r in top_rows
            ]

            return {
                "entity_count": entity_count,
                "relation_count": relation_count,
                "entity_types": entity_types,
                "relation_types": relation_types,
                "most_connected": most_connected,
            }

    # ── Export ───────────────────────────────────────────────────────

    def export_adjacency(self) -> dict[str, list[str]]:
        """Export as adjacency list (entity_name → [neighbor_names])."""
        adj: dict[str, list[str]] = {}
        entities = self.list_entities(limit=1000)
        entity_map = {e.id: e.name for e in entities}

        for entity in entities:
            rels = self.get_relations(entity.id, direction="outgoing", limit=100)
            adj[entity.name] = []
            for rel in rels:
                target_name = entity_map.get(rel.target_id, rel.target_id)
                adj[entity.name].append(f"{rel.relation_type}→{target_name}")

        return adj

    def export_dot(self) -> str:
        """Export graph in DOT format (Graphviz)."""
        lines = ["digraph ontology {", '  rankdir=LR;', '  node [shape=box, style=filled, fillcolor=lightblue];']

        entities = self.list_entities(limit=500)
        entity_map = {e.id: e for e in entities}

        for e in entities:
            label = f"{e.name}\\n({e.entity_type})"
            lines.append(f'  "{e.id}" [label="{label}"];')

        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM relations LIMIT 1000").fetchall()
            for r in rows:
                src_name = entity_map.get(r["source_id"], None)
                tgt_name = entity_map.get(r["target_id"], None)
                if src_name and tgt_name:
                    lines.append(
                        f'  "{r["source_id"]}" -> "{r["target_id"]}" '
                        f'[label="{r["relation_type"]}", weight={r["weight"]}];'
                    )

        lines.append("}")
        return "\n".join(lines)

    def export_json(self) -> dict[str, Any]:
        """Export full graph as JSON-serializable dict."""
        entities = self.list_entities(limit=1000)
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM relations LIMIT 1000").fetchall()
            relations = [self._row_to_relation(r) for r in rows]

        return {
            "entities": [e.to_dict() for e in entities],
            "relations": [r.to_dict() for r in relations],
            "stats": self.stats(),
        }

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        d = dict(row)
        try:
            d["properties"] = json.loads(d.get("properties") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["properties"] = {}
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        return Entity(
            id=d["id"],
            name=d["name"],
            entity_type=d["entity_type"],
            properties=d["properties"],
            confidence=d["confidence"],
            created_at=d["created_at"],
            access_count=d["access_count"],
            metadata=d["metadata"],
        )

    @staticmethod
    def _row_to_relation(row: sqlite3.Row) -> Relation:
        d = dict(row)
        try:
            d["properties"] = json.loads(d.get("properties") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["properties"] = {}
        return Relation(
            id=d["id"],
            source_id=d["source_id"],
            target_id=d["target_id"],
            relation_type=d["relation_type"],
            properties=d["properties"],
            weight=d["weight"],
            confidence=d["confidence"],
            created_at=d["created_at"],
        )
