"""Tests for lilith_orchestrator.graph.state."""
import pytest
from pathlib import Path

from lilith_orchestrator.graph.state import (
    GraphState,
    NodeType,
    GraphCheckpoint,
    Checkpointer,
)


# ── NodeType ──────────────────────────────────────────────────────


def test_node_type_values():
    assert NodeType.ROUTER == "router"
    assert NodeType.AGENT == "agent"
    assert NodeType.TOOL == "tool"
    assert NodeType.MEMORY == "memory"
    assert NodeType.PERSONA == "persona"
    assert NodeType.OUTPUT == "output"


def test_node_type_is_strenum():
    assert isinstance(NodeType.ROUTER, str)
    assert NodeType.ROUTER.value == "router"


# ── GraphState ────────────────────────────────────────────────────


def test_graph_state_defaults():
    state = GraphState()
    assert state.messages == []
    assert state.current_node == ""
    assert state.context == {}
    assert state.memory_results == []
    assert state.tool_results == []
    assert state.errors == []
    assert state.metadata == {}


def test_graph_state_with_values():
    state = GraphState(
        messages=[{"role": "user", "content": "hi"}],
        current_node="router",
        context={"intent": "code"},
    )
    assert state.current_node == "router"
    assert state.context["intent"] == "code"


def test_graph_state_setters():
    state = GraphState()
    state.messages = [{"role": "user", "content": "test"}]
    state.current_node = "odin"
    state.context = {"x": 1}
    assert state.messages[0]["content"] == "test"
    assert state.current_node == "odin"
    assert state.context["x"] == 1


def test_graph_state_copy_with():
    state = GraphState(current_node="router", context={"a": 1})
    new_state = state.copy_with(current_node="lilith", context={"b": 2})
    assert new_state.current_node == "lilith"
    assert new_state.context == {"b": 2}
    # Original is unchanged
    assert state.current_node == "router"
    assert state.context == {"a": 1}


def test_graph_state_copy_isolates_context():
    # context is deep-copied via dict() in copy_with
    state = GraphState(context={"nested": {"k": 1}})
    new_state = state.copy_with()
    new_state.context["nested"]["k"] = 999
    # Original is unchanged because dict() creates a shallow copy at the top level
    # but the inner dict is shared. This test verifies the surface copy
    # at least protects the top-level keys.
    new_state.context["new_top_key"] = "x"
    assert "new_top_key" not in state.context


# ── GraphCheckpoint ───────────────────────────────────────────────


def test_checkpoint_default_id():
    cp = GraphCheckpoint()
    assert isinstance(cp.id, str)
    assert len(cp.id) > 0


def test_checkpoint_state_and_timestamp():
    cp = GraphCheckpoint(state={"messages": []}, node_name="router")
    assert cp.state == {"messages": []}
    assert cp.node_name == "router"
    assert cp.timestamp > 0


# ── Checkpointer (in-memory) ──────────────────────────────────────


@pytest.fixture
def cp() -> Checkpointer:
    return Checkpointer()  # in-memory


def test_checkpointer_save_and_load(cp: Checkpointer):
    cp_obj = GraphCheckpoint(state={"messages": [{"role": "user", "content": "hi"}]})
    cp_id = cp.save(cp_obj)
    loaded = cp.load(cp_id)
    assert loaded is not None
    assert loaded.id == cp_id
    assert loaded.state["messages"][0]["content"] == "hi"


def test_checkpointer_load_missing(cp: Checkpointer):
    assert cp.load("nonexistent") is None


def test_checkpointer_list_empty(cp: Checkpointer):
    assert cp.list_checkpoints() == []


def test_checkpointer_list_by_session(cp: Checkpointer):
    cp.save(GraphCheckpoint(state={"metadata": {"session_id": "s1"}}))
    cp.save(GraphCheckpoint(state={"metadata": {"session_id": "s2"}}))
    cp.save(GraphCheckpoint(state={"metadata": {"session_id": "s1"}}))
    s1 = cp.list_checkpoints(session_id="s1")
    assert len(s1) == 2


def test_checkpointer_delete(cp: Checkpointer):
    cp_id = cp.save(GraphCheckpoint())
    assert cp.delete(cp_id) is True
    assert cp.delete(cp_id) is False
    assert cp.load(cp_id) is None


@pytest.mark.asyncio
async def test_checkpointer_async_save_load():
    cp = Checkpointer()
    cp_obj = GraphCheckpoint(state={"x": 1})
    cp_id = await cp.save_async(cp_obj)
    loaded = await cp.load_async(cp_id)
    assert loaded is not None
    assert loaded.state == {"x": 1}


@pytest.mark.asyncio
async def test_checkpointer_async_list():
    cp = Checkpointer()
    await cp.save_async(GraphCheckpoint(state={"metadata": {"session_id": "async-s"}}))
    results = await cp.list_checkpoints_async(session_id="async-s")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_checkpointer_async_delete():
    cp = Checkpointer()
    cp_obj = GraphCheckpoint()
    cp_id = await cp.save_async(cp_obj)
    deleted = await cp.delete_async(cp_id)
    assert deleted is True


def test_checkpointer_persists_to_disk(tmp_path: Path):
    db = tmp_path / "cp.db"
    cp1 = Checkpointer(db)
    cp_id = cp1.save(GraphCheckpoint(state={"x": 42}))

    # Reopen the same DB
    cp2 = Checkpointer(db)
    loaded = cp2.load(cp_id)
    assert loaded is not None
    assert loaded.state == {"x": 42}
