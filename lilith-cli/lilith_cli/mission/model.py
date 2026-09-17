from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class MissionBudget:
    max_wall_seconds: int = 14_400
    max_iterations: int = 48
    max_subagent_calls: int = 12
    max_tokens: int | None = None
    max_usd: float | None = None

    def validate(self) -> None:
        if self.max_wall_seconds < 60:
            raise ValueError("max_wall_seconds must be >= 60")
        if self.max_iterations < 1 or self.max_subagent_calls < 0:
            raise ValueError("mission iteration/subagent budgets are invalid")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive when set")
        if self.max_usd is not None and self.max_usd <= 0:
            raise ValueError("max_usd must be positive when set")


@dataclass(frozen=True)
class MissionQuestion:
    question: str
    options: tuple[str, ...]
    reason: str
    blocking: bool = True
    recommended_option: int | None = None


@dataclass(frozen=True)
class MissionRoute:
    route_id: str
    label: str
    rationale: str
    expected_strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    estimated_cost: str = "unknown"


@dataclass
class MissionSpec:
    objective: str
    project_root: str
    success_criteria: list[str]
    constraints: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    questions: list[MissionQuestion] = field(default_factory=list)
    routes: list[MissionRoute] = field(default_factory=list)
    selected_route: str | None = None
    authority_profile: str = "sovereign-local"
    budget: MissionBudget = field(default_factory=MissionBudget)
    mission_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "draft"
    created_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.objective.strip():
            raise ValueError("mission objective cannot be empty")
        root = Path(self.project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project root does not exist: {root}")
        if not self.success_criteria:
            raise ValueError("mission requires at least one success criterion")
        self.budget.validate()
        if self.selected_route and self.selected_route not in {r.route_id for r in self.routes}:
            raise ValueError("selected_route is not in candidate routes")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
