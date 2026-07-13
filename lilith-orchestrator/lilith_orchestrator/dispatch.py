"""Task Dispatcher — connects AgentRegistry with the orchestrator.

Routes tasks to the best-matching agent using Vanaheim agent cards
(intent, tools, level). The orchestrator's `router_node` already
extracts intent; this dispatcher adds tool-aware selection.

Usage::

    from lilith_orchestrator.dispatch import TaskDispatcher
    from lilith_skills.agent_cards import AgentCardLoader
    from lilith_skills.agent_registry import AgentRegistry

    loader = AgentCardLoader.from_vanaheim("/path/to/Yggdrasil")
    registry = AgentRegistry(loader)
    dispatcher = TaskDispatcher(registry)

    card = dispatcher.route(intent="code", required_tools=["terminal"])
    if card:
        orchestrator.assign(card.name, task)
"""

from __future__ import annotations

import re
from typing import Any

from lilith_skills.agent_cards import AgentCard
from lilith_skills.agent_registry import AgentRegistry


# Keyword → intent mapping for when a task description is given
# without an explicit intent. Mirrors lilith_orchestrator.graph.nodes.
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "code": ["code", "function", "class", "implement", "build", "refactor", "fix", "bug"],
    "research": ["research", "search", "find", "investigate", "what is", "compare"],
    "creative": ["write", "story", "poem", "creative", "imagine", "design"],
    "debug": ["debug", "traceback", "exception", "crash", "broken", "not working"],
    "chat": ["hello", "hi", "chat", "talk", "thank"],
}


def _extract_intent_from_text(text: str) -> str:
    """Best-effort intent extraction from a free-form task description."""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        scores[intent] = sum(text_lower.count(k) for k in keywords)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "chat"


# Optional tools inferred from a task description
_TOOL_HINTS: dict[str, list[str]] = {
    "code": ["terminal", "file_edit"],
    "research": ["web", "search"],
    "creative": ["image_gen"],
    "debug": ["terminal", "debug"],
    "chat": ["chat"],
}


def _extract_tools_from_text(text: str) -> list[str]:
    """Heuristically extract tool names mentioned in a task description."""
    text_lower = text.lower()
    tools: list[str] = []
    for tool in ("terminal", "file_edit", "web", "search", "image_gen", "debug", "chat"):
        if tool.replace("_", " ") in text_lower or tool in text_lower:
            tools.append(tool)
    return tools


class TaskDispatcher:
    """Routes tasks to agents using AgentRegistry."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def route(
        self,
        intent: str | None = None,
        required_tools: list[str] | None = None,
        prefer_level: int = 2,
    ) -> AgentCard | None:
        """Route based on explicit intent/tools.

        Args:
            intent: "code", "research", "creative", "debug", "chat".
            required_tools: List of tool names the task needs.
            prefer_level: 1 (consultant) or 2 (executor).

        Returns:
            Selected AgentCard or None.
        """
        return self.registry.select_agent(
            intent=intent,
            required_tools=required_tools,
            prefer_level=prefer_level,
        )

    def route_task(self, task_description: str, prefer_level: int = 2) -> AgentCard | None:
        """Route a free-form task description.

        Extracts intent and required tools from the text, then dispatches.

        Args:
            task_description: Plain-text description of the task.
            prefer_level: Preferred agent level (1 or 2).

        Returns:
            Selected AgentCard or None.
        """
        intent = _extract_intent_from_text(task_description)
        # Combine explicit tool hints for this intent with any
        # tools mentioned literally in the description.
        tools = list(set(_TOOL_HINTS.get(intent, []) + _extract_tools_from_text(task_description)))
        # Only require tools that an agent actually has — keep at least
        # the explicit ones; drop intent hints that would over-constrain.
        required = [t for t in tools if t not in _TOOL_HINTS.get(intent, [])]
        if not required:
            # Use just the intent, no required tools
            return self.registry.select_agent(intent=intent, prefer_level=prefer_level)
        return self.registry.select_agent(
            intent=intent,
            required_tools=required,
            prefer_level=prefer_level,
        )

    def explain(self, card: AgentCard) -> dict[str, Any]:
        """Return a human-readable explanation of why this agent was chosen."""
        return {
            "agent": card.name,
            "role": card.role,
            "level": card.level,
            "model": card.model,
            "tools": card.tools,
            "is_executor": card.is_executor(),
            "is_consultant": card.is_consultant(),
            "hooks": card.hooks,
        }
