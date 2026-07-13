"""ConversationGraph builder for LangGraph StateGraphs.

Constructs, configures, and runs LangGraph StateGraphs with conditional
edges, checkpointing, and streaming support.  LangGraph is an **optional**
dependency — if it is not installed, ``build()`` raises an ``ImportError``
with a helpful message.  All other methods work regardless.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lilith_orchestrator.graph.nodes import (
    memory_node,
    output_node,
    persona_node,
    router_node,
    tool_node,
)
from lilith_orchestrator.graph.state import Checkpointer, GraphState


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

logger = logging.getLogger("lilith.graph.builder")

# ── Lazy import guard ──────────────────────────────────────────────────────

_LANGGRAPH_AVAILABLE: bool | None = None


def _langgraph_available() -> bool:
    """Check whether ``langgraph`` is importable (cached)."""
    global _LANGGRAPH_AVAILABLE
    if _LANGGRAPH_AVAILABLE is None:
        try:
            import langgraph.graph  # noqa: F401

            _LANGGRAPH_AVAILABLE = True
        except ImportError:
            _LANGGRAPH_AVAILABLE = False
    return _LANGGRAPH_AVAILABLE


# ── Routing condition ──────────────────────────────────────────────────────


def _route_by_intent(state: GraphState) -> str:
    """Conditional edge function: routes based on the intent in context."""
    intent = state.context.get("intent", "chat")
    mapping = {
        "code": "odin",
        "research": "mimir",
        "creative": "eva",
        "chat": "lilith",
        "debug": "adan",
    }
    return mapping.get(intent, "lilith")


# ── ConversationGraph ──────────────────────────────────────────────────────


class ConversationGraph:
    """Builds and runs LangGraph StateGraphs for conversational AI flows.

    The default graph follows the pattern:
        START → router → {agent_nodes} → tool → memory → output → END

    Conditional edges from the router route to the appropriate agent
    based on the extracted intent.

    Args:
        checkpointer: Optional ``Checkpointer`` for persisting state.
            If ``None`` and LangGraph is installed, a ``MemorySaver``
            is used by default.
    """

    def __init__(self, checkpointer: Checkpointer | None = None) -> None:
        self._checkpointer = checkpointer
        self._custom_nodes: list[tuple[str, Callable]] = []
        self._custom_conditional_edges: list[tuple[str, Callable, dict[str, str]]] = []
        self._custom_edges: list[tuple[str, str]] = []
        self._graph: Any = None

    # ── Configuration ─────────────────────────────────────────────────────

    def add_node(self, name: str, func: Callable) -> None:
        """Register a custom node to be added during ``build()``.

        Args:
            name: Unique node name.
            func: A callable ``(state: GraphState) -> GraphState``.
        """
        self._custom_nodes.append((name, func))
        self._graph = None  # invalidate cached graph

    def add_conditional_edges(
        self,
        source: str,
        condition: Callable,
        edges: dict[str, str],
    ) -> None:
        """Register conditional edges from *source*.

        Args:
            source: Node name from which conditional edges originate.
            condition: Callable ``(state: GraphState) -> str`` returning a
                key into *edges*.
            edges: Mapping from condition result to destination node name.
        """
        self._custom_conditional_edges.append((source, condition, edges))
        self._graph = None

    def add_edge(self, from_node: str, to_node: str) -> None:
        """Register a direct edge between two nodes.

        Args:
            from_node: Source node name.
            to_node: Destination node name.
        """
        self._custom_edges.append((from_node, to_node))
        self._graph = None

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self) -> Any:
        """Construct and return a LangGraph ``StateGraph``.

        Raises:
            ImportError: If ``langgraph`` is not installed.

        Returns:
            A compiled LangGraph ``StateGraph`` ready for execution.
        """
        if not _langgraph_available():
            raise ImportError(
                "LangGraph is required to build a ConversationGraph. "
                "Install it with: pip install langgraph>=0.2 langchain-core>=0.3"
            )

        from langgraph.graph import END, START, StateGraph

        # -- Build the graph --
        graph = StateGraph(GraphState)

        # Core nodes
        graph.add_node("router", router_node)
        graph.add_node("memory", memory_node)
        graph.add_node("persona", persona_node)
        graph.add_node("tool", tool_node)
        graph.add_node("output", output_node)

        # Agent stub nodes (pass-through by default)
        for agent_name in ("odin", "mimir", "eva", "lilith", "adan"):
            graph.add_node(agent_name, self._make_agent_stub(agent_name))

        # Custom nodes
        for name, func in self._custom_nodes:
            graph.add_node(name, func)

        # -- Edges --
        # START → router
        graph.add_edge(START, "router")

        # router → {agents} (conditional)
        graph.add_conditional_edges(
            "router",
            _route_by_intent,
            {
                "odin": "odin",
                "mimir": "mimir",
                "eva": "eva",
                "lilith": "lilith",
                "adan": "adan",
            },
        )

        # Each agent → tool
        for agent_name in ("odin", "mimir", "eva", "lilith", "adan"):
            graph.add_edge(agent_name, "tool")

        # tool → memory → output → END
        graph.add_edge("tool", "memory")
        graph.add_edge("memory", "output")
        graph.add_edge("output", END)

        # Custom conditional edges
        for source, condition, edges in self._custom_conditional_edges:
            graph.add_conditional_edges(source, condition, edges)

        # Custom direct edges
        for from_node, to_node in self._custom_edges:
            graph.add_edge(from_node, to_node)

        # -- Compile with optional checkpointer --
        langgraph_checkpointer = None
        if self._checkpointer is not None:
            langgraph_checkpointer = None  # Use our own Checkpointer separately
        else:
            try:
                from langgraph.checkpoint.memory import MemorySaver

                langgraph_checkpointer = MemorySaver()
            except ImportError:
                pass

        compiled = graph.compile(checkpointer=langgraph_checkpointer)
        self._graph = compiled
        return compiled

    @staticmethod
    def _make_agent_stub(agent_name: str) -> Callable:
        """Create a pass-through node function for an agent."""

        def _stub(state: GraphState) -> GraphState:
            new_context = {**state.context, "agent": agent_name}
            return state.copy_with(current_node=agent_name, context=new_context)

        _stub.__name__ = f"{agent_name}_node"
        return _stub

    # ── Execution ─────────────────────────────────────────────────────────

    def run(
        self,
        messages: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> GraphState:
        """Run the graph to completion with the given messages.

        Args:
            messages: Conversation history (list of dicts with role/content/timestamp).
            context: Optional shared context.

        Returns:
            The final ``GraphState`` after graph execution.

        Raises:
            ImportError: If LangGraph is not installed.
        """
        if self._graph is None:
            self.build()

        initial_state = GraphState(
            messages=messages,
            current_node="",
            context=context or {},
            metadata={"session_id": context.get("session_id", "")} if context else {},
        )

        result = self._graph.invoke(dict(initial_state))
        return GraphState(**result) if isinstance(result, dict) else result

    async def run_stream(
        self,
        messages: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> AsyncGenerator[GraphState, None]:
        """Stream state updates as the graph executes.

        Args:
            messages: Conversation history.
            context: Optional shared context.

        Yields:
            Intermediate ``GraphState`` objects as each node completes.

        Raises:
            ImportError: If LangGraph is not installed.
        """
        if self._graph is None:
            self.build()

        initial_state = GraphState(
            messages=messages,
            current_node="",
            context=context or {},
            metadata={"session_id": context.get("session_id", "")} if context else {},
        )

        async for event in self._graph.astream(dict(initial_state)):
            # Each event is a dict mapping node_name → state_update
            if isinstance(event, dict):
                yield GraphState(**event) if isinstance(event, dict) else event
