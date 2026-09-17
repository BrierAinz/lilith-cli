from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry
from lilith_tools.orchestration_state import OrchestrationStateStore

from .court import CourtRegistry
from .skill_bench import SkillBench


@dataclass(frozen=True)
class RouteLearning:
    court_agent: str
    resource_id: str
    attempts: int
    successes: int
    failures: int
    success_rate: float
    repeated_failure: str | None
    avoid: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SkillVersionMetric:
    skill_name: str
    version_id: str
    active: bool
    source: str
    attempts: int
    successes: int
    failures: int
    success_rate: float
    avg_latency_ms: float
    total_tokens: int
    total_cost_usd: float
    rollback_count: int = 0
    success_delta: float | None = None
    regression: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SkillRegressionAction:
    skill_name: str
    current_version: str
    target_version: str | None
    success_drop: float
    rolled_back: bool
    new_version_id: str | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SkillPromotion:
    name: str
    court_agent: str
    success_count: int
    success_rate: float
    path: str | None
    promoted: bool
    version_id: str | None = None
    bench_passed: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_FAILURE_NOISE = re.compile(
    r"(?:[A-Z]:\\[^\s]+|/[^\s]+|\b\d+\b|[0-9a-f]{8,})",
    re.IGNORECASE,
)


def _failure_signature(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).lower()[:400]
    return _FAILURE_NOISE.sub("#", cleaned)


class LearningEngine:
    """Turn durable post-mortems into routing memory and safe skill candidates."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        skills_root: str | Path | None = None,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.skills_root = Path(skills_root).expanduser() if skills_root else None

    def _state(self) -> dict[str, Any]:
        return OrchestrationStateStore(self.state_path).get()

    @staticmethod
    def _rows(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [row for row in state.get("post_mortems", []) if isinstance(row, dict)]

    def route_learning(self) -> list[RouteLearning]:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in self._rows(self._state()):
            agent = str(row.get("preset") or "").strip().lower()
            resource = str(row.get("provider") or "").strip().lower()
            if CourtRegistry.get(agent) is None or not resource:
                continue
            groups[(CourtRegistry.get(agent).agent_id, resource)].append(row)
        learned: list[RouteLearning] = []
        for (agent, resource), rows in sorted(groups.items()):
            successes = sum(1 for row in rows if bool(row.get("success")))
            failures = len(rows) - successes
            signatures = [
                _failure_signature(str(row.get("cause") or ""))
                for row in rows
                if not bool(row.get("success")) and str(row.get("cause") or "").strip()
            ]
            repeated = None
            if len(signatures) >= 2 and signatures[-1] == signatures[-2]:
                repeated = signatures[-1]
            learned.append(RouteLearning(
                court_agent=agent,
                resource_id=resource,
                attempts=len(rows),
                successes=successes,
                failures=failures,
                success_rate=round(successes / len(rows), 4),
                repeated_failure=repeated,
                avoid=bool(repeated and failures >= 2),
            ))
        return learned

    def avoided_resources(self, court_agent: str) -> set[str]:
        resolved = CourtRegistry.get(court_agent)
        if resolved is None:
            return set()
        return {
            row.resource_id
            for row in self.route_learning()
            if row.court_agent == resolved.agent_id and row.avoid
        }

    def skill_candidates(
        self,
        *,
        min_successes: int = 3,
        min_success_rate: float = 0.8,
    ) -> list[SkillPromotion]:
        state = self._state()
        tasks = {str(t.get("id")): t for t in state.get("tasks", []) if isinstance(t, dict)}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._rows(state):
            agent = str(row.get("preset") or "").strip().lower()
            resolved = CourtRegistry.get(agent)
            if resolved is None or resolved.agent_id == "lilith":
                continue
            grouped[resolved.agent_id].append(row)

        candidates: list[SkillPromotion] = []
        for agent_id, rows in sorted(grouped.items()):
            successes = [row for row in rows if bool(row.get("success"))]
            rate = len(successes) / len(rows) if rows else 0.0
            distinct_tasks = {
                str(tasks.get(str(row.get("task_id")), {}).get("description") or "").strip()
                for row in successes
            } - {""}
            if len(successes) < min_successes or rate < min_success_rate:
                continue
            if len(distinct_tasks) < 2:
                continue
            candidates.append(SkillPromotion(
                name=f"{agent_id}-verified-pattern",
                court_agent=agent_id,
                success_count=len(successes),
                success_rate=round(rate, 4),
                path=None,
                promoted=False,
            ))
        return candidates

    def promote_safe_patterns(
        self,
        *,
        min_successes: int = 3,
        min_success_rate: float = 0.8,
    ) -> list[SkillPromotion]:
        registry = DelegationSkillRegistry(
            root=self.skills_root if self.skills_root is not None else None,
            seed_defaults=False,
        )
        promoted: list[SkillPromotion] = []
        for candidate in self.skill_candidates(
            min_successes=min_successes,
            min_success_rate=min_success_rate,
        ):
            if registry.get(candidate.name) is not None:
                promoted.append(candidate)
                continue
            agent = CourtRegistry.get(candidate.court_agent)
            if agent is None:
                continue
            skill = DelegationSkill(
                name=candidate.name,
                description=(
                    f"Patrón auto-promovido tras {candidate.success_count} ejecuciones "
                    f"verificadas de {agent.display_name}."
                ),
                preset=agent.agent_id,
                prompt_template=(
                    f"Actúa como {agent.display_name} ({agent.role}).\n"
                    "Tarea: {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}\n"
                    "Verifica el resultado y separa evidencia de inferencia."
                ),
                agentic=agent.can_execute,
                structured=False,
            )
            bench = SkillBench().run(skill)
            if not bench.passed:
                promoted.append(SkillPromotion(
                    name=candidate.name,
                    court_agent=candidate.court_agent,
                    success_count=candidate.success_count,
                    success_rate=candidate.success_rate,
                    path=None,
                    promoted=False,
                    bench_passed=False,
                ))
                continue
            version = registry.save_versioned(
                skill, source=f"auto-promotion:{candidate.court_agent}"
            )
            promoted.append(SkillPromotion(
                name=candidate.name,
                court_agent=candidate.court_agent,
                success_count=candidate.success_count,
                success_rate=candidate.success_rate,
                path=version.path,
                promoted=True,
                version_id=version.version_id,
                bench_passed=True,
            ))
        return promoted

    def skill_metrics(self, name: str = "") -> list[SkillVersionMetric]:
        registry = DelegationSkillRegistry(
            root=self.skills_root if self.skills_root is not None else None,
            seed_defaults=False,
        )
        post_mortems = [
            row for row in self._rows(self._state())
            if str(row.get("skill_name") or "").strip()
        ]
        names = {str(row.get("skill_name")) for row in post_mortems}
        names.update(skill.name for skill in registry.list())
        requested = str(name).strip()
        if requested:
            names = {requested}
        metrics: list[SkillVersionMetric] = []
        for skill_name in sorted(names):
            versions = sorted(registry.versions(skill_name), key=lambda row: row.created_at)
            rollback_count = sum(
                1 for row in versions if row.source.startswith("rollback:")
            )
            rows_by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in post_mortems:
                if str(row.get("skill_name")) != skill_name:
                    continue
                version_id = str(row.get("skill_version_id") or "").strip()
                if version_id:
                    rows_by_version[version_id].append(row)
            previous_rate: float | None = None
            previous_attempts = 0
            for version in versions:
                rows = rows_by_version.get(version.version_id, [])
                attempts = len(rows)
                successes = sum(1 for row in rows if bool(row.get("success")))
                failures = attempts - successes
                rate = round(successes / attempts, 4) if attempts else 0.0
                latency = [float(row.get("latency_ms", 0) or 0) for row in rows]
                avg_latency = round(sum(latency) / attempts, 2) if attempts else 0.0
                total_tokens = 0
                total_cost = 0.0
                for row in rows:
                    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
                    total_tokens += int(usage.get("total_tokens", 0) or 0)
                    total_cost += float(usage.get("cost_usd", usage.get("cost", 0.0)) or 0.0)
                delta = None
                regression = None
                if attempts and previous_rate is not None:
                    delta = round(rate - previous_rate, 4)
                    regression = bool(
                        attempts >= 2 and previous_attempts >= 2 and delta <= -0.15
                    )
                metrics.append(SkillVersionMetric(
                    skill_name=skill_name,
                    version_id=version.version_id,
                    active=version.active,
                    source=version.source,
                    attempts=attempts,
                    successes=successes,
                    failures=failures,
                    success_rate=rate,
                    avg_latency_ms=avg_latency,
                    total_tokens=total_tokens,
                    total_cost_usd=round(total_cost, 6),
                    rollback_count=rollback_count,
                    success_delta=delta,
                    regression=regression,
                ))
                if attempts:
                    previous_rate = rate
                    previous_attempts = attempts
        return metrics

    def auto_rollback_regressions(
        self, *, min_attempts: int = 3, min_success_drop: float = 0.20
    ) -> list[SkillRegressionAction]:
        registry = DelegationSkillRegistry(
            root=self.skills_root if self.skills_root is not None else None,
            seed_defaults=False,
        )
        grouped: dict[str, list[SkillVersionMetric]] = defaultdict(list)
        for metric in self.skill_metrics():
            grouped[metric.skill_name].append(metric)
        actions: list[SkillRegressionAction] = []
        for skill_name, metrics in grouped.items():
            active_index = next(
                (index for index, row in enumerate(metrics) if row.active), None
            )
            if active_index is None:
                continue
            current = metrics[active_index]
            if current.source.startswith("rollback:"):
                continue
            if current.attempts < min_attempts or current.failures < 2:
                continue
            target = next(
                (row for row in reversed(metrics[:active_index])
                 if row.attempts >= min_attempts),
                None,
            )
            if target is None:
                continue
            drop = round(target.success_rate - current.success_rate, 4)
            if drop < float(min_success_drop):
                continue
            try:
                candidate = registry.get_version(skill_name, target.version_id)
            except ValueError:
                continue
            bench = SkillBench().run(candidate)
            if not bench.passed:
                actions.append(SkillRegressionAction(
                    skill_name=skill_name,
                    current_version=current.version_id,
                    target_version=target.version_id,
                    success_drop=drop,
                    rolled_back=False,
                    new_version_id=None,
                    reason="previous version failed SkillBench; rollback blocked",
                ))
                continue
            restored = registry.rollback(
                skill_name,
                target.version_id,
                source=(
                    "rollback:auto-regression:"
                    f"{current.version_id}->{target.version_id}"
                ),
            )
            actions.append(SkillRegressionAction(
                skill_name=skill_name,
                current_version=current.version_id,
                target_version=target.version_id,
                success_drop=drop,
                rolled_back=True,
                new_version_id=restored.version_id,
                reason="verified regression exceeded automatic rollback threshold",
            ))
        return actions

    def public_summary(self) -> dict[str, object]:
        return {
            "routes": [row.as_dict() for row in self.route_learning()],
            "skill_candidates": [row.as_dict() for row in self.skill_candidates()],
            "skill_metrics": [row.as_dict() for row in self.skill_metrics()],
        }
