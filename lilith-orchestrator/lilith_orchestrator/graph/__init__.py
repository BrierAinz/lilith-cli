"""LangGraph StateGraph module for Yggdrasil.

Provides conversation graph building, state management, and checkpointing
for stateful multi-agent flows.

Exports:
    ConversationGraph: The main graph builder class.
    GraphState: The state dictionary that flows through the graph.
    GraphCheckpoint: A snapshot of a GraphState at a given point.
    NodeType: Enumeration of node types in the conversation graph.
"""

from __future__ import annotations

from lilith_orchestrator.graph.builder import ConversationGraph
from lilith_orchestrator.graph.nodes import (
    extract_intent,
    memory_node,
    output_node,
    persona_node,
    router_node,
    select_agent,
    tool_node,
)
from lilith_orchestrator.graph.presets import (
    code_preset,
    conversation_preset,
    creative_preset,
    debug_preset,
    research_preset,
)
from lilith_orchestrator.graph.state import (
    Checkpointer,
    GraphCheckpoint,
    GraphState,
    NodeType,
)


__all__ = [
    "Checkpointer",
    "ConversationGraph",
    "GraphCheckpoint",
    "GraphState",
    "NodeType",
    "code_preset",
    "conversation_preset",
    "creative_preset",
    "debug_preset",
    "extract_intent",
    "memory_node",
    "output_node",
    "persona_node",
    "research_preset",
    "router_node",
    "select_agent",
    "tool_node",
]
