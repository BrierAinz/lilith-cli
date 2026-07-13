"""Pre-built graph configurations (presets) for common conversational flows.

Each preset returns a :class:`ConversationGraph` pre-configured for a
specific use case.  Call ``.build()`` on the returned object to get a
compiled LangGraph StateGraph.
"""

from __future__ import annotations

from lilith_orchestrator.graph.builder import ConversationGraph


def conversation_preset() -> ConversationGraph:
    """Standard conversation flow with all nodes.

    Flow: START → router → {agents} → tool → memory → persona → output → END

    Returns:
        A :class:`ConversationGraph` ready to ``.build()``.
    """
    return ConversationGraph()


def research_preset() -> ConversationGraph:
    """Research-focused flow: router → mimir → memory → output.

    Skips tool execution — research queries typically don't need tools.

    Returns:
        A :class:`ConversationGraph` configured for research tasks.
    """
    graph = ConversationGraph()

    # Override the default edges: after mimir, skip tool → go to memory → output
    # We add custom edge mimir → memory (bypassing tool)
    graph.add_edge("odin", "memory")  # code agent also skip tool in research context
    # Actually we need to build a minimal graph, so we override agent→dest edges

    # For research: agent nodes route to memory then output
    # These override the default agent → tool edges via custom edges
    for agent_name in ("odin", "mimir", "eva", "lilith", "adan"):
        graph.add_edge(agent_name, "memory")

    graph._research_mode = True
    return graph


def code_preset() -> ConversationGraph:
    """Code-focused flow: router → odin → tool → memory → output.

    Standard flow optimised for code generation and execution tasks.

    Returns:
        A :class:`ConversationGraph` configured for code tasks.
    """
    return ConversationGraph()


def creative_preset() -> ConversationGraph:
    """Creative flow: router → eva → output.

    Skips tool execution and memory lookup — creative tasks are direct.

    Returns:
        A :class:`ConversationGraph` configured for creative tasks.
    """
    graph = ConversationGraph()

    # Override agent → tool edges: creative goes straight to output
    for agent_name in ("odin", "mimir", "eva", "lilith", "adan"):
        graph.add_edge(agent_name, "output")

    graph._creative_mode = True
    return graph


def debug_preset() -> ConversationGraph:
    """Debug flow: router → adan → tool → memory → output.

    Includes tool execution (to run diagnostics) and memory lookup.

    Returns:
        A :class:`ConversationGraph` configured for debugging tasks.
    """
    return ConversationGraph()


def pipeline_preset() -> ConversationGraph:
    """5-Phase Pipeline preset: IDEA → RESEARCH → DESIGN → PLAN → CODE.

    Inspired by Aether-Agents' sequential quality-gate pipeline.
    Each phase has a node function and a quality gate that must pass
    before proceeding to the next phase.

    Flow:
        START → idea → research → design → plan → code → memory → output → END

    Quality gates enforce:
        - IDEA: non-empty summary
        - RESEARCH: query defined
        - DESIGN: components defined
        - PLAN: tasks defined
        - CODE: files created/modified

    Returns:
        A :class:`ConversationGraph` configured for structured pipeline execution.
    """
    from lilith_orchestrator.graph.pipeline import (
        PipelinePhase,
        DEFAULT_GATES,
        DEFAULT_NODES,
    )

    graph = ConversationGraph()

    # Add pipeline nodes for each phase
    for phase in PipelinePhase:
        node_fn = DEFAULT_NODES.get(phase)
        if node_fn:
            graph.add_node(phase.value, node_fn)

    # Connect phases sequentially: idea → research → design → plan → code
    graph.add_edge("idea", "research")
    graph.add_edge("research", "design")
    graph.add_edge("design", "plan")
    graph.add_edge("plan", "code")

    # After code, go to memory then output
    graph.add_edge("code", "memory")

    # Mark as pipeline mode
    graph._pipeline_mode = True
    return graph
