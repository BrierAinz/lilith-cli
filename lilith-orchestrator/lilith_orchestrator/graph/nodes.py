"""Pre-built graph node functions for LangGraph conversation flows.

Each node is a pure function ``(state: GraphState) -> GraphState``
that reads required fields, performs work, and returns a new state
with updates.  This module has NO dependency on LangGraph itself.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from lilith_orchestrator.graph.state import GraphState


# ── Intent extraction ───────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "code": [
        "code",
        "program",
        "function",
        "class",
        "script",
        "implement",
        "compile",
        "syntax",
        "variable",
        "api",
        "refactor",
        "feature",
        "module",
        "package",
        "deploy",
        "build",
        "write",
        "create a",
        "develop",
    ],
    "research": [
        "research",
        "search",
        "find",
        "look up",
        "information",
        "data",
        "analyze",
        "study",
        "investigate",
        "explore",
        "compare",
        "facts",
        "what is",
        "who is",
        "where is",
        "when did",
        "how does",
    ],
    "creative": [
        "write",
        "story",
        "poem",
        "creative",
        "imagine",
        "create",
        "design",
        "compose",
        "generate",
        "brainstorm",
        "fiction",
        "narrative",
        "character",
        "plot",
        "lyrics",
        "art",
    ],
    "debug": [
        "debug",
        "fix",
        "bug",
        "error",
        "traceback",
        "exception",
        "crash",
        "fail",
        "stack trace",
        "assert",
        "segfault",
        "hang",
        "freeze",
        "timeout",
        "broken",
        "doesn't work",
        "not working",
        "issue",
    ],
    "chat": [
        "hello",
        "hi",
        "hey",
        "how are",
        "chat",
        "talk",
        "conversation",
        "thank",
        "goodbye",
        "bye",
        "greet",
        "morning",
        "evening",
    ],
}


def extract_intent(message: str) -> str:
    """Extract the primary intent from a user message using keyword matching.

    Args:
        message: The user's message text.

    Returns:
        One of: ``"code"``, ``"research"``, ``"creative"``, ``"debug"``, ``"chat"``.
        Defaults to ``"chat"`` if no specific intent is detected.
    """
    text = message.lower().strip()

    if not text:
        return "chat"

    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            # Use word-boundary matching for short keywords, substring for longer ones
            if len(keyword) <= 4:
                pattern = rf"\b{re.escape(keyword)}\b"
                score += len(re.findall(pattern, text))
            else:
                score += text.count(keyword)
        scores[intent] = score

    # Pick the highest-scoring intent
    best_intent = max(scores, key=lambda k: scores[k])
    if scores[best_intent] == 0:
        return "chat"
    return best_intent


# ── Agent selection ──────────────────────────────────────────────────────────

_INTENT_AGENT_MAP: dict[str, str] = {
    "code": "odin",
    "research": "mimir",
    "creative": "eva",
    "chat": "lilith",
    "debug": "adan",
}

_DEFAULT_AGENTS: list[str] = ["odin", "mimir", "eva", "lilith", "adan"]


def select_agent(intent: str, available_agents: list[str] | None = None) -> str:
    """Map an intent to an agent name.

    Args:
        intent: One of the intent strings returned by :func:`extract_intent`.
        available_agents: Optional list of agent names. If the preferred agent
            is not in this list, the first available agent is returned.

    Returns:
        The selected agent name.
    """
    preferred = _INTENT_AGENT_MAP.get(intent, "lilith")

    if available_agents is None:
        return preferred

    if preferred in available_agents:
        return preferred

    # Fallback to the first available agent
    if available_agents:
        return available_agents[0]

    return "lilith"


# ── Node functions ───────────────────────────────────────────────────────────


def router_node(state: GraphState) -> GraphState:
    """Analyze the last message and decide which agent to route to.

    Sets ``current_node`` to the selected agent name and updates
    ``context["intent"]`` and ``context["routed_agent"]``.
    """
    messages = state.messages
    if not messages:
        return state.copy_with(
            current_node="lilith",
            context={**state.context, "intent": "chat", "routed_agent": "lilith"},
        )

    last_msg = messages[-1]
    content = last_msg.get("content", "") if isinstance(last_msg, dict) else str(last_msg)

    intent = extract_intent(content)
    agent = select_agent(intent)

    new_context = {**state.context, "intent": intent, "routed_agent": agent}
    return state.copy_with(current_node=agent, context=new_context)


def memory_node(state: GraphState) -> GraphState:
    """Look up relevant memories and add them to context.

    In a full implementation this would query a MemoryStore.
    This stub records that a memory lookup was performed and
    adds a placeholder memory_result.
    """
    messages = state.messages
    if not messages:
        return state.copy_with(current_node="memory")

    last_msg = messages[-1]
    content = last_msg.get("content", "") if isinstance(last_msg, dict) else str(last_msg)

    # Placeholder: in production, query MemoryStore here
    memory_result: dict[str, Any] = {
        "query": content[:200],
        "results": [],
        "timestamp": time.time(),
        "source": "memory_node",
    }

    new_memory_results = [*state.memory_results, memory_result]
    new_context = {**state.context, "memory_lookup_done": True}

    return state.copy_with(
        current_node="memory",
        memory_results=new_memory_results,
        context=new_context,
    )


def persona_node(state: GraphState) -> GraphState:
    """Apply persona adaptation based on the current context.

    Adjusts the context with persona parameters (tone, style, etc.)
    based on user mood or other context cues.
    """
    context = state.context
    intent = context.get("intent", "chat")

    # Map intents to persona profiles
    personas: dict[str, dict[str, Any]] = {
        "code": {"tone": "precise", "style": "technical", "verbosity": "low"},
        "research": {"tone": "informative", "style": "analytical", "verbosity": "medium"},
        "creative": {"tone": "expressive", "style": "narrative", "verbosity": "high"},
        "debug": {"tone": "analytical", "style": "diagnostic", "verbosity": "medium"},
        "chat": {"tone": "friendly", "style": "conversational", "verbosity": "medium"},
    }

    persona = personas.get(intent, personas["chat"])
    new_context = {**context, "persona": persona}

    return state.copy_with(current_node="persona", context=new_context)


def tool_node(state: GraphState) -> GraphState:
    """Execute tool calls from previous agent output.

    Looks for ``tool_calls`` in the context and simulates execution.
    In a full implementation this would dispatch to real tool runners.
    """
    context = state.context
    tool_calls: list[dict[str, Any]] = context.get("tool_calls", [])

    if not tool_calls:
        # No tool calls to execute — pass through
        return state.copy_with(current_node="tool")

    # Simulate tool execution
    results: list[dict[str, Any]] = []
    for call in tool_calls:
        tool_name = call.get("name", "unknown")
        tool_args = call.get("args", {})
        results.append(
            {
                "tool": tool_name,
                "args": tool_args,
                "result": f"(simulated) {tool_name} executed successfully",
                "timestamp": time.time(),
            }
        )

    new_tool_results = [*state.tool_results, *results]
    # Clear processed tool_calls from context
    new_context = {k: v for k, v in context.items() if k != "tool_calls"}

    return state.copy_with(
        current_node="tool",
        tool_results=new_tool_results,
        context=new_context,
    )


def output_node(state: GraphState) -> GraphState:
    """Format the final response from the graph.

    Takes the last message in the conversation history and produces
    a formatted output message appended to the messages list.
    """
    context = state.context
    messages = state.messages

    # Gather the latest content
    if messages:
        last_msg = messages[-1]
        content = last_msg.get("content", "") if isinstance(last_msg, dict) else str(last_msg)
    else:
        content = ""

    # Add output metadata
    output_message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "timestamp": time.time(),
        "node": "output",
    }

    # Include persona info if available
    persona = context.get("persona", {})
    if persona:
        output_message["persona_applied"] = True

    # Include memory info if available
    if state.memory_results:
        output_message["memory_used"] = True

    # Include tool info if available
    if state.tool_results:
        output_message["tools_used"] = True

    new_messages = [*messages, output_message]
    new_context = {**context, "output_produced": True}

    return state.copy_with(
        current_node="output",
        messages=new_messages,
        context=new_context,
    )
