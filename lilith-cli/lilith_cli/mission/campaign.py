from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lilith_tools.orchestration_state import OrchestrationStateStore

from .authority import AuthorityPolicy
from .compiler import MissionCompiler
from .model import MissionBudget


@dataclass(frozen=True)
class CampaignRecommendation:
    objective: str
    success_criteria: tuple[str, ...]
    reason: str
    capabilities: tuple[str, ...] = ()
    priority: int = 50
    action: str = "file_edit"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CampaignEngine:
    """Create bounded descendant missions without inventing a second scheduler."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        longrun_root: str | Path | None = None,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.longrun_root = Path(longrun_root).expanduser() if longrun_root else None

    def _store(self) -> OrchestrationStateStore:
        return OrchestrationStateStore(self.state_path)

    @staticmethod
    def _find_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
        task = next(
            (row for row in state.get("tasks", []) if row.get("id") == task_id),
            None,
        )
        if task is None:
            raise ValueError(f"parent mission not found: {task_id}")
        return task

    @staticmethod
    def _child_budget(raw: dict[str, Any]) -> MissionBudget:
        return MissionBudget(
            max_wall_seconds=max(900, min(int(raw.get("max_wall_seconds", 14400)), 7200)),
            max_iterations=max(8, min(int(raw.get("max_iterations", 48)), 24)),
            max_subagent_calls=max(2, min(int(raw.get("max_subagent_calls", 12)), 6)),
            max_tokens=(
                max(1024, int(raw["max_tokens"]) // 2)
                if raw.get("max_tokens") is not None else None
            ),
            max_usd=(
                max(0.01, float(raw["max_usd"]) / 2)
                if raw.get("max_usd") is not None else None
            ),
        )

    @staticmethod
    def _child_id(parent_task_id: str, objective: str) -> str:
        raw = f"{parent_task_id}\n{objective.strip().casefold()}".encode()
        return hashlib.sha256(raw).hexdigest()

    def chain(
        self,
        parent_task_id: str,
        recommendations: list[CampaignRecommendation],
        *,
        max_children: int = 3,
    ) -> dict[str, Any]:
        state = self._store().get()
        parent = self._find_task(state, parent_task_id)
        routing = parent.get("routing") or {}
        spec = routing.get("mission_spec") or {}
        root = str(spec.get("project_root") or "").strip()
        if not root:
            raise ValueError("parent mission has no project_root")
        if parent.get("preset") != "lilith-mission":
            raise ValueError("campaign chaining requires a prime Lilith mission")

        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        ordered = sorted(recommendations, key=lambda row: (-row.priority, row.objective))
        for recommendation in ordered[: max(0, min(10, int(max_children)))]:
            decision = AuthorityPolicy().evaluate(
                recommendation.action,
                path=root,
                recovery_available=True,
            )
            if not decision.allowed or decision.requires_operator:
                skipped.append({
                    "objective": recommendation.objective,
                    "reason": decision.reason,
                    "requires_operator": decision.requires_operator,
                })
                continue
            child_id = self._child_id(parent_task_id, recommendation.objective)
            existing = next(
                (row for row in state.get("tasks", []) if row.get("correlation_id") == child_id),
                None,
            )
            if existing is not None:
                created.append({"deduplicated": True, "task": existing})
                continue
            compiled = MissionCompiler().compile(
                objective=recommendation.objective,
                project_root=root,
                success_criteria=recommendation.success_criteria,
                constraints=("Descendant of " + parent_task_id,),
                assumptions=(recommendation.reason,),
                budget=self._child_budget(dict(spec.get("budget") or {})),
            )
            compiled.mission.mission_id = child_id
            parent_meta = dict(spec.get("metadata") or {})
            repair_depth = int(parent_meta.get("repair_depth", 0))
            compiled.mission.metadata.update({
                "campaign_mode": True,
                "parent_task_id": parent_task_id,
                "reason": recommendation.reason,
                "priority": int(recommendation.priority),
                "repair_depth": repair_depth + int(
                    recommendation.objective.startswith("Repair failed mission:")
                ),
            })
            from .kernel import MissionKernel

            registration = MissionKernel(
                state_path=self.state_path,
                longrun_root=self.longrun_root,
            ).register(
                compiled,
                required_capabilities=set(recommendation.capabilities),
            )
            created.append({
                "deduplicated": False,
                "recommendation": recommendation.as_dict(),
                "registration": registration.as_dict(),
            })
            state = self._store().get()
        return {
            "parent_task_id": parent_task_id,
            "created": created,
            "skipped": skipped,
        }

    def repair_failed(
        self,
        parent_task_id: str,
        *,
        summary: str,
    ) -> dict[str, Any]:
        state = self._store().get()
        parent = self._find_task(state, parent_task_id)
        spec = (parent.get("routing") or {}).get("mission_spec") or {}
        metadata = dict(spec.get("metadata") or {})
        depth = int(metadata.get("repair_depth", 0))
        if depth >= 2:
            return {
                "parent_task_id": parent_task_id,
                "created": [],
                "skipped": [{"reason": "repair depth limit reached", "depth": depth}],
            }
        criteria = tuple(str(item) for item in spec.get("success_criteria") or ())
        criteria += ("The previous failure no longer reproduces.",)
        recommendation = CampaignRecommendation(
            objective=f"Repair failed mission: {parent.get('title') or parent_task_id}",
            success_criteria=criteria,
            reason=summary,
            capabilities=("plan", "code_write", "review"),
            priority=100,
            action="file_edit",
        )
        return self.chain(parent_task_id, [recommendation], max_children=1)
