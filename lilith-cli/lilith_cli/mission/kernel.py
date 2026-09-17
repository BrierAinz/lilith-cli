from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .compiler import CompileResult
from .compute import ComputeBroker, ComputeRoute
from .compute_health import ComputeHealthResolver
from .court import CourtAgent, CourtRegistry
from .model import MissionSpec


@dataclass(frozen=True)
class MissionRegistration:
    mission_id: str
    task_id: str
    status: str
    run_id: str | None
    run_directory: str | None
    team: tuple[str, ...]
    compute: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class MissionKernel:
    """Bind mission intent to the existing durable orchestration and longrun engines."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        longrun_root: str | Path | None = None,
        court: type[CourtRegistry] = CourtRegistry,
        compute: ComputeBroker | None = None,
        health: ComputeHealthResolver | None = None,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else None
        env_longrun = os.environ.get("YGGDRASIL_LONGRUN_ROOT")
        self.longrun_root = (
            Path(longrun_root).expanduser()
            if longrun_root
            else Path(env_longrun).expanduser()
            if env_longrun
            else Path.home() / ".yggdrasil" / "long-runs"
        )
        self.court = court
        self.compute = compute or ComputeBroker()
        self.health = health or ComputeHealthResolver(
            broker=self.compute,
            state_path=self.state_path,
        )

    def _store(self):
        from lilith_tools.orchestration_state import OrchestrationStateStore

        return OrchestrationStateStore(self.state_path)

    def register(
        self,
        compiled: CompileResult,
        *,
        required_capabilities: set[str] | frozenset[str] = frozenset(),
        verify: str = "",
        healthy_compute: set[str] | None = None,
        lease_owner: str | None = None,
        lease_seconds: float = 300.0,
    ) -> MissionRegistration:
        mission = compiled.mission
        mission.validate()
        task_id = f"mission-{mission.mission_id[:12]}"
        store = self._store()
        store.set_plan(mission.objective, "Lilith Mission Kernel")

        team: list[CourtAgent] = []
        routes: dict[str, ComputeRoute] = {}
        if not compiled.needs_operator:
            team = self._compose_team(required_capabilities)
            routes = self._route_team(team, healthy_compute=healthy_compute)

        routing = {
            "kernel": "mission-v1",
            "mission_spec": mission.as_dict(),
            "team": [agent.as_dict() for agent in team],
            "compute": {
                agent_id: {
                    "resource_id": route.resource.resource_id,
                    "transport": route.resource.transport,
                    "adapter": route.resource.adapter,
                    "fallbacks": list(route.fallbacks),
                    "reason": route.reason,
                }
                for agent_id, route in routes.items()
            },
        }
        task = store.add_task(
            mission.objective,
            "Prime mission owned by Lilith.",
            task_id=task_id,
            status=(
                "bloqueada"
                if compiled.needs_operator or lease_owner
                else "pendiente"
            ),
            preset="lilith-mission",
            success_criteria=mission.success_criteria,
            budget=asdict(mission.budget),
            routing=routing,
            correlation_id=mission.mission_id,
            idempotency_key=f"mission:{mission.mission_id}",
        )
        if compiled.needs_operator:
            return MissionRegistration(
                mission.mission_id,
                task_id,
                str(task["status"]),
                None,
                None,
                (),
                {},
            )

        run_id, run_directory = self._create_longrun(mission, verify=verify)
        routing["longrun"] = {
            "run_id": run_id,
            "directory": str(run_directory),
        }
        store.update_task(task_id, routing=routing)
        final_status = "pendiente"
        if lease_owner:
            claimed = store.claim_task(
                task_id,
                str(lease_owner),
                lease_seconds=max(15.0, float(lease_seconds)),
            )
            final_status = str(claimed["status"])
        return MissionRegistration(
            mission.mission_id,
            task_id,
            final_status,
            run_id,
            str(run_directory),
            tuple(agent.agent_id for agent in team),
            {key: route.resource.resource_id for key, route in routes.items()},
        )

    def _create_longrun(self, mission: MissionSpec, *, verify: str) -> tuple[str, Path]:
        from ..longrun.contract import Budget, Consumed, RunContract

        max_legs = max(1, mission.budget.max_subagent_calls + 1)
        per_leg = max(1, min(24, mission.budget.max_iterations))
        budget = Budget(
            max_wall_seconds=mission.budget.max_wall_seconds,
            max_legs=max_legs,
            max_iterations_per_leg=per_leg,
            max_tokens=mission.budget.max_tokens,
            max_usd=mission.budget.max_usd,
        )
        contract = RunContract.create(
            goal=mission.objective,
            end_criterion="; ".join(mission.success_criteria),
            verify=verify,
            project_root=mission.project_root,
            budget=budget,
        )
        contract.validate()
        directory = self.longrun_root / contract.run_id
        directory.mkdir(parents=True, exist_ok=False)
        contract.save(directory)
        Consumed().save(directory)
        return contract.run_id, directory

    def _compose_team(self, required: set[str] | frozenset[str]) -> list[CourtAgent]:
        requested = set(required)
        require_executor = bool(
            requested & {"code", "code_write", "execute", "system", "admin", "automation"}
        )
        return self.court.choose(
            requested or {"plan", "review"},
            count=4,
            require_executor=require_executor,
            require_reviewer=True,
        )

    @staticmethod
    def compute_needs(agent: CourtAgent) -> set[str]:
        mapping = {
            "demiurge": {"analysis", "reasoning"},
            "pandora": {"analysis", "reasoning"},
            "sebas": {"analysis", "admin_reasoning"},
            "cocytus": {"code_write"},
            "shalltear": {"analysis", "review"},
            "aura": {"analysis", "research"},
            "mare": {"code_write", "batch"},
        }
        return mapping.get(agent.agent_id, {"analysis"})

    def _route_team(
        self,
        team: list[CourtAgent],
        *,
        healthy_compute: set[str] | None,
    ) -> dict[str, ComputeRoute]:
        routes: dict[str, ComputeRoute] = {}
        for agent in team:
            agent_healthy = (
                healthy_compute
                if healthy_compute is not None
                else self.health.healthy_ids(court_agent=agent.agent_id)
            )
            needs = self.compute_needs(agent)
            try:
                route = self.compute.route(needs, healthy=agent_healthy)
            except LookupError:
                configured = self.compute.route(needs)
                route = ComputeRoute(
                    configured.resource,
                    "DEGRADED PLAN: no healthy route at registration; "
                    + configured.reason,
                    configured.fallbacks,
                )
            routes[agent.agent_id] = route
        return routes

    @staticmethod
    def _run_verifier(task: dict[str, Any]) -> dict[str, Any] | None:
        routing = task.get("routing") or {}
        longrun = routing.get("longrun") or {}
        directory = str(longrun.get("directory") or "").strip()
        if not directory:
            return None
        from ..longrun.contract import RunContract
        from ..verification_input import verification_argv

        contract = RunContract.load(Path(directory))
        if not contract.verify.strip():
            return None
        root = Path(contract.project_root).expanduser().resolve()
        argv = verification_argv(contract.verify, root)
        result = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, min(600, int(contract.budget.max_wall_seconds))),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "verified": result.returncode == 0,
            "mode": "deterministic_command",
            "command": contract.verify,
            "exit_code": int(result.returncode),
            "stdout": (result.stdout or "")[-4000:],
            "stderr": (result.stderr or "")[-4000:],
        }

    @staticmethod
    def _compact_longrun_journal(task: dict[str, Any]) -> dict[str, Any]:
        routing = task.get("routing") or {}
        directory = str((routing.get("longrun") or {}).get("directory") or "").strip()
        if not directory:
            return {"compacted": False, "reason": "longrun_missing"}
        path = Path(directory) / "journal.jsonl"
        if not path.is_file():
            return {"compacted": False, "reason": "journal_missing"}
        try:
            from ..longrun.journal import Journal

            return Journal(path).compact_storage(max_entries=500, keep_recent=100)
        except (OSError, TypeError, ValueError) as exc:
            return {
                "compacted": False,
                "reason": "compaction_error",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def complete(
        self,
        task_id: str,
        *,
        success: bool,
        summary: str,
        verification: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        lessons: list[str] | None = None,
        next_missions: list[dict[str, Any]] | None = None,
        campaign_mode: bool = True,
    ) -> dict[str, Any]:
        store = self._store()
        state = store.get()
        current = next(
            (item for item in state.get("tasks", []) if item.get("id") == task_id),
            None,
        )
        if current is None:
            raise ValueError(f"task no encontrada: {task_id}")
        merged_verification = dict(verification or {})
        deterministic = self._run_verifier(current)
        if deterministic is not None:
            merged_verification["deterministic"] = deterministic
            merged_verification["verified"] = bool(deterministic["verified"])
            if success and not deterministic["verified"]:
                store.update_task(task_id, verification=merged_verification)
                raise ValueError(
                    "deterministic verification failed "
                    f"(exit {deterministic['exit_code']}); repair and retry mission_complete"
                )
        elif "verified" not in merged_verification:
            merged_verification["verified"] = bool(success)
            merged_verification["mode"] = "self_reported"
        status = "completada" if success else "fallida"
        task = store.update_task(
            task_id,
            status=status,
            result=summary,
            usage=usage or {},
            verification=merged_verification,
        )
        post_mortem = store.append_post_mortem({
            "task_id": task_id,
            "correlation_id": task.get("correlation_id"),
            "preset": "lilith-mission",
            "success": success,
            "summary": summary,
            "lessons": list(lessons or []),
            "verification": merged_verification,
        })
        journal_compaction = self._compact_longrun_journal(task)
        routing = dict(task.get("routing") or {})
        routing["journal_compaction"] = journal_compaction
        task = store.update_task(task_id, routing=routing)
        from .learning import LearningEngine

        learning = LearningEngine(state_path=self.state_path)
        promoted = learning.promote_safe_patterns()
        regressions = learning.auto_rollback_regressions()
        campaign: dict[str, Any] = {"created": [], "skipped": []}
        if campaign_mode:
            from .campaign import CampaignEngine, CampaignRecommendation

            engine = CampaignEngine(
                state_path=self.state_path,
                longrun_root=self.longrun_root,
            )
            if success and next_missions:
                rows: list[CampaignRecommendation] = []
                for item in next_missions[:3]:
                    if not isinstance(item, dict):
                        continue
                    rows.append(CampaignRecommendation(
                        objective=str(item.get("objective") or "").strip(),
                        success_criteria=tuple(str(v) for v in item.get("success_criteria") or ()),
                        reason=str(item.get("reason") or "follow-up from completed mission"),
                        capabilities=tuple(str(v) for v in item.get("capabilities") or ()),
                        priority=int(item.get("priority", 50)),
                        action=str(item.get("action") or "file_edit"),
                    ))
                rows = [row for row in rows if row.objective and row.success_criteria]
                if rows:
                    campaign = engine.chain(task_id, rows, max_children=3)
            elif not success and not (verification or {}).get("requires_operator"):
                if (verification or {}).get("recoverable", True):
                    campaign = engine.repair_failed(task_id, summary=summary)
        return {
            "task": task,
            "post_mortem": post_mortem,
            "journal_compaction": journal_compaction,
            "learning_promotions": [row.as_dict() for row in promoted],
            "skill_regression_actions": [row.as_dict() for row in regressions],
            "campaign": campaign,
        }

    def status(self, mission_id: str) -> dict[str, Any] | None:
        state = self._store().get()
        for task in state.get("tasks", []):
            if task.get("correlation_id") == mission_id:
                return task
        return None

    def resume_expired(self) -> list[dict[str, Any]]:
        return self._store().resume_expired()


    def list_missions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        state = self._store().get()
        rows = [
            task for task in state.get("tasks", [])
            if task.get("preset") == "lilith-mission"
        ]
        rows.sort(key=lambda task: str(task.get("updated_at") or ""), reverse=True)
        return rows[: max(1, min(100, int(limit)))]
