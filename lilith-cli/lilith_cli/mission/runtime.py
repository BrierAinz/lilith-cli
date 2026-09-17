from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lilith_tools.orchestration_state import OrchestrationStateStore

from ..agent_modes import tool_capability
from .budget_guard import AggregateBudgetGuard, campaign_id_for_task
from .kernel import MissionKernel


@dataclass(frozen=True)
class CampaignRunResult:
    owner: str
    processed: int
    completed: int
    blocked: int
    released: int
    errors: tuple[str, ...]


@dataclass
class MissionExecution:
    task_id: str
    mission_id: str
    completed: bool = False
    mutating_effect_started: bool = False
    lease_lost: bool = False
    budget_exhausted: bool = False
    error: str | None = None


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class AutonomousCampaignRuntime:
    """Lease-based supervisor for durable Lilith campaign missions."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        longrun_root: str | Path | None = None,
        owner: str | None = None,
        lease_seconds: float = 180.0,
        max_missions: int = 5,
        max_wall_seconds: float = 14_400.0,
        session_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        raw_state = (
            state_path
            or os.environ.get("YGGDRASIL_ORCHESTRATION_STATE")
            or Path.home() / ".yggdrasil" / "orchestration_state.sqlite3"
        )
        raw_longrun = (
            longrun_root
            or os.environ.get("YGGDRASIL_LONGRUN_ROOT")
            or Path.home() / ".yggdrasil" / "long-runs"
        )
        self.state_path = Path(raw_state).expanduser().resolve()
        self.longrun_root = Path(raw_longrun).expanduser().resolve()
        self.owner = owner or (
            f"lilith-campaign:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.lease_seconds = max(15.0, float(lease_seconds))
        self.max_missions = max(1, min(100, int(max_missions)))
        self.max_wall_seconds = max(60.0, float(max_wall_seconds))
        self.session_factory = session_factory or self._default_session

    def _store(self) -> OrchestrationStateStore:
        return OrchestrationStateStore(self.state_path)

    def _kernel(self) -> MissionKernel:
        return MissionKernel(
            state_path=self.state_path,
            longrun_root=self.longrun_root,
        )

    def _campaign_id(self, task: dict[str, Any]) -> str:
        return campaign_id_for_task(self._store(), task)

    def _aggregate_guard(self, session: Any) -> AggregateBudgetGuard | None:
        config = getattr(session, "config", None)
        policy = getattr(config, "campaign_budgets", None)
        if policy is None:
            return None
        return AggregateBudgetGuard(policy=policy, state_path=str(self.state_path))

    def _record_primary_usage(self, session: Any, campaign_id: str) -> None:
        usage = dict(getattr(session, "total_usage", {}) or {})
        per_model = getattr(session, "per_model_usage", {}) or {}
        cost = sum(
            float(row.get("cost", 0.0) or 0.0)
            for row in per_model.values()
            if isinstance(row, dict)
        )
        total_tokens = int(usage.get("total_tokens", 0) or 0)
        if total_tokens <= 0 and cost <= 0:
            return
        usage["cost_usd"] = cost
        provider = str(getattr(getattr(session, "config", None), "provider", "lilith-primary"))
        self._store().record_cost(
            "lilith", provider, usage, session_id=campaign_id or "default"
        )

    @staticmethod
    def _metadata(task: dict[str, Any]) -> dict[str, Any]:
        return dict(((task.get("routing") or {}).get("mission_spec") or {}).get("metadata") or {})

    @classmethod
    def _eligible(cls, task: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> bool:
        if task.get("preset") != "lilith-mission" or task.get("status") != "pendiente":
            return False
        metadata = cls._metadata(task)
        parent_id = str(metadata.get("parent_task_id") or "").strip()
        if not parent_id:
            return True
        parent = tasks.get(parent_id)
        if parent is None:
            return False
        repair_depth = int(metadata.get("repair_depth", 0) or 0)
        if repair_depth > 0:
            return parent.get("status") == "fallida"
        return parent.get("status") == "completada"

    def next_eligible(
        self, *, exclude_task_ids: set[str] | None = None
    ) -> dict[str, Any] | None:
        state = self._store().get()
        tasks = {str(row.get("id")): row for row in state.get("tasks", []) if row.get("id")}
        excluded = set(exclude_task_ids or ())
        eligible = [
            row for row in tasks.values()
            if self._eligible(row, tasks) and str(row.get("id")) not in excluded
        ]
        eligible.sort(
            key=lambda row: (
                -int(self._metadata(row).get("priority", 50) or 50),
                str(row.get("created_at") or ""),
                str(row.get("id") or ""),
            )
        )
        return eligible[0] if eligible else None

    @staticmethod
    def _default_session(root: Path):
        from ..agent_console import prepare_agent_runtime
        from ..config import load_config
        from ..session_runtime import create_session

        cfg = load_config()
        session = create_session(cfg)
        prepare_agent_runtime(session, root)
        return session

    @staticmethod
    def _mission_prompt(task: dict[str, Any]) -> str:
        routing = task.get("routing") or {}
        spec = routing.get("mission_spec") or {}
        criteria = "\n".join(f"- {item}" for item in spec.get("success_criteria") or [])
        constraints = "\n".join(f"- {item}" for item in spec.get("constraints") or []) or "- none"
        return (
            "Resume an already registered Lilith mission. DO NOT call mission_prepare again.\n"
            f"task_id: {task.get('id')}\n"
            f"mission_id: {task.get('correlation_id')}\n"
            f"objective: {task.get('title')}\n"
            f"project_root: {spec.get('project_root')}\n"
            f"selected_route: {spec.get('selected_route') or 'none'}\n\n"
            f"Success criteria:\n{criteria}\n\nConstraints:\n{constraints}\n\n"
            "Inspect current evidence and checkpoints before acting. Continue autonomously inside "
            "the registered authority and budget. If a tool or verifier fails, diagnose, repair, "
            "and retry without asking the operator unless the authority gate explicitly requires it. "
            "Use mission_complete with the exact task_id only after criteria are verified. "
            "Do not bypass external/irreversible gates and do not create another prime mission."
        )

    @staticmethod
    def _result_effects_unknown(event: dict[str, Any]) -> bool:
        import json

        content = str(event.get("content") or "").strip()
        if content.startswith("Error: "):
            content = content[7:].strip()
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("effects_unknown") is True:
            return True
        recovery = payload.get("recovery")
        return isinstance(recovery, dict) and recovery.get("effects_unknown") is True

    async def _heartbeat(
        self,
        store: OrchestrationStateStore,
        task_id: str,
        session: Any,
        stop: asyncio.Event,
        execution: MissionExecution,
        *,
        heartbeat_seconds: float,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(
                    store.renew_lease,
                    task_id,
                    self.owner,
                    lease_seconds=self.lease_seconds,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                # mission_complete deletes the lease when it makes the task terminal.
                # A heartbeat racing just behind that commit is normal completion,
                # not dual ownership.
                try:
                    snapshot = await asyncio.to_thread(store.get)
                    current = next(
                        (row for row in snapshot.get("tasks", []) if row.get("id") == task_id),
                        None,
                    )
                except (OSError, ValueError, RuntimeError):
                    current = None
                if current and current.get("status") in {"completada", "fallida", "cancelada"}:
                    stop.set()
                    return
                execution.lease_lost = True
                execution.error = f"lease lost: {type(exc).__name__}: {exc}"
                cancel = getattr(session, "cancel", None)
                if callable(cancel):
                    cancel()
                stop.set()
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_seconds)
                return
            except TimeoutError:
                continue

    async def _execute_claimed(
        self,
        task: dict[str, Any],
        *,
        heartbeat_seconds: float,
        wall_seconds: float,
    ) -> MissionExecution:
        routing = task.get("routing") or {}
        spec = routing.get("mission_spec") or {}
        root = Path(str(spec.get("project_root") or "")).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"mission project_root missing: {root}")
        session = self.session_factory(root)
        budget = dict(task.get("budget") or {})
        config = getattr(session, "config", None)
        if config is not None:
            max_iterations = int(budget.get("max_iterations", 0) or 0)
            if max_iterations > 0:
                config.max_iterations = min(int(config.max_iterations), max_iterations)
            token_limit = int(budget.get("max_tokens", 0) or 0)
            if token_limit > 0:
                config.max_tokens = min(int(config.max_tokens), token_limit)
        execution = MissionExecution(
            task_id=str(task["id"]),
            mission_id=str(task.get("correlation_id") or ""),
        )
        campaign_id = self._campaign_id(task)
        guard = self._aggregate_guard(session)
        provider_name = str(getattr(config, "provider", "")) if config is not None else ""
        if guard is not None:
            decision = guard.check(campaign_id=campaign_id, provider=provider_name)
            if not decision.allowed:
                execution.budget_exhausted = True
                execution.error = f"aggregate budget exhausted: {decision.reason}"
                provider = getattr(session, "provider", None)
                close = getattr(provider, "close", None)
                if callable(close):
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                return execution
        stop = asyncio.Event()
        store = self._store()
        heartbeat = asyncio.create_task(
            self._heartbeat(
                store,
                execution.task_id,
                session,
                stop,
                execution,
                heartbeat_seconds=heartbeat_seconds,
            )
        )
        inflight_mutations: set[str] = set()
        explicit_unknown = False
        max_subagents = max(0, int(budget.get("max_subagent_calls", 12) or 0))
        token_limit = int(budget.get("max_tokens", 0) or 0)
        usd_limit = float(budget.get("max_usd", 0) or 0)
        subagent_calls = 0
        delegated_names = {
            "mission_delegate", "mission_conclave", "mission_skill_run",
            "delegate_subagent", "conclave", "skill_run",
            "vor_delegate", "huginn_delegate", "muninn_delegate",
        }

        def estimated_cost() -> float:
            rows = getattr(session, "_per_model_usage", {})
            if not isinstance(rows, dict):
                return 0.0
            return sum(
                float(row.get("cost", 0) or 0)
                for row in rows.values()
                if isinstance(row, dict)
            )

        try:
            async with asyncio.timeout(max(1.0, float(wall_seconds))):
                with _working_directory(root):
                    async for event in session.process_message_stream(self._mission_prompt(task)):
                        kind = event.get("type")
                        if kind == "tool_call":
                            name = str(event.get("name") or "")
                            if name in delegated_names:
                                subagent_calls += 1
                                if subagent_calls > max_subagents:
                                    execution.budget_exhausted = True
                                    execution.error = (
                                        f"subagent budget exceeded: {subagent_calls}>{max_subagents}"
                                    )
                                    cancel = getattr(session, "cancel", None)
                                    if callable(cancel):
                                        cancel()
                                    break
                            if tool_capability(name) != "read":
                                inflight_mutations.add(str(event.get("id") or name))
                        elif kind == "tool_result":
                            call_id = str(event.get("id") or event.get("name") or "")
                            inflight_mutations.discard(call_id)
                            explicit_unknown = (
                                explicit_unknown or self._result_effects_unknown(event)
                            )
                        elif kind in {"usage", "done"}:
                            usage = event.get("usage") or {}
                            total_tokens = int(usage.get("total_tokens", 0) or 0)
                            if token_limit > 0 and total_tokens > token_limit:
                                execution.budget_exhausted = True
                                execution.error = (
                                    f"token budget exceeded: {total_tokens}>{token_limit}"
                                )
                                cancel = getattr(session, "cancel", None)
                                if callable(cancel):
                                    cancel()
                                break
                            cost = estimated_cost()
                            if usd_limit > 0 and cost > usd_limit:
                                execution.budget_exhausted = True
                                execution.error = (
                                    f"cost budget exceeded: {cost:.6f}>{usd_limit:.6f}"
                                )
                                cancel = getattr(session, "cancel", None)
                                if callable(cancel):
                                    cancel()
                                break
                        elif kind == "cancelled" and execution.lease_lost:
                            break
            current = self._kernel().status(execution.mission_id)
            execution.completed = bool(
                current and current.get("status") in {"completada", "fallida"}
            )
            execution.mutating_effect_started = bool(
                inflight_mutations or explicit_unknown
            )
        except TimeoutError:
            execution.error = f"mission wall budget exceeded: {wall_seconds:.1f}s"
            execution.budget_exhausted = True
            execution.mutating_effect_started = bool(
                inflight_mutations or explicit_unknown
            )
            cancel = getattr(session, "cancel", None)
            if callable(cancel):
                cancel()
        except Exception as exc:  # noqa: BLE001 - runtime frontier reconciles arbitrary provider/tool failures
            execution.error = f"{type(exc).__name__}: {exc}"
            execution.mutating_effect_started = bool(
                inflight_mutations or explicit_unknown
            )
        finally:
            stop.set()
            with contextlib.suppress(Exception):
                await heartbeat
            with contextlib.suppress(Exception):
                self._record_primary_usage(session, campaign_id)
            provider = getattr(session, "provider", None)
            close = getattr(provider, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
        return execution

    def _reconcile(self, execution: MissionExecution) -> str:
        store = self._store()
        current = self._kernel().status(execution.mission_id)
        if current and current.get("status") in {"completada", "fallida", "cancelada"}:
            return "terminal"
        if execution.lease_lost:
            return "lease_lost"
        payload = {
            "owner": self.owner,
            "error": execution.error,
            "effects_unknown": execution.mutating_effect_started,
            "budget_exhausted": execution.budget_exhausted,
        }
        if execution.mutating_effect_started:
            store.checkpoint_task(execution.task_id, "runtime-effects-unknown", payload)
            store.release_task(execution.task_id, self.owner, status="bloqueada")
            return "blocked"
        if execution.budget_exhausted:
            store.checkpoint_task(execution.task_id, "runtime-budget-exhausted", payload)
            store.release_task(execution.task_id, self.owner, status="bloqueada")
            return "blocked"
        snapshot = store.get()
        latest = next(
            (row for row in snapshot.get("tasks", []) if row.get("id") == execution.task_id),
            {},
        )
        attempts = int(latest.get("attempts", 0) or 0)
        max_retries = int(latest.get("max_retries", 2) or 0)
        if attempts > max_retries:
            payload.update({"attempts": attempts, "max_retries": max_retries})
            store.checkpoint_task(execution.task_id, "runtime-retry-exhausted", payload)
            store.release_task(execution.task_id, self.owner, status="bloqueada")
            return "blocked"
        label = (
            "runtime-error-safe-to-resume"
            if execution.error
            else "runtime-returned-without-completion"
        )
        store.checkpoint_task(execution.task_id, label, payload)
        store.release_task(execution.task_id, self.owner, status="pendiente")
        return "released"

    async def run_async(
        self,
        *,
        heartbeat_seconds: float | None = None,
    ) -> CampaignRunResult:
        started = time.monotonic()
        completed = blocked = released = processed = 0
        errors: list[str] = []
        interval = max(
            0.05,
            float(heartbeat_seconds)
            if heartbeat_seconds is not None
            else self.lease_seconds / 3,
        )
        store = self._store()
        store.resume_expired()
        processed_ids: set[str] = set()
        while (
            processed < self.max_missions
            and time.monotonic() - started < self.max_wall_seconds
        ):
            task = self.next_eligible(exclude_task_ids=processed_ids)
            if task is None:
                break
            try:
                claimed = store.claim_task(
                    str(task["id"]),
                    self.owner,
                    lease_seconds=self.lease_seconds,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(
                    f"claim {task.get('id')}: {type(exc).__name__}: {exc}"
                )
                break
            processed += 1
            processed_ids.add(str(claimed["id"]))
            claimed = store.update_task(
                str(claimed["id"]),
                attempts=int(claimed.get("attempts", 0) or 0) + 1,
            )
            store.checkpoint_task(
                str(claimed["id"]),
                "campaign-runtime-claimed",
                {
                    "owner": self.owner,
                    "mission_id": claimed.get("correlation_id"),
                    "attempt": claimed.get("attempts"),
                },
            )
            campaign_id = self._campaign_id(claimed)
            environment: dict[str, str | None] = {
                "YGGDRASIL_ORCHESTRATION_STATE": str(self.state_path),
                "YGGDRASIL_LONGRUN_ROOT": str(self.longrun_root),
                "YGGDRASIL_CAMPAIGN_ID": campaign_id,
            }
            elapsed = time.monotonic() - started
            remaining = max(1.0, self.max_wall_seconds - elapsed)
            task_wall = float(
                (claimed.get("budget") or {}).get("max_wall_seconds", remaining)
                or remaining
            )
            wall_seconds = max(1.0, min(remaining, task_wall))
            with _temporary_environment(environment):
                execution = await self._execute_claimed(
                    claimed,
                    heartbeat_seconds=interval,
                    wall_seconds=wall_seconds,
                )
                outcome = self._reconcile(execution)
            if execution.error:
                errors.append(f"{execution.task_id}: {execution.error}")
            if outcome == "terminal":
                completed += 1
            elif outcome == "blocked":
                blocked += 1
            elif outcome == "released":
                released += 1
            elif outcome == "lease_lost":
                errors.append(f"{execution.task_id}: lease ownership lost")
                break
            store.resume_expired()
        return CampaignRunResult(
            self.owner,
            processed,
            completed,
            blocked,
            released,
            tuple(errors),
        )

    def run(self, *, heartbeat_seconds: float | None = None) -> CampaignRunResult:
        return asyncio.run(self.run_async(heartbeat_seconds=heartbeat_seconds))


@contextmanager
def _temporary_environment(values: dict[str, str | None]):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
