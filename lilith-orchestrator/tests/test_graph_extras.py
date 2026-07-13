"""Tests for lilith_orchestrator.graph (nodes, builder, presets)."""
import pytest
from pathlib import Path
from typing import Any

from lilith_orchestrator.graph.state import GraphState, NodeType, Checkpointer, GraphCheckpoint
from lilith_orchestrator.graph.nodes import (
    extract_intent,
    select_agent,
    router_node,
    memory_node,
    persona_node,
    tool_node,
    output_node,
)
from lilith_orchestrator.graph.builder import ConversationGraph
from lilith_orchestrator.graph.presets import (
    conversation_preset,
    research_preset,
    code_preset,
    creative_preset,
    debug_preset,
    pipeline_preset,
)


# ── Intent extraction ─────────────────────────────────────────────


def test_extract_intent_code():
    assert extract_intent("write a function to parse JSON") == "code"


def test_extract_intent_research():
    assert extract_intent("research the history of Yggdrasil") == "research"


def test_extract_intent_creative():
    assert extract_intent("write a story about dragons") == "creative"


def test_extract_intent_debug():
    assert extract_intent("fix this bug in my code") == "debug"


def test_extract_intent_chat_default():
    assert extract_intent("hello there") == "chat"


def test_extract_intent_empty():
    assert extract_intent("") == "chat"


def test_extract_intent_case_insensitive():
    assert extract_intent("DEBUG the FUNCTION") == "code" or extract_intent("DEBUG the FUNCTION") == "debug"


# ── Agent selection ───────────────────────────────────────────────


def test_select_agent_known_intent():
    assert select_agent("code") == "odin"
    assert select_agent("research") == "mimir"
    assert select_agent("creative") == "eva"
    assert select_agent("chat") == "lilith"
    assert select_agent("debug") == "adan"


def test_select_agent_unknown_intent():
    assert select_agent("xyz") == "lilith"


def test_select_agent_with_availability():
    # Preferred available
    assert select_agent("code", ["odin", "lilith"]) == "odin"
    # Preferred not available, fallback
    assert select_agent("code", ["lilith", "eva"]) == "lilith"
    # Empty list
    assert select_agent("code", []) == "lilith"


# ── Node functions ────────────────────────────────────────────────


def _make_state(**kwargs) -> GraphState:
    return GraphState(
        messages=kwargs.pop("messages", []),
        current_node="",
        context=kwargs.pop("context", {}),
    )


def test_router_node_empty_messages():
    state = _make_state()
    out = router_node(state)
    assert out.context["intent"] == "chat"
    assert out.context["routed_agent"] == "lilith"
    assert out.current_node == "lilith"


def test_router_node_with_code_message():
    state = _make_state(messages=[{"role": "user", "content": "write a function"}])
    out = router_node(state)
    assert out.context["intent"] == "code"
    assert out.context["routed_agent"] == "odin"


def test_memory_node_adds_result():
    state = _make_state(messages=[{"role": "user", "content": "test"}])
    out = memory_node(state)
    assert len(out.memory_results) == 1
    assert out.memory_results[0]["query"] == "test"
    assert out.context["memory_lookup_done"] is True


def test_memory_node_empty_messages():
    state = _make_state()
    out = memory_node(state)
    assert out.current_node == "memory"


def test_persona_node_code_intent():
    state = _make_state(context={"intent": "code"})
    out = persona_node(state)
    assert out.context["persona"]["tone"] == "precise"
    assert out.context["persona"]["style"] == "technical"


def test_persona_node_creative_intent():
    state = _make_state(context={"intent": "creative"})
    out = persona_node(state)
    assert out.context["persona"]["verbosity"] == "high"


def test_tool_node_no_calls():
    state = _make_state(context={})
    out = tool_node(state)
    assert out.tool_results == []
    assert out.current_node == "tool"


def test_tool_node_with_calls():
    state = _make_state(context={"tool_calls": [{"name": "search", "args": {"q": "test"}}]})
    out = tool_node(state)
    assert len(out.tool_results) == 1
    assert out.tool_results[0]["tool"] == "search"
    # tool_calls should be cleared
    assert "tool_calls" not in out.context


def test_output_node_appends_assistant():
    state = _make_state(
        messages=[{"role": "user", "content": "hi"}],
        context={"persona": {"tone": "friendly"}},
    )
    out = output_node(state)
    assert out.messages[-1]["role"] == "assistant"
    assert out.messages[-1]["persona_applied"] is True
    assert out.context["output_produced"] is True


def test_output_node_with_memory_and_tools():
    state = GraphState(
        messages=[{"role": "user", "content": "x"}],
        memory_results=[{"query": "x", "results": []}],
        tool_results=[{"tool": "t", "result": "r"}],
    )
    out = output_node(state)
    # memory_used / tools_used are only added when the state already had
    # memory/tool results flowing through. router_node → tool_node → memory_node
    # flow is what populates them; the bare test state has them set
    # but output_node reads from state.tool_results / state.memory_results
    # after they may have been consumed. Verify the key fields are set
    # based on the input state.
    out_msg = out.messages[-1]
    assert out_msg["role"] == "assistant"
    # output_node checks `if state.memory_results` and `if state.tool_results`
    # directly, so they should be present
    assert out_msg.get("memory_used") is True
    assert out_msg.get("tools_used") is True


# ── Builder ───────────────────────────────────────────────────────


def test_conversation_graph_creation():
    g = ConversationGraph()
    assert g._graph is None
    assert g._custom_nodes == []
    assert g._custom_edges == []


def test_conversation_graph_add_node():
    g = ConversationGraph()
    def my_node(state): return state
    g.add_node("custom", my_node)
    assert len(g._custom_nodes) == 1
    assert g._graph is None  # invalidated


def test_conversation_graph_add_edge():
    g = ConversationGraph()
    g.add_edge("a", "b")
    assert ("a", "b") in g._custom_edges


def test_conversation_graph_add_conditional_edges():
    g = ConversationGraph()
    g.add_conditional_edges("a", lambda s: "x", {"x": "b"})
    assert len(g._custom_conditional_edges) == 1


def test_conversation_graph_build_without_langgraph():
    # If langgraph is missing, build() raises ImportError
    # If present, build() returns a compiled graph
    g = ConversationGraph()
    try:
        result = g.build()
        # langgraph is available
        assert result is not None
    except ImportError as e:
        assert "langgraph" in str(e).lower()


# ── Presets ───────────────────────────────────────────────────────


def test_conversation_preset():
    g = conversation_preset()
    assert isinstance(g, ConversationGraph)


def test_research_preset():
    g = research_preset()
    assert isinstance(g, ConversationGraph)
    assert getattr(g, "_research_mode", False) is True


def test_code_preset():
    g = code_preset()
    assert isinstance(g, ConversationGraph)


def test_creative_preset():
    g = creative_preset()
    assert isinstance(g, ConversationGraph)
    assert getattr(g, "_creative_mode", False) is True


def test_debug_preset():
    g = debug_preset()
    assert isinstance(g, ConversationGraph)


def test_pipeline_preset():
    g = pipeline_preset()
    assert isinstance(g, ConversationGraph)
    assert getattr(g, "_pipeline_mode", False) is True
    # Should have 5 phase edges (idea→research→design→plan→code→memory)
    assert len(g._custom_edges) >= 5
