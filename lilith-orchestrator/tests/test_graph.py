"""Tests for the lilith_orchestrator.graph module.

Covers:
- GraphState creation and mutation
- Checkpointer save/load/list/delete (sync and async)
- Node functions (router, memory, persona, tool, output)
- Intent extraction and agent selection
- ConversationGraph build and run (with LangGraph mock if not installed)
- Preset graph creation
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from pathlib import Path

from lilith_orchestrator.graph.nodes import (
    extract_intent,
    memory_node,
    output_node,
    persona_node,
    router_node,
    select_agent,
    tool_node,
)
from lilith_orchestrator.graph.state import (
    Checkpointer,
    GraphCheckpoint,
    GraphState,
    NodeType,
)


# ── NodeType tests ──────────────────────────────────────────────────────────


class TestNodeType:
    """Tests for the NodeType enum."""

    def test_node_types_exist(self) -> None:
        assert NodeType.ROUTER == "router"
        assert NodeType.AGENT == "agent"
        assert NodeType.TOOL == "tool"
        assert NodeType.MEMORY == "memory"
        assert NodeType.PERSONA == "persona"
        assert NodeType.OUTPUT == "output"

    def test_node_type_is_string(self) -> None:
        """NodeType values should be strings for JSON serialisation."""
        for member in NodeType:
            assert isinstance(member.value, str)


# ── GraphState tests ─────────────────────────────────────────────────────────


class TestGraphState:
    """Tests for GraphState creation and mutation."""

    def test_default_state(self) -> None:
        state = GraphState()
        assert state.messages == []
        assert state.current_node == ""
        assert state.context == {}
        assert state.memory_results == []
        assert state.tool_results == []
        assert state.errors == []
        assert state.metadata == {}

    def test_state_with_messages(self) -> None:
        messages = [
            {"role": "user", "content": "Hello", "timestamp": 1000.0},
            {"role": "assistant", "content": "Hi!", "timestamp": 1001.0},
        ]
        state = GraphState(messages=messages)
        assert len(state.messages) == 2
        assert state.messages[0]["role"] == "user"

    def test_state_with_context(self) -> None:
        context = {"user_mood": "happy", "project_type": "web"}
        state = GraphState(context=context)
        assert state.context["user_mood"] == "happy"

    def test_copy_with(self) -> None:
        state = GraphState(messages=[{"role": "user", "content": "test"}])
        new_state = state.copy_with(current_node="router")
        assert new_state.current_node == "router"
        assert len(new_state.messages) == 1
        # Original unchanged
        assert state.current_node == ""

    def test_copy_with_preserves_original(self) -> None:
        state = GraphState(context={"a": 1})
        new_state = state.copy_with(context={"a": 2, "b": 3})
        assert new_state.context == {"a": 2, "b": 3}
        assert state.context == {"a": 1}

    def test_state_property_setters(self) -> None:
        state = GraphState()
        state.current_node = "test_node"
        state.errors = ["error1"]
        assert state.current_node == "test_node"
        assert state.errors == ["error1"]

    def test_state_dict_access(self) -> None:
        """GraphState inherits from dict, so dict access should work."""
        state = GraphState(current_node="test")
        assert state["current_node"] == "test"
        state["current_node"] = "updated"
        assert state.current_node == "updated"


# ── GraphCheckpoint tests ────────────────────────────────────────────────────


class TestGraphCheckpoint:
    """Tests for the GraphCheckpoint model."""

    def test_default_checkpoint(self) -> None:
        cp = GraphCheckpoint()
        assert cp.id  # auto-generated
        assert cp.state == {}
        assert cp.timestamp > 0
        assert cp.node_name == ""

    def test_checkpoint_with_fields(self) -> None:
        cp = GraphCheckpoint(
            id="test-123",
            state={"current_node": "router"},
            timestamp=1234567890.0,
            node_name="router",
        )
        assert cp.id == "test-123"
        assert cp.state == {"current_node": "router"}
        assert cp.timestamp == 1234567890.0
        assert cp.node_name == "router"


# ── Checkpointer tests ──────────────────────────────────────────────────────


class TestCheckpointerSync:
    """Tests for Checkpointer synchronous operations."""

    def test_save_and_load(self) -> None:
        cp = Checkpointer(db_path=None)  # in-memory
        checkpoint = GraphCheckpoint(
            state={"current_node": "router", "messages": []},
            node_name="router",
        )
        cid = cp.save(checkpoint)
        loaded = cp.load(cid)
        assert loaded is not None
        assert loaded.id == checkpoint.id
        assert loaded.node_name == "router"

    def test_load_nonexistent(self) -> None:
        cp = Checkpointer(db_path=None)
        assert cp.load("does-not-exist") is None

    def test_list_checkpoints(self) -> None:
        cp = Checkpointer(db_path=None)
        for i in range(3):
            cp.save(
                GraphCheckpoint(
                    state={"current_node": f"node_{i}"},
                    node_name=f"node_{i}",
                )
            )
        checkpoints = cp.list_checkpoints()
        assert len(checkpoints) == 3

    def test_list_checkpoints_by_session(self) -> None:
        cp = Checkpointer(db_path=None)
        cp.save(
            GraphCheckpoint(
                state={"metadata": {"session_id": "sess_a"}},
                node_name="router",
            )
        )
        cp.save(
            GraphCheckpoint(
                state={"metadata": {"session_id": "sess_b"}},
                node_name="agent",
            )
        )
        a_checkpoints = cp.list_checkpoints(session_id="sess_a")
        assert len(a_checkpoints) == 1
        assert a_checkpoints[0].node_name == "router"

    def test_delete(self) -> None:
        cp = Checkpointer(db_path=None)
        checkpoint = GraphCheckpoint(state={}, node_name="test")
        cid = cp.save(checkpoint)
        assert cp.delete(cid) is True
        assert cp.load(cid) is None

    def test_delete_nonexistent(self) -> None:
        cp = Checkpointer(db_path=None)
        assert cp.delete("no-such-id") is False

    def test_save_to_file(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_checkpoints.db"
        cp = Checkpointer(db_path=db_file)
        checkpoint = GraphCheckpoint(
            state={"current_node": "test"},
            node_name="test",
        )
        cid = cp.save(checkpoint)
        loaded = cp.load(cid)
        assert loaded is not None
        assert loaded.node_name == "test"

    def test_save_overwrite(self) -> None:
        cp = Checkpointer(db_path=None)
        checkpoint = GraphCheckpoint(id="fixed-id", state={"v": 1}, node_name="a")
        cp.save(checkpoint)
        # Save again with same ID
        checkpoint2 = GraphCheckpoint(id="fixed-id", state={"v": 2}, node_name="b")
        cp.save(checkpoint2)
        loaded = cp.load("fixed-id")
        assert loaded is not None
        assert loaded.state["v"] == 2


class TestCheckpointerAsync:
    """Tests for Checkpointer async operations."""

    @pytest.mark.asyncio
    async def test_save_and_load_async(self) -> None:
        cp = Checkpointer(db_path=None)
        checkpoint = GraphCheckpoint(
            state={"current_node": "router"},
            node_name="router",
        )
        cid = await cp.save_async(checkpoint)
        loaded = await cp.load_async(cid)
        assert loaded is not None
        assert loaded.node_name == "router"

    @pytest.mark.asyncio
    async def test_delete_async(self) -> None:
        cp = Checkpointer(db_path=None)
        checkpoint = GraphCheckpoint(state={}, node_name="test")
        cid = await cp.save_async(checkpoint)
        result = await cp.delete_async(cid)
        assert result is True

    @pytest.mark.asyncio
    async def test_list_checkpoints_async(self) -> None:
        cp = Checkpointer(db_path=None)
        for i in range(3):
            await cp.save_async(GraphCheckpoint(state={"i": i}, node_name=f"node_{i}"))
        checkpoints = await cp.list_checkpoints_async()
        assert len(checkpoints) == 3

    @pytest.mark.asyncio
    async def test_list_checkpoints_by_session_async(self) -> None:
        cp = Checkpointer(db_path=None)
        await cp.save_async(
            GraphCheckpoint(
                state={"metadata": {"session_id": "s1"}},
                node_name="a",
            )
        )
        await cp.save_async(
            GraphCheckpoint(
                state={"metadata": {"session_id": "s2"}},
                node_name="b",
            )
        )
        result = await cp.list_checkpoints_async(session_id="s1")
        assert len(result) == 1
        assert result[0].node_name == "a"


# ── Node function tests ──────────────────────────────────────────────────────


class TestExtractIntent:
    """Tests for the extract_intent helper."""

    def test_code_intent(self) -> None:
        assert extract_intent("write a function to sort a list") == "code"
        assert extract_intent("implement a REST API") == "code"
        assert extract_intent("fix this error traceback") == "debug"

    def test_research_intent(self) -> None:
        assert extract_intent("research the history of Rome") == "research"
        assert extract_intent("what is quantum computing") == "research"

    def test_creative_intent(self) -> None:
        assert extract_intent("write a story about dragons") == "creative"
        assert extract_intent("compose a poem") == "creative"

    def test_debug_intent(self) -> None:
        assert extract_intent("debug this error") == "debug"
        assert extract_intent("I'm getting a traceback") == "debug"

    def test_chat_intent(self) -> None:
        assert extract_intent("hello there") == "chat"
        assert extract_intent("how are you doing?") == "chat"

    def test_default_chat(self) -> None:
        """Unknown messages should default to chat."""
        assert extract_intent("") == "chat"
        assert extract_intent("xyz123") == "chat"

    def test_intent_priority(self) -> None:
        """When multiple intents match, the highest-scoring one wins."""
        # "debug my code" has both debug and code keywords; debug has
        # stronger keyword match here
        result = extract_intent("debug error traceback in my program")
        assert result == "debug"


class TestSelectAgent:
    """Tests for the select_agent helper."""

    def test_code_maps_to_odin(self) -> None:
        assert select_agent("code") == "odin"

    def test_research_maps_to_mimir(self) -> None:
        assert select_agent("research") == "mimir"

    def test_creative_maps_to_eva(self) -> None:
        assert select_agent("creative") == "eva"

    def test_chat_maps_to_lilith(self) -> None:
        assert select_agent("chat") == "lilith"

    def test_debug_maps_to_adan(self) -> None:
        assert select_agent("debug") == "adan"

    def test_unknown_intent_defaults_to_lilith(self) -> None:
        assert select_agent("unknown") == "lilith"

    def test_available_agents_filter(self) -> None:
        # If preferred agent isn't available, pick first available
        assert select_agent("code", ["mimir", "lilith"]) == "mimir"
        # If preferred is available, use it
        assert select_agent("code", ["odin", "mimir"]) == "odin"

    def test_empty_available_agents(self) -> None:
        assert select_agent("code", []) == "lilith"


class TestRouterNode:
    """Tests for the router_node function."""

    def test_routes_chat(self) -> None:
        state = GraphState(messages=[{"role": "user", "content": "hello", "timestamp": 1.0}])
        result = router_node(state)
        assert result.current_node == "lilith"
        assert result.context["intent"] == "chat"
        assert result.context["routed_agent"] == "lilith"

    def test_routes_code(self) -> None:
        state = GraphState(
            messages=[{"role": "user", "content": "write a python function", "timestamp": 1.0}]
        )
        result = router_node(state)
        assert result.current_node == "odin"

    def test_empty_messages(self) -> None:
        state = GraphState()
        result = router_node(state)
        assert result.current_node == "lilith"

    def test_preserves_existing_context(self) -> None:
        state = GraphState(
            messages=[{"role": "user", "content": "debug this error", "timestamp": 1.0}],
            context={"user_mood": "frustrated"},
        )
        result = router_node(state)
        assert result.context["user_mood"] == "frustrated"
        assert result.context["intent"] == "debug"


class TestMemoryNode:
    """Tests for the memory_node function."""

    def test_adds_memory_result(self) -> None:
        state = GraphState(
            messages=[{"role": "user", "content": "remember this", "timestamp": 1.0}]
        )
        result = memory_node(state)
        assert len(result.memory_results) == 1
        assert result.context.get("memory_lookup_done") is True

    def test_empty_messages(self) -> None:
        state = GraphState()
        result = memory_node(state)
        assert result.current_node == "memory"


class TestPersonaNode:
    """Tests for the persona_node function."""

    def test_applies_code_persona(self) -> None:
        state = GraphState(context={"intent": "code"})
        result = persona_node(state)
        assert result.context["persona"]["tone"] == "precise"
        assert result.current_node == "persona"

    def test_applies_default_persona(self) -> None:
        state = GraphState(context={"intent": "chat"})
        result = persona_node(state)
        assert result.context["persona"]["tone"] == "friendly"


class TestToolNode:
    """Tests for the tool_node function."""

    def test_no_tool_calls(self) -> None:
        state = GraphState()
        result = tool_node(state)
        assert result.current_node == "tool"

    def test_executes_tool_calls(self) -> None:
        state = GraphState(
            context={
                "tool_calls": [
                    {"name": "search", "args": {"query": "python"}},
                ]
            }
        )
        result = tool_node(state)
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["tool"] == "search"

    def test_clears_tool_calls_from_context(self) -> None:
        state = GraphState(context={"tool_calls": [{"name": "test", "args": {}}], "other": "value"})
        result = tool_node(state)
        assert "tool_calls" not in result.context
        assert result.context["other"] == "value"


class TestOutputNode:
    """Tests for the output_node function."""

    def test_formats_output(self) -> None:
        state = GraphState(messages=[{"role": "assistant", "content": "Hello!", "timestamp": 1.0}])
        result = output_node(state)
        assert len(result.messages) == 2  # original + output
        assert result.messages[-1]["role"] == "assistant"
        assert result.messages[-1]["node"] == "output"

    def test_empty_messages(self) -> None:
        state = GraphState()
        result = output_node(state)
        assert result.messages[-1]["node"] == "output"
        assert result.context.get("output_produced") is True

    def test_includes_metadata(self) -> None:
        state = GraphState(
            messages=[{"role": "user", "content": "hi", "timestamp": 1.0}],
            context={"persona": {"tone": "friendly"}},
        )
        state.memory_results = [{"query": "test"}]
        result = output_node(state)
        assert result.messages[-1].get("persona_applied") is True
        assert result.messages[-1].get("memory_used") is True


# ── ConversationGraph tests ──────────────────────────────────────────────────


class TestConversationGraph:
    """Tests for the ConversationGraph builder."""

    def test_init(self) -> None:
        from lilith_orchestrator.graph.builder import ConversationGraph

        cg = ConversationGraph()
        assert cg._checkpointer is None

    def test_init_with_checkpointer(self) -> None:
        from lilith_orchestrator.graph.builder import ConversationGraph

        cp = Checkpointer(db_path=None)
        cg = ConversationGraph(checkpointer=cp)
        assert cg._checkpointer is cp

    def test_add_node(self) -> None:
        from lilith_orchestrator.graph.builder import ConversationGraph

        cg = ConversationGraph()

        def my_node(state: GraphState) -> GraphState:
            return state

        cg.add_node("custom", my_node)
        assert ("custom", my_node) in cg._custom_nodes

    def test_add_edge(self) -> None:
        from lilith_orchestrator.graph.builder import ConversationGraph

        cg = ConversationGraph()
        cg.add_edge("a", "b")
        assert ("a", "b") in cg._custom_edges

    def test_add_conditional_edges(self) -> None:
        from lilith_orchestrator.graph.builder import ConversationGraph

        cg = ConversationGraph()

        def cond(state: GraphState) -> str:
            return "a"

        cg.add_conditional_edges("source", cond, {"a": "dest_a"})
        assert len(cg._custom_conditional_edges) == 1

    def test_build_raises_import_error_without_langgraph(self) -> None:
        """If LangGraph is not installed, build() should raise ImportError."""
        from lilith_orchestrator.graph.builder import ConversationGraph, _langgraph_available

        if _langgraph_available():
            pytest.skip("LangGraph is installed — skipping ImportError test")

        cg = ConversationGraph()
        with pytest.raises(ImportError, match="LangGraph is required"):
            cg.build()

    def test_build_with_langgraph(self) -> None:
        """If LangGraph is installed, build() should succeed."""
        from lilith_orchestrator.graph.builder import ConversationGraph, _langgraph_available

        if not _langgraph_available():
            pytest.skip("LangGraph not installed — skipping build test")

        cg = ConversationGraph()
        graph = cg.build()
        assert graph is not None

    def test_run_with_langgraph(self) -> None:
        """Test run() with LangGraph installed."""
        from lilith_orchestrator.graph.builder import ConversationGraph, _langgraph_available

        if not _langgraph_available():
            pytest.skip("LangGraph not installed — skipping run test")

        cg = ConversationGraph()
        result = cg.run(messages=[{"role": "user", "content": "hello", "timestamp": 1.0}])
        assert isinstance(result, GraphState)
        assert len(result.messages) > 0

    def test_build_invalidates_cache(self) -> None:
        """Adding nodes/edges should invalidate the cached graph."""
        from lilith_orchestrator.graph.builder import ConversationGraph

        cg = ConversationGraph()
        cg._graph = "old_cached_graph"  # type: ignore[assignment]
        cg.add_node("test", lambda s: s)
        assert cg._graph is None


# ── Preset tests ─────────────────────────────────────────────────────────────


class TestPresets:
    """Tests for preset graph configurations."""

    def test_conversation_preset(self) -> None:
        from lilith_orchestrator.graph.presets import conversation_preset

        cg = conversation_preset()
        assert cg is not None

    def test_research_preset(self) -> None:
        from lilith_orchestrator.graph.presets import research_preset

        cg = research_preset()
        assert cg is not None

    def test_code_preset(self) -> None:
        from lilith_orchestrator.graph.presets import code_preset

        cg = code_preset()
        assert cg is not None

    def test_creative_preset(self) -> None:
        from lilith_orchestrator.graph.presets import creative_preset

        cg = creative_preset()
        assert cg is not None

    def test_debug_preset(self) -> None:
        from lilith_orchestrator.graph.presets import debug_preset

        cg = debug_preset()
        assert cg is not None

    def test_preset_build_with_langgraph(self) -> None:
        """Presets should build successfully when LangGraph is available."""
        from lilith_orchestrator.graph.builder import _langgraph_available
        from lilith_orchestrator.graph.presets import code_preset

        if not _langgraph_available():
            pytest.skip("LangGraph not installed")

        cg = code_preset()
        graph = cg.build()
        assert graph is not None


# ── Package import tests ──────────────────────────────────────────────────


class TestPackageImport:
    """Tests for the graph package imports."""

    def test_imports_from_package(self) -> None:
        from lilith_orchestrator.graph import (
            Checkpointer,
            ConversationGraph,
            GraphCheckpoint,
            GraphState,
            NodeType,
        )

        assert NodeType is not None
        assert GraphState is not None
        assert GraphCheckpoint is not None
        assert Checkpointer is not None
        assert ConversationGraph is not None

    def test_imports_from_state(self) -> None:
        from lilith_orchestrator.graph.state import (
            NodeType,
        )

        assert NodeType.ROUTER == "router"

    def test_imports_from_nodes(self) -> None:
        from lilith_orchestrator.graph.nodes import (
            extract_intent,
            select_agent,
        )

        assert callable(extract_intent)
        assert callable(select_agent)

    def test_imports_from_presets(self) -> None:
        from lilith_orchestrator.graph.presets import (
            conversation_preset,
        )

        assert callable(conversation_preset)


# ── Integration tests ──────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests for the full graph flow."""

    def test_state_flow_through_nodes(self) -> None:
        """Test state mutation through all node functions in sequence."""
        state = GraphState(
            messages=[{"role": "user", "content": "debug this error", "timestamp": 1.0}],
            context={},
        )

        # Router
        state = router_node(state)
        assert state.context["intent"] == "debug"
        assert state.current_node == "adan"

        # Persona
        state = persona_node(state)
        assert state.context["persona"]["tone"] == "analytical"

        # Tool (no tool calls)
        state = tool_node(state)
        assert state.current_node == "tool"

        # Memory
        state = memory_node(state)
        assert state.context.get("memory_lookup_done") is True

        # Output
        state = output_node(state)
        assert state.context.get("output_produced") is True
        assert len(state.messages) == 2  # original + output

    def test_full_conversation_with_tool_calls(self) -> None:
        """Test a conversation flow that includes tool calls."""
        state = GraphState(
            messages=[
                {
                    "role": "user",
                    "content": "write a python script to sort a list",
                    "timestamp": 1.0,
                }
            ],
            context={"tool_calls": [{"name": "python_exec", "args": {"code": "sorted([3,1,2])"}}]},
        )

        state = router_node(state)
        assert state.context["intent"] == "code"
        assert state.current_node == "odin"

        state = tool_node(state)
        assert len(state.tool_results) == 1
        assert "python_exec" in state.tool_results[0]["tool"]

        state = output_node(state)
        assert state.messages[-1].get("tools_used") is True

    def test_checkpoint_roundtrip_with_state(self) -> None:
        """Test saving and loading a graph state through checkpoints."""
        cp = Checkpointer(db_path=None)

        state = GraphState(
            messages=[{"role": "user", "content": "hello", "timestamp": 1000.0}],
            current_node="router",
            context={"intent": "chat", "routed_agent": "lilith"},
        )

        checkpoint = GraphCheckpoint(
            state=dict(state),
            node_name="router",
        )

        cid = cp.save(checkpoint)
        loaded = cp.load(cid)

        assert loaded is not None
        assert loaded.node_name == "router"
        assert loaded.state["current_node"] == "router"
        assert loaded.state["context"]["intent"] == "chat"
