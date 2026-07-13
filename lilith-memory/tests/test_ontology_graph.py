"""Tests for OntologyGraph — knowledge graph memory layer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lilith_memory.ontology_graph import (
    Entity,
    EntityType,
    GraphPath,
    OntologyGraph,
    Relation,
    RelationType,
    SubGraph,
)


@pytest.fixture
def graph(tmp_path: Path) -> OntologyGraph:
    """Create a fresh in-memory graph for each test."""
    return OntologyGraph(db_path=str(tmp_path / "test_ontology.db"))


@pytest.fixture
def populated_graph(graph: OntologyGraph) -> OntologyGraph:
    """Graph pre-populated with a small knowledge base."""
    # Entities
    odin = graph.add_entity("Odin", "agent", {"role": "orchestrator"})
    mimir = graph.add_entity("Mimir", "agent", {"role": "researcher"})
    heimdall = graph.add_entity("Heimdall", "agent", {"role": "auditor"})
    lilith_core = graph.add_entity("lilith-core", "project", {"version": "2.4.0"})
    lilith_memory = graph.add_entity("lilith-memory", "project", {"version": "1.1.0"})
    python = graph.add_entity("Python", "concept", {"version": "3.11"})
    sqlite = graph.add_entity("SQLite", "tool", {"purpose": "storage"})
    yggdrasil = graph.add_entity("Yggdrasil", "project", {"realms": 9})

    # Relations
    graph.add_relation(odin.id, yggdrasil.id, "part_of")
    graph.add_relation(mimir.id, yggdrasil.id, "part_of")
    graph.add_relation(heimdall.id, yggdrasil.id, "part_of")
    graph.add_relation(lilith_core.id, yggdrasil.id, "part_of")
    graph.add_relation(lilith_memory.id, lilith_core.id, "part_of")
    graph.add_relation(lilith_memory.id, sqlite.id, "uses")
    graph.add_relation(lilith_memory.id, python.id, "uses")
    graph.add_relation(odin.id, mimir.id, "uses")
    graph.add_relation(odin.id, heimdall.id, "uses")
    graph.add_relation(heimdall.id, odin.id, "related_to")

    return graph


# ── Entity CRUD ──────────────────────────────────────────────────────


class TestEntityCRUD:
    def test_add_entity(self, graph: OntologyGraph):
        entity = graph.add_entity("TestAgent", "agent", {"key": "value"})
        assert entity.name == "TestAgent"
        assert entity.entity_type == "agent"
        assert entity.properties == {"key": "value"}
        assert entity.confidence == 0.8
        assert entity.id

    def test_add_entity_default_type(self, graph: OntologyGraph):
        entity = graph.add_entity("Concept")
        assert entity.entity_type == "concept"

    def test_add_entity_dedup(self, graph: OntologyGraph):
        e1 = graph.add_entity("Test", "agent")
        e2 = graph.add_entity("Test", "agent")
        assert e1.id == e2.id

    def test_add_entity_different_types_no_dedup(self, graph: OntologyGraph):
        e1 = graph.add_entity("Test", "agent")
        e2 = graph.add_entity("Test", "concept")
        assert e1.id != e2.id

    def test_get_entity(self, graph: OntologyGraph):
        added = graph.add_entity("Test", "agent")
        fetched = graph.get_entity(added.id)
        assert fetched is not None
        assert fetched.name == "Test"
        assert fetched.access_count == 1  # get_entity increments

    def test_get_entity_not_found(self, graph: OntologyGraph):
        assert graph.get_entity("nonexistent") is None

    def test_find_entity(self, graph: OntologyGraph):
        graph.add_entity("Odin", "agent")
        found = graph.find_entity("odin")  # case-insensitive
        assert found is not None
        assert found.name == "Odin"

    def test_find_entity_with_type(self, graph: OntologyGraph):
        graph.add_entity("Test", "agent")
        graph.add_entity("Test", "concept")
        found = graph.find_entity("Test", "agent")
        assert found is not None
        assert found.entity_type == "agent"

    def test_find_entity_not_found(self, graph: OntologyGraph):
        assert graph.find_entity("Nonexistent") is None

    def test_search_entities(self, graph: OntologyGraph):
        graph.add_entity("Odin", "agent")
        graph.add_entity("Odin Memory", "project")
        graph.add_entity("Mimir", "agent")
        results = graph.search_entities("odin")
        assert len(results) == 2

    def test_search_entities_by_type(self, graph: OntologyGraph):
        graph.add_entity("Odin", "agent")
        graph.add_entity("Odin Config", "file")
        results = graph.search_entities("odin", entity_type="agent")
        assert len(results) == 1
        assert results[0].entity_type == "agent"

    def test_list_entities(self, graph: OntologyGraph):
        graph.add_entity("A", "agent")
        graph.add_entity("B", "concept")
        graph.add_entity("C", "agent")
        all_entities = graph.list_entities()
        assert len(all_entities) == 3
        agents = graph.list_entities(entity_type="agent")
        assert len(agents) == 2

    def test_delete_entity(self, graph: OntologyGraph):
        entity = graph.add_entity("Test", "agent")
        assert graph.delete_entity(entity.id) is True
        assert graph.get_entity(entity.id) is None

    def test_delete_entity_cascades_relations(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "agent")
        graph.add_relation(e1.id, e2.id, "uses")
        graph.delete_entity(e1.id)
        rels = graph.get_relations(e2.id)
        assert len(rels) == 0

    def test_delete_entity_not_found(self, graph: OntologyGraph):
        assert graph.delete_entity("nonexistent") is False


# ── Relation CRUD ────────────────────────────────────────────────────


class TestRelationCRUD:
    def test_add_relation(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        rel = graph.add_relation(e1.id, e2.id, "uses", weight=2.0)
        assert rel is not None
        assert rel.source_id == e1.id
        assert rel.target_id == e2.id
        assert rel.relation_type == "uses"
        assert rel.weight == 2.0

    def test_add_relation_dedup(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        r1 = graph.add_relation(e1.id, e2.id, "uses")
        r2 = graph.add_relation(e1.id, e2.id, "uses")
        assert r1.id == r2.id

    def test_add_relation_nonexistent_entity(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        rel = graph.add_relation(e1.id, "nonexistent", "uses")
        assert rel is None

    def test_add_relation_same_type_different_target(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        e3 = graph.add_entity("C", "tool")
        r1 = graph.add_relation(e1.id, e2.id, "uses")
        r2 = graph.add_relation(e1.id, e3.id, "uses")
        assert r1.id != r2.id

    def test_find_relation(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        added = graph.add_relation(e1.id, e2.id, "uses")
        found = graph.find_relation(e1.id, e2.id, "uses")
        assert found is not None
        assert found.id == added.id

    def test_find_relation_not_found(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        assert graph.find_relation(e1.id, e2.id, "uses") is None

    def test_get_relations_outgoing(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        rels = populated_graph.get_relations(odin.id, direction="outgoing")
        assert len(rels) >= 3  # part_of Yggdrasil, uses Mimir, uses Heimdall

    def test_get_relations_incoming(self, populated_graph: OntologyGraph):
        ygg = populated_graph.find_entity("Yggdrasil")
        assert ygg is not None
        rels = populated_graph.get_relations(ygg.id, direction="incoming")
        assert len(rels) >= 4  # Odin, Mimir, Heimdall, lilith-core part_of

    def test_get_relations_both(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        rels = populated_graph.get_relations(odin.id, direction="both")
        assert len(rels) >= 4  # outgoing + incoming (heimdall→odin)

    def test_get_relations_by_type(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        uses_rels = populated_graph.get_relations(odin.id, direction="outgoing",
                                                   relation_type="uses")
        assert all(r.relation_type == "uses" for r in uses_rels)

    def test_delete_relation(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        rel = graph.add_relation(e1.id, e2.id, "uses")
        assert graph.delete_relation(rel.id) is True
        assert graph.find_relation(e1.id, e2.id, "uses") is None

    def test_delete_relation_not_found(self, graph: OntologyGraph):
        assert graph.delete_relation("nonexistent") is False


# ── Graph Traversal ─────────────────────────────────────────────────


class TestGraphTraversal:
    def test_neighbors_depth_1(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        sub = populated_graph.neighbors(odin.id, depth=1)
        assert sub.center_id == odin.id
        # Odin connects to: Yggdrasil, Mimir, Heimdall (outgoing) + Heimdall (incoming)
        assert len(sub.entities) >= 3
        assert len(sub.relations) >= 3

    def test_neighbors_depth_2(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        sub1 = populated_graph.neighbors(odin.id, depth=1)
        sub2 = populated_graph.neighbors(odin.id, depth=2)
        # Depth 2 should include more entities
        assert len(sub2.entities) >= len(sub1.entities)

    def test_neighbors_with_relation_filter(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        sub = populated_graph.neighbors(odin.id, depth=1, relation_type="uses")
        # Only 'uses' relations
        entity_names = {e.name for e in sub.entities}
        assert "Mimir" in entity_names
        assert "Heimdall" in entity_names

    def test_neighbors_nonexistent(self, graph: OntologyGraph):
        sub = graph.neighbors("nonexistent", depth=1)
        assert len(sub.entities) == 0

    def test_shortest_path_direct(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        mimir = populated_graph.find_entity("Mimir")
        assert odin and mimir
        path = populated_graph.shortest_path(odin.id, mimir.id)
        assert path is not None
        assert path.length == 1
        assert len(path.entities) == 2

    def test_shortest_path_indirect(self, populated_graph: OntologyGraph):
        mimir = populated_graph.find_entity("Mimir")
        ygg = populated_graph.find_entity("Yggdrasil")
        assert mimir and ygg
        path = populated_graph.shortest_path(mimir.id, ygg.id)
        assert path is not None
        assert path.length >= 1

    def test_shortest_path_same_node(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        path = populated_graph.shortest_path(odin.id, odin.id)
        assert path is not None
        assert path.length == 0
        assert len(path.entities) == 1

    def test_shortest_path_no_path(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "agent")
        # No relation between them
        path = graph.shortest_path(e1.id, e2.id)
        assert path is None

    def test_shortest_path_nonexistent(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        path = populated_graph.shortest_path(odin.id, "nonexistent")
        assert path is None


# ── Data Classes ─────────────────────────────────────────────────────


class TestDataClasses:
    def test_entity_to_dict(self, graph: OntologyGraph):
        entity = graph.add_entity("Test", "agent", {"key": "val"})
        d = entity.to_dict()
        assert d["name"] == "Test"
        assert d["entity_type"] == "agent"
        assert d["properties"] == {"key": "val"}

    def test_relation_to_dict(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        rel = graph.add_relation(e1.id, e2.id, "uses", weight=2.5)
        assert rel is not None
        d = rel.to_dict()
        assert d["relation_type"] == "uses"
        assert d["weight"] == 2.5

    def test_graph_path_to_dict(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        mimir = populated_graph.find_entity("Mimir")
        assert odin and mimir
        path = populated_graph.shortest_path(odin.id, mimir.id)
        assert path is not None
        d = path.to_dict()
        assert "entities" in d
        assert "relations" in d
        assert d["length"] == 1

    def test_subgraph_to_dict(self, populated_graph: OntologyGraph):
        odin = populated_graph.find_entity("Odin")
        assert odin is not None
        sub = populated_graph.neighbors(odin.id, depth=1)
        d = sub.to_dict()
        assert "entities" in d
        assert "entity_count" in d
        assert d["center_id"] == odin.id

    def test_entity_type_enum(self):
        assert EntityType.AGENT.value == "agent"
        assert EntityType.CONCEPT.value == "concept"

    def test_relation_type_enum(self):
        assert RelationType.IS_A.value == "is_a"
        assert RelationType.USES.value == "uses"


# ── Stats & Export ───────────────────────────────────────────────────


class TestStatsAndExport:
    def test_stats_empty(self, graph: OntologyGraph):
        stats = graph.stats()
        assert stats["entity_count"] == 0
        assert stats["relation_count"] == 0

    def test_stats_populated(self, populated_graph: OntologyGraph):
        stats = populated_graph.stats()
        assert stats["entity_count"] == 8
        assert stats["relation_count"] == 10
        assert "agent" in stats["entity_types"]
        assert "uses" in stats["relation_types"]
        assert len(stats["most_connected"]) > 0

    def test_export_adjacency(self, populated_graph: OntologyGraph):
        adj = populated_graph.export_adjacency()
        assert isinstance(adj, dict)
        assert "Odin" in adj
        assert len(adj["Odin"]) >= 3

    def test_export_dot(self, populated_graph: OntologyGraph):
        dot = populated_graph.export_dot()
        assert dot.startswith("digraph ontology {")
        assert "Odin" in dot
        assert "→" not in dot or "->" in dot  # DOT uses ->

    def test_export_json(self, populated_graph: OntologyGraph):
        data = populated_graph.export_json()
        assert "entities" in data
        assert "relations" in data
        assert "stats" in data
        assert len(data["entities"]) == 8
        assert len(data["relations"]) == 10

    def test_export_json_serializable(self, populated_graph: OntologyGraph):
        data = populated_graph.export_json()
        # Should not raise
        json_str = json.dumps(data, indent=2)
        assert len(json_str) > 100


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_custom_confidence(self, graph: OntologyGraph):
        entity = graph.add_entity("LowConf", "concept", confidence=0.3)
        assert entity.confidence == 0.3

    def test_metadata_stored(self, graph: OntologyGraph):
        entity = graph.add_entity("Meta", "agent", metadata={"source": "test"})
        fetched = graph.get_entity(entity.id)
        assert fetched is not None
        assert fetched.metadata == {"source": "test"}

    def test_relation_with_properties(self, graph: OntologyGraph):
        e1 = graph.add_entity("A", "agent")
        e2 = graph.add_entity("B", "tool")
        rel = graph.add_relation(e1.id, e2.id, "uses", properties={"since": "2026"})
        assert rel is not None
        assert rel.properties == {"since": "2026"}

    def test_large_graph(self, graph: OntologyGraph):
        """Performance test with 100 entities."""
        entities = []
        for i in range(100):
            e = graph.add_entity(f"entity_{i}", "concept")
            entities.append(e)

        # Connect in a chain
        for i in range(len(entities) - 1):
            graph.add_relation(entities[i].id, entities[i + 1].id, "related_to")

        stats = graph.stats()
        assert stats["entity_count"] == 100
        assert stats["relation_count"] == 99

        # Shortest path across the chain
        path = graph.shortest_path(entities[0].id, entities[99].id, max_depth=100)
        assert path is not None
        assert path.length == 99

    def test_cycle_detection(self, graph: OntologyGraph):
        """Graph with cycles should still work for traversal."""
        a = graph.add_entity("A", "agent")
        b = graph.add_entity("B", "agent")
        c = graph.add_entity("C", "agent")
        graph.add_relation(a.id, b.id, "related_to")
        graph.add_relation(b.id, c.id, "related_to")
        graph.add_relation(c.id, a.id, "related_to")  # cycle

        sub = graph.neighbors(a.id, depth=5)
        assert len(sub.entities) == 3  # all reachable without infinite loop
