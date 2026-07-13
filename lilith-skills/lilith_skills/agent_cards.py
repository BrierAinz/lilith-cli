"""Agent Card Loader — loads agent_cards.yaml and integrates with skill registry.

Loads standardized agent metadata from Vanaheim/Agents/agent_cards.yaml
and provides a registry for agent discovery, filtering, and tool assignment.
Inspired by Eter-Agents' agent card system.

Uses Pydantic for validation (Pydantic agent cards recommendation from
research/emerging-agents-2026-06-21.md, next-cycle priorities).
"""

from __future__ import annotations

from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pathlib import Path


# ── AgentCard (Pydantic) ────────────────────────────────────────


class AgentCard(BaseModel):
    """Represents a single agent card from agent_cards.yaml.

    Validated via Pydantic — unknown fields are ignored, missing
    required fields raise ValidationError.
    """

    name: str = Field(default="")
    role: str = Field(default="")
    level: int = Field(default=1, ge=0)
    model: str = Field(default="glm-5.2")
    tools: list[str] = Field(default_factory=list)
    description: str = Field(default="")
    hooks: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    @field_validator("name", "role", "model", "description", mode="before")
    @classmethod
    def _ensure_str(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    @field_validator("tools", "hooks", mode="before")
    @classmethod
    def _ensure_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        return list(v)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCard:
        """Create AgentCard from a dictionary (Pydantic validation)."""
        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "role": self.role,
            "level": self.level,
            "model": self.model,
            "tools": self.tools,
            "description": self.description,
            "hooks": self.hooks,
        }

    def is_executor(self) -> bool:
        """Check if this is an executor-level agent (level >= 2)."""
        return self.level >= 2

    def is_consultant(self) -> bool:
        """Check if this is a consultant-level agent (level == 1)."""
        return self.level == 1

    def __repr__(self) -> str:
        return f"AgentCard({self.name}, level={self.level}, model={self.model})"


class AgentCardLoader:
    """Loads and manages agent cards from Vanaheim.

    Usage::

        # Load from Vanaheim agent_cards.yaml
        loader = AgentCardLoader.from_vanaheim("/path/to/Yggdrasil")

        # Or directly
        loader = AgentCardLoader("/path/to/agent_cards.yaml")

        # Get all agents
        agents = loader.list_agents()

        # Get by name
        odin = loader.get_agent("Odin")

        # Filter by level
        executors = loader.by_level(2)

        # Filter by tool capability
        coders = loader.has_tool("terminal")

        # Get agents supporting a specific hook
        hooked = loader.by_hook("pre_tool_use")
    """

    def __init__(self, cards_path: Path | str) -> None:
        """Initialize loader with path to agent_cards.yaml.

        Args:
            cards_path: Path to agent_cards.yaml file.
        """
        self.cards_path = Path(cards_path).resolve()
        self._agents: list[AgentCard] = []
        self._by_name: dict[str, AgentCard] = {}
        self._load()

    @classmethod
    def from_vanaheim(cls, repo_root: Path | str) -> AgentCardLoader:
        """Create loader from Yggdrasil repo root.

        Automatically locates Vanaheim/Agents/agent_cards.yaml.

        Args:
            repo_root: Path to Yggdrasil repository root.

        Returns:
            AgentCardLoader initialized with Vanaheim cards.
        """
        repo = Path(repo_root).resolve()
        cards_path = repo / "Vanaheim" / "Agents" / "agent_cards.yaml"

        if not cards_path.is_file():
            raise FileNotFoundError(
                f"agent_cards.yaml not found at {cards_path}. "
                f"Ensure Vanaheim is properly initialized."
            )

        return cls(cards_path)

    def _load(self) -> None:
        """Load and parse agent_cards.yaml."""
        if not self.cards_path.is_file():
            return

        try:
            content = self.cards_path.read_text(encoding="utf-8")
        except OSError:
            return

        # Parse YAML multi-document (--- separated)
        try:
            docs = list(yaml.safe_load_all(content))
        except yaml.YAMLError:
            return

        self._agents = []
        self._by_name = {}

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if "name" not in doc:
                continue

            card = AgentCard.from_dict(doc)
            self._agents.append(card)
            self._by_name[card.name] = card

    def list_agents(self) -> list[AgentCard]:
        """Return all loaded agent cards."""
        return list(self._agents)

    def get_agent(self, name: str) -> AgentCard | None:
        """Get an agent by name (case-insensitive)."""
        name_lower = name.lower()
        for agent in self._agents:
            if agent.name.lower() == name_lower:
                return agent
        return None

    def by_level(self, level: int) -> list[AgentCard]:
        """Get all agents at a specific level.

        Args:
            level: 1 = consultant, 2 = executor

        Returns:
            List of matching agents.
        """
        return [a for a in self._agents if a.level == level]

    def has_tool(self, tool: str) -> list[AgentCard]:
        """Get all agents that have a specific tool in their allowed list.

        Args:
            tool: Tool name to filter by (case-insensitive).

        Returns:
            List of matching agents.
        """
        tool_lower = tool.lower()
        return [a for a in self._agents if tool_lower in [t.lower() for t in a.tools]]

    def by_hook(self, hook: str) -> list[AgentCard]:
        """Get all agents that register for a specific hook.

        Args:
            hook: Hook name to filter by (case-insensitive).

        Returns:
            List of matching agents.
        """
        hook_lower = hook.lower()
        return [a for a in self._agents if hook_lower in [h.lower() for h in a.hooks]]

    def by_role_keyword(self, keyword: str) -> list[AgentCard]:
        """Search agents by role keyword (case-insensitive).

        Args:
            keyword: Keyword to search in role description.

        Returns:
            List of matching agents.
        """
        keyword_lower = keyword.lower()
        return [
            a for a in self._agents
            if keyword_lower in a.role.lower()
        ]

    def executors(self) -> list[AgentCard]:
        """Get all executor-level agents (level >= 2)."""
        return [a for a in self._agents if a.is_executor()]

    def consultants(self) -> list[AgentCard]:
        """Get all consultant-level agents (level == 1)."""
        return [a for a in self._agents if a.is_consultant()]

    def stats(self) -> dict[str, Any]:
        """Return statistics about loaded agents."""
        return {
            "total_agents": len(self._agents),
            "consultants": len(self.consultants()),
            "executors": len(self.executors()),
            "agents": [a.name for a in self._agents],
        }

    def to_dict(self) -> dict[str, Any]:
        """Export all agents as a dictionary."""
        return {
            "source": str(self.cards_path),
            "agents": [a.to_dict() for a in self._agents],
        }
