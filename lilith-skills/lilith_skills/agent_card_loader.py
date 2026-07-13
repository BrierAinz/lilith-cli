"""Agent Card Loader — loads agent metadata from Vanaheim agent_cards.yaml.

Inspired by Eter-Agents' agent card system: standardized YAML metadata for agents.
This module parses Vanaheim/Agents/agent_cards.yaml and provides:
    - AgentCard dataclass: standardized metadata for each agent
    - AgentCardLoader: loads and manages agent cards
    - AgentRegistry: search and retrieve agents by role, level, or capability

Usage:
    loader = AgentCardLoader.from_vanaheim("/path/to/Yggdrasil")
    for card in loader.list_agents():
        print(card.name, card.role)

    registry = loader.get_registry()
    executors = registry.by_level(2)  # All level 2 (executor) agents
"""

from __future__ import annotations

import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Default path within Yggdrasil monorepo
_DEFAULT_AGENT_CARDS_REL = "Vanaheim/Agents/agent_cards.yaml"


@dataclass
class AgentCard:
    """Standardized metadata for an agent.

    Attributes:
        name: Display name of the agent (e.g., "Odin", "Heimdall")
        role: Functional role in the ecosystem (e.g., "Allfather — Strategist & Oracle")
        level: Agent level - 1 = consultant (on-demand), 2 = executor (active)
        model: Preferred LLM model
        tools: List of allowed tools (for tool isolation)
        description: Human-readable description
        hooks: Optional hook registrations (lilith-core hooks system)
        capabilities: Derived list of capabilities from tools + role
    """

    name: str
    role: str
    level: int
    model: str
    tools: list[str] = field(default_factory=list)
    description: str = ""
    hooks: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Derive capabilities from role and tools."""
        if not self.capabilities:
            # Extract key capabilities from role description
            capability_keywords = {
                "strategic": ["strategy", "planning", "oracle"],
                "research": ["research", "knowledge", "memory"],
                "building": ["build", "scaffold", "infrastructure"],
                "improving": ["improve", "refactor", "optimize", "quality"],
                "executing": ["execute", "fast", "implement"],
                "security": ["security", "audit", "watchman", "gatekeeper"],
                "coordination": ["orchestrat", "coordinat", "manage"],
            }

            role_lower = self.role.lower()
            desc_lower = self.description.lower()
            combined = f"{role_lower} {desc_lower}"

            for cap, keywords in capability_keywords.items():
                if any(kw in combined for kw in keywords):
                    self.capabilities.append(cap)

            # Add tool-based capabilities
            tool_caps = {
                "terminal": "execution",
                "write_file": "creation",
                "read_file": "reading",
                "search_files": "searching",
                "web_search": "research",
                "session_search": "memory",
                "patch": "modification",
            }

            for tool in self.tools:
                if tool in tool_caps and tool_caps[tool] not in self.capabilities:
                    self.capabilities.append(tool_caps[tool])

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "role": self.role,
            "level": self.level,
            "model": self.model,
            "tools": self.tools,
            "description": self.description,
            "hooks": self.hooks,
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCard:
        """Create an AgentCard from a dictionary."""
        return cls(
            name=data["name"],
            role=data["role"],
            level=data.get("level", 1),
            model=data.get("model", "unknown"),
            tools=data.get("tools", []),
            description=data.get("description", ""),
            hooks=data.get("hooks", {}),
            capabilities=data.get("capabilities", []),
        )


class AgentCardLoader:
    """Loads and manages agent cards from YAML.

    Supports multi-document YAML format (--- separated).
    """

    def __init__(self, cards: list[AgentCard]) -> None:
        self._cards = cards
        self._by_name: dict[str, AgentCard] = {card.name: card for card in cards}
        self._by_level: dict[int, list[AgentCard]] = {}
        self._by_capability: dict[str, list[AgentCard]] = {}

        # Index by level
        for card in cards:
            if card.level not in self._by_level:
                self._by_level[card.level] = []
            self._by_level[card.level].append(card)

        # Index by capability
        for card in cards:
            for cap in card.capabilities:
                if cap not in self._by_capability:
                    self._by_capability[cap] = []
                self._by_capability[cap].append(card)

    @classmethod
    def from_yaml(cls, path: Path | str) -> AgentCardLoader:
        """Load agent cards from a YAML file.

        Args:
            path: Path to agent_cards.yaml

        Returns:
            AgentCardLoader with loaded cards

        Raises:
            FileNotFoundError: If the YAML file doesn't exist
            ValueError: If the YAML is malformed
        """
        yaml_path = Path(path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Agent cards not found: {path}")

        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse multi-document YAML
        documents = list(yaml.safe_load_all(content))
        cards: list[AgentCard] = []

        for doc in documents:
            if doc and "name" in doc:
                cards.append(AgentCard.from_dict(doc))

        return cls(cards)

    @classmethod
    def from_vanaheim(cls, repo_root: Path | str) -> AgentCardLoader:
        """Load agent cards from the Vanaheim realm of a Yggdrasil repo.

        Args:
            repo_root: Path to the Yggdrasil monorepo root

        Returns:
            AgentCardLoader with loaded cards

        Raises:
            FileNotFoundError: If agent_cards.yaml doesn't exist
        """
        repo = Path(repo_root).resolve()
        cards_path = repo / _DEFAULT_AGENT_CARDS_REL

        if not cards_path.is_file():
            # Try alternate location
            alt_path = repo / "Vanaheim" / "Agents" / "agent_cards.yaml"
            if alt_path.is_file():
                cards_path = alt_path
            else:
                raise FileNotFoundError(
                    f"Agent cards not found at {cards_path} or {alt_path}"
                )

        return cls.from_yaml(cards_path)

    def list_agents(self) -> list[AgentCard]:
        """Return all loaded agent cards."""
        return list(self._cards)

    def get(self, name: str) -> AgentCard | None:
        """Get an agent card by name."""
        return self._by_name.get(name)

    def by_level(self, level: int) -> list[AgentCard]:
        """Get all agents at a specific level."""
        return self._by_level.get(level, [])

    def by_capability(self, capability: str) -> list[AgentCard]:
        """Get all agents with a specific capability."""
        return self._by_capability.get(capability.lower(), [])

    def by_tool(self, tool: str) -> list[AgentCard]:
        """Get all agents that have access to a specific tool."""
        return [c for c in self._cards if tool in c.tools]

    def search(self, query: str, limit: int = 10) -> list[AgentCard]:
        """Search agents by name, role, description, or capability.

        Args:
            query: Search string (case-insensitive)
            limit: Maximum results to return

        Returns:
            Ranked list of matching agents
        """
        query_lower = query.lower()
        scored: list[tuple[int, AgentCard]] = []

        for card in self._cards:
            score = 0

            # Name match (highest priority)
            if query_lower in card.name.lower():
                score += 20
            if card.name.lower() == query_lower:
                score += 30

            # Role match
            if query_lower in card.role.lower():
                score += 10

            # Description match
            if query_lower in card.description.lower():
                score += 5

            # Capability match
            for cap in card.capabilities:
                if query_lower in cap.lower():
                    score += 8

            # Tool match
            for tool in card.tools:
                if query_lower in tool.lower():
                    score += 3

            if score > 0:
                scored.append((score, card))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [card for _, card in scored[:limit]]

    def get_registry(self) -> AgentRegistry:
        """Return an AgentRegistry for this loader's cards."""
        return AgentRegistry(self._cards)

    def stats(self) -> dict[str, Any]:
        """Return statistics about loaded agents."""
        return {
            "total_agents": len(self._cards),
            "by_level": {level: len(agents) for level, agents in self._by_level.items()},
            "capabilities": {
                cap: len(agents)
                for cap, agents in self._by_capability.items()
            },
            "all_tools": list(
                set(tool for card in self._cards for tool in card.tools)
            ),
        }


class AgentRegistry:
    """Registry for searching and retrieving agents by various criteria."""

    def __init__(self, cards: list[AgentCard]) -> None:
        self._cards = cards

    def list_all(self) -> list[AgentCard]:
        """Return all agents."""
        return list(self._cards)

    def by_level(self, level: int) -> list[AgentCard]:
        """Get all agents at a specific level (1=consultant, 2=executor)."""
        return [c for c in self._cards if c.level == level]

    def by_capability(self, capability: str) -> list[AgentCard]:
        """Get all agents with a specific capability."""
        cap_lower = capability.lower()
        return [c for c in self._cards if cap_lower in [cap.lower() for cap in c.capabilities]]

    def by_tool(self, tool: str) -> list[AgentCard]:
        """Get all agents that have access to a specific tool."""
        return [c for c in self._cards if tool in c.tools]

    def by_model(self, model: str) -> list[AgentCard]:
        """Get all agents using a specific model."""
        return [c for c in self._cards if c.model == model]

    def consultants(self) -> list[AgentCard]:
        """Get all level 1 (consultant) agents."""
        return self.by_level(1)

    def executors(self) -> list[AgentCard]:
        """Get all level 2 (executor) agents."""
        return self.by_level(2)

    def find_for_task(self, task: str) -> list[AgentCard]:
        """Find the best agents for a given task description.

        Args:
            task: Natural language task description

        Returns:
            Ranked list of suitable agents
        """
        # Simple keyword matching for task types
        task_lower = task.lower()
        scores: list[tuple[int, AgentCard]] = []

        task_keywords = {
            "strategic": ["strategy", "plan", "roadmap", "vision", "decision"],
            "research": ["research", "find", "search", "investigate", "learn"],
            "build": ["build", "create", "scaffold", "new", "project"],
            "improve": ["improve", "refactor", "optimize", "enhance", "fix"],
            "execute": ["execute", "run", "implement", "do", "quick"],
            "audit": ["audit", "security", "review", "check", "vulnerability"],
        }

        for card in self._cards:
            score = 0
            role_desc = f"{card.role} {card.description}".lower()

            for task_type, keywords in task_keywords.items():
                if any(kw in task_lower for kw in keywords):
                    if any(kw in role_desc for kw in keywords):
                        score += 10

            # Prefer executors for most tasks unless it's a high-level decision
            if card.level == 2:
                score += 2
            elif card.level == 1:
                score += 1

            if score > 0:
                scores.append((score, card))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [card for _, card in scores]
