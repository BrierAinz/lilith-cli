"""Agent Registry — indexed access to Vanaheim agent cards with dispatch.

Builds on AgentCardLoader: indexes cards by name, level, model, and tools,
and provides a ``select_agent()`` dispatch function used by
lilith-orchestrator to route tasks to the best-matching agent.

Inspired by the meta-harness pattern from research/emerging-agents-2026-06-21.md
(Omnigent-style agent dispatch).
"""

from __future__ import annotations

from typing import Any

from lilith_skills.agent_cards import AgentCard, AgentCardLoader


class AgentRegistry:
    """Indexed registry for AgentCards with dispatch support.

    Usage::

        loader = AgentCardLoader.from_vanaheim("/path/to/Yggdrasil")
        registry = AgentRegistry(loader)

        # Find agents matching criteria
        executors = registry.by_level(2)
        researchers = registry.by_tool("web")

        # Dispatch a task by intent
        card = registry.select_agent(intent="research")
    """

    def __init__(self, loader: AgentCardLoader) -> None:
        self.loader = loader
        self._cards: list[AgentCard] = loader.list_agents()
        self._by_name: dict[str, AgentCard] = {c.name: c for c in self._cards}

    # ── Lookups (mirror AgentCardLoader for convenience) ────────

    def list_agents(self) -> list[AgentCard]:
        return list(self._cards)

    def get(self, name: str) -> AgentCard | None:
        return self._by_name.get(name)

    def by_level(self, level: int) -> list[AgentCard]:
        return [c for c in self._cards if c.level == level]

    def by_tool(self, tool: str) -> list[AgentCard]:
        t = tool.lower()
        return [c for c in self._cards if t in [x.lower() for x in c.tools]]

    def by_model(self, model: str) -> list[AgentCard]:
        return [c for c in self._cards if c.model == model]

    def executors(self) -> list[AgentCard]:
        return [c for c in self._cards if c.is_executor()]

    def consultants(self) -> list[AgentCard]:
        return [c for c in self._cards if c.is_consultant()]

    # ── Dispatch ─────────────────────────────────────────────────

    # Default intent → preferred agent name mapping.
    # Mirrors lilith_orchestrator.graph.nodes._INTENT_AGENT_MAP.
    DEFAULT_INTENT_AGENT: dict[str, str] = {
        "code": "Odin",
        "research": "Mimir",
        "creative": "Eva",
        "chat": "Lilith",
        "debug": "Adan",
    }

    def select_agent(
        self,
        intent: str | None = None,
        required_tools: list[str] | None = None,
        prefer_level: int = 2,
    ) -> AgentCard | None:
        """Select the best agent for a task.

        Args:
            intent: One of "code", "research", "creative", "debug", "chat".
                    Maps to a preferred agent name (Odin/Mimir/Eva/Adan/Lilith).
            required_tools: If given, only agents that have ALL these tools
                            in their card are eligible.
            prefer_level: Preferred level (1 = consultant, 2 = executor).
                          If no agent at that level matches, fall back to
                          the other level.

        Returns:
            The selected AgentCard, or None if no agent matches.
        """
        candidates = list(self._cards)

        # Filter by required tools
        if required_tools:
            required_lower = [t.lower() for t in required_tools]
            candidates = [
                c for c in candidates
                if all(t in [x.lower() for x in c.tools] for t in required_lower)
            ]

        # Filter by intent
        if intent and intent in self.DEFAULT_INTENT_AGENT:
            preferred_name = self.DEFAULT_INTENT_AGENT[intent]
            preferred = next(
                (c for c in candidates if c.name.lower() == preferred_name.lower()),
                None,
            )
            if preferred:
                return preferred

        # Fall back to level preference
        level_matches = [c for c in candidates if c.level == prefer_level]
        if level_matches:
            return level_matches[0]
        # Try the other level
        other_level = 1 if prefer_level != 1 else 2
        level_matches = [c for c in candidates if c.level == other_level]
        if level_matches:
            return level_matches[0]

        # Last resort: any candidate
        return candidates[0] if candidates else None

    def stats(self) -> dict[str, Any]:
        """Return registry statistics."""
        return {
            "total": len(self._cards),
            "executors": len(self.executors()),
            "consultants": len(self.consultants()),
            "by_model": {m: len(self.by_model(m)) for m in {c.model for c in self._cards}},
            "agents": [c.name for c in self._cards],
        }
