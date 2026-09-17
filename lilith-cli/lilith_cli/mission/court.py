from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import ClassVar


@dataclass(frozen=True)
class CourtAgent:
    agent_id: str
    display_name: str
    role: str
    mission: str
    capabilities: frozenset[str]
    can_execute: bool = False
    can_review: bool = False
    can_admin: bool = False
    preferred_parallelism: int = 1

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["capabilities"] = sorted(self.capabilities)
        return data


class CourtRegistry:
    """Canonical Overlord court. Roles are stable; compute backends are not."""

    QUEEN = CourtAgent(
        "lilith",
        "Lilith",
        "Queen / Prime Orchestrator",
        "Own the mission, choose the route, assemble the court and decide synthesis.",
        frozenset({"orchestrate", "synthesize", "route", "question", "verify", "learn"}),
        can_execute=True,
        can_review=True,
        can_admin=True,
        preferred_parallelism=4,
    )

    AGENTS: ClassVar[dict[str, CourtAgent]] = {
        "demiurge": CourtAgent(
            "demiurge", "Demiurge", "Strategist / Planner",
            "Explore competing plans, dependencies, risks and mission decomposition.",
            frozenset({"plan", "architecture", "reasoning", "risk", "decompose"}),
            can_review=True,
        ),
        "pandora": CourtAgent(
            "pandora", "Pandora's Actor", "Adaptive Router / Specialist",
            "Match task shape to tools, models, skills and fallback routes.",
            frozenset({"route", "adapt", "tool_selection", "model_selection", "fallback"}),
            can_review=True,
        ),
        "sebas": CourtAgent(
            "sebas", "Sebas", "PC Operator / Administrator",
            "Execute computer, shell and system operations with recovery and audit.",
            frozenset({"execute", "computer_use", "system", "admin", "powershell", "windows"}),
            can_execute=True,
            can_admin=True,
        ),
        "cocytus": CourtAgent(
            "cocytus", "Cocytus", "Engineering / Implementation",
            "Implement, debug, refactor and test code inside the mission scope.",
            frozenset({"code", "debug", "refactor", "test", "build", "git"}),
            can_execute=True,
        ),
        "shalltear": CourtAgent(
            "shalltear", "Shalltear", "Security / Adversarial Review",
            "Attack assumptions, review diffs and surface security or scope failures.",
            frozenset({"security", "review", "adversarial", "audit", "diff"}),
            can_review=True,
        ),
        "aura": CourtAgent(
            "aura", "Aura", "Research / Discovery",
            "Search code, web and evidence; separate verified facts from inference.",
            frozenset({"research", "web", "search", "discovery", "evidence"}),
        ),
        "mare": CourtAgent(
            "mare", "Mare", "Batch / Automation / Data",
            "Handle bounded batch work, transformations, data and repetitive automation.",
            frozenset({"batch", "automation", "data", "conversion", "boilerplate"}),
            can_execute=True,
            preferred_parallelism=2,
        ),
    }

    LEGACY_ALIASES: ClassVar[dict[str, str]] = {
        "batch-deepseek": "mare",
        "grok-research": "aura",
        "revisor-deepseek": "shalltear",
        "ratatoskr": "pandora",
        "vor": "cocytus",
        "vor2": "cocytus",
        "huginn": "mare",
        "muninn": "sebas",
        "kvasir": "aura",
    }

    @classmethod
    def get(cls, name: str) -> CourtAgent | None:
        key = str(name).strip().lower()
        if key == "lilith":
            return cls.QUEEN
        key = cls.LEGACY_ALIASES.get(key, key)
        return cls.AGENTS.get(key)

    @classmethod
    def list_agents(cls, *, include_queen: bool = False) -> list[CourtAgent]:
        rows = list(cls.AGENTS.values())
        return ([cls.QUEEN] + rows) if include_queen else rows

    @classmethod
    def choose(
        cls,
        required: set[str] | frozenset[str],
        *,
        count: int = 3,
        require_executor: bool = False,
        require_reviewer: bool = False,
    ) -> list[CourtAgent]:
        wanted = {str(item).strip().lower() for item in required if str(item).strip()}
        scored: list[tuple[int, str, CourtAgent]] = []
        for agent in cls.AGENTS.values():
            overlap = len(wanted & agent.capabilities)
            bonus = int(require_executor and agent.can_execute) + int(
                require_reviewer and agent.can_review
            )
            scored.append((overlap * 10 + bonus, agent.agent_id, agent))
        scored.sort(key=lambda row: (-row[0], row[1]))
        selected = [agent for score, _, agent in scored if score > 0][: max(1, count)]
        if require_executor and not any(item.can_execute for item in selected):
            selected.append(cls.AGENTS["cocytus"])
        if require_reviewer and not any(item.can_review for item in selected):
            selected.append(cls.AGENTS["shalltear"])
        return list(dict.fromkeys(selected))

    @classmethod
    def migration_map(cls) -> dict[str, str]:
        return dict(cls.LEGACY_ALIASES)
