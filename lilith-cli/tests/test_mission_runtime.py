import asyncio
import re
from pathlib import Path

from lilith_cli.mission.compiler import MissionCompiler
from lilith_cli.mission.kernel import MissionKernel
from lilith_cli.mission.runtime import AutonomousCampaignRuntime
from lilith_tools.orchestration_state import OrchestrationStateStore


def _register(tmp_path: Path, *, objective: str = "runtime mission"):
    project = tmp_path / objective.replace(" ", "-")
    project.mkdir(exist_ok=True)
    compiled = MissionCompiler().compile(
        objective=objective,
        project_root=str(project),
        success_criteria=("mission closed",),
    )
    kernel = MissionKernel(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
    )
    return kernel, kernel.register(compiled)


class _Provider:
    async def close(self):
        return None


class _BaseSession:
    def __init__(self):
        self.provider = _Provider()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    @staticmethod
    def _task_id(prompt: str) -> str:
        match = re.search(r"^task_id:\s*(\S+)", prompt, re.MULTILINE)
        assert match
        return match.group(1)


class _CompleteSession(_BaseSession):
    def __init__(self, kernel: MissionKernel, *, delay: float = 0.0):
        super().__init__()
        self.kernel = kernel
        self.delay = delay

    async def process_message_stream(self, prompt: str):
        task_id = self._task_id(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        yield {
            "type": "tool_call",
            "id": "close-1",
            "name": "mission_complete",
            "arguments": {"task_id": task_id},
        }
        self.kernel.complete(
            task_id,
            success=True,
            summary="verified runtime completion",
            campaign_mode=False,
        )
        yield {
            "type": "tool_result",
            "id": "close-1",
            "name": "mission_complete",
            "content": '{"task":{"status":"completada"}}',
            "is_error": False,
        }
        yield {"type": "done", "content": "done", "usage": {}}


class _CrashBeforeEffects(_BaseSession):
    async def process_message_stream(self, prompt: str):
        if False:
            yield {}
        raise RuntimeError("provider dropped before tools")


class _CrashDuringWrite(_BaseSession):
    async def process_message_stream(self, prompt: str):
        yield {
            "type": "tool_call",
            "id": "write-1",
            "name": "file_write",
            "arguments": {"path": "x.txt", "content": "x"},
        }
        raise RuntimeError("connection lost while write result unknown")


def test_runtime_claims_and_completes_registered_mission(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path)
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: _CompleteSession(kernel),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.processed == 1
    assert result.completed == 1
    assert result.blocked == result.released == 0
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "completada"


def test_runtime_heartbeat_renews_lease_during_slow_turn(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="slow mission")
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        lease_seconds=15,
        session_factory=lambda root: _CompleteSession(kernel, delay=0.16),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.completed == 1
    events = OrchestrationStateStore(tmp_path / "state.sqlite3").events(
        limit=100, task_id=registration.task_id
    )
    assert any(row["event_type"] == "task.lease_renewed" for row in events)


def test_crash_before_effects_releases_mission_to_pending(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="safe crash")
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: _CrashBeforeEffects(),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.released == 1
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "pendiente"
    checkpoints = OrchestrationStateStore(tmp_path / "state.sqlite3").checkpoints(
        registration.task_id
    )
    assert any(row["label"] == "runtime-error-safe-to-resume" for row in checkpoints)


def test_crash_with_mutation_in_flight_blocks_retry(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="unknown write")
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: _CrashDuringWrite(),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.blocked == 1
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "bloqueada"
    checkpoints = OrchestrationStateStore(tmp_path / "state.sqlite3").checkpoints(
        registration.task_id
    )
    unknown = next(row for row in checkpoints if row["label"] == "runtime-effects-unknown")
    assert unknown["payload"]["effects_unknown"] is True


def test_only_pending_lilith_missions_are_eligible(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="eligible")
    store = OrchestrationStateStore(tmp_path / "state.sqlite3")
    store.add_task("ordinary", status="pendiente", preset="cocytus", task_id="ordinary")
    blocked_compiled = MissionCompiler().compile(
        objective="blocked",
        project_root=str(tmp_path),
        success_criteria=("operator decision",),
    )
    blocked_reg = kernel.register(blocked_compiled)
    store.update_task(blocked_reg.task_id, status="bloqueada")

    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        session_factory=lambda root: _CrashBeforeEffects(),
    )
    task = runtime.next_eligible()
    assert task and task["id"] == registration.task_id


def test_expired_lease_is_resumed_then_reclaimed(monkeypatch, tmp_path: Path) -> None:
    import lilith_tools.orchestration_sqlite as sqlite_module

    clock = {"now": 1000.0}
    monkeypatch.setattr(sqlite_module.time, "time", lambda: clock["now"])
    kernel, registration = _register(tmp_path, objective="expired lease")
    store = OrchestrationStateStore(tmp_path / "state.sqlite3")
    store.claim_task(registration.task_id, "dead-owner", lease_seconds=15)
    clock["now"] = 1020.0

    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        owner="replacement-owner",
        max_missions=1,
        session_factory=lambda root: _CompleteSession(kernel),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.completed == 1
    events = store.events(limit=100, task_id=registration.task_id)
    assert any(row["event_type"] == "task.resumed_after_lease" for row in events)
    assert kernel.status(registration.mission_id)["status"] == "completada"


def test_two_runtimes_cannot_execute_same_mission(tmp_path: Path) -> None:
    kernel, _ = _register(tmp_path, objective="single owner")
    first = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        owner="runtime-a",
        max_missions=1,
        session_factory=lambda root: _CompleteSession(kernel, delay=0.15),
    )
    second = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        owner="runtime-b",
        max_missions=1,
        session_factory=lambda root: _CompleteSession(kernel),
    )

    async def run_both():
        return await asyncio.gather(
            first.run_async(heartbeat_seconds=0.05),
            second.run_async(heartbeat_seconds=0.05),
        )

    a, b = asyncio.run(run_both())
    assert sorted((a.processed, b.processed)) == [0, 1]
    assert a.completed + b.completed == 1


def test_campaign_child_runs_only_after_completed_parent(tmp_path: Path) -> None:
    from lilith_cli.mission.campaign import CampaignRecommendation

    kernel, parent = _register(tmp_path, objective="parent mission")
    store = OrchestrationStateStore(tmp_path / "state.sqlite3")
    parent_task = store.get()["tasks"][0]
    recommendation = CampaignRecommendation(
        objective="child mission",
        success_criteria=("child closed",),
        reason="follow-up after parent",
        capabilities=("plan",),
        priority=80,
    )
    from lilith_cli.mission.campaign import CampaignEngine

    engine = CampaignEngine(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
    )
    # Parent is still pending, so a manually created child is not yet eligible.
    chained = engine.chain(parent.task_id, [recommendation])
    child_task_id = chained["created"][0]["registration"]["task_id"]
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: _CrashBeforeEffects(),
    )
    assert runtime.next_eligible()["id"] == parent_task["id"]

    kernel.complete(
        parent.task_id,
        success=True,
        summary="parent done",
        campaign_mode=False,
    )
    assert runtime.next_eligible()["id"] == child_task_id


class _ToolCompleteSession(_BaseSession):
    async def process_message_stream(self, prompt: str):
        import json

        from lilith_cli.mission.tools import MissionCompleteTool

        task_id = self._task_id(prompt)
        yield {
            "type": "tool_call",
            "id": "close-tool",
            "name": "mission_complete",
            "arguments": {"task_id": task_id},
        }
        result = MissionCompleteTool().execute(
            task_id=task_id,
            success=True,
            summary="completed through default MissionKernel tool",
            campaign_mode=False,
        )
        assert result.success, result.error
        yield {
            "type": "tool_result",
            "id": "close-tool",
            "name": "mission_complete",
            "content": json.dumps(result.data, default=str),
            "is_error": False,
        }
        yield {"type": "done", "content": "done", "usage": {}}


def test_custom_state_is_propagated_to_mission_tools(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="custom state")
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: _ToolCompleteSession(),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.completed == 1
    assert kernel.status(registration.mission_id)["status"] == "completada"


def test_relative_runtime_paths_survive_project_cwd_change(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    kernel, registration = _register(tmp_path, objective="relative paths")
    runtime = AutonomousCampaignRuntime(
        state_path="state.sqlite3",
        longrun_root="longruns",
        max_missions=1,
        lease_seconds=15,
        session_factory=lambda root: _CompleteSession(kernel, delay=0.16),
    )
    assert runtime.state_path == (tmp_path / "state.sqlite3").resolve()
    assert runtime.longrun_root == (tmp_path / "longruns").resolve()
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.completed == 1
    assert not result.errors
    assert kernel.status(registration.mission_id)["status"] == "completada"


class _UsageBudgetSession(_BaseSession):
    def __init__(self, *, total_tokens: int = 500, cost: float = 0.0):
        super().__init__()
        from types import SimpleNamespace

        self.config = SimpleNamespace(max_iterations=99, max_tokens=4096)
        self.total_tokens = total_tokens
        self._per_model_usage = {"fixture": {"cost": cost}}

    async def process_message_stream(self, prompt: str):
        yield {
            "type": "usage",
            "usage": {"total_tokens": self.total_tokens},
        }
        yield {"type": "done", "content": "unfinished", "usage": {"total_tokens": self.total_tokens}}


class _SubagentBudgetSession(_BaseSession):
    async def process_message_stream(self, prompt: str):
        yield {
            "type": "tool_call",
            "id": "delegate-over-budget",
            "name": "mission_delegate",
            "arguments": {"agent": "aura", "prompt": "inspect"},
        }
        raise AssertionError("runtime must stop before continuing after subagent budget exhaustion")


class _SlowNoEffectSession(_BaseSession):
    async def process_message_stream(self, prompt: str):
        await asyncio.sleep(1.2)
        yield {"type": "done", "content": "late", "usage": {}}


def _budget_task(tmp_path: Path, objective: str, **overrides):
    kernel, registration = _register(tmp_path, objective=objective)
    store = OrchestrationStateStore(tmp_path / "state.sqlite3")
    task = next(row for row in store.get()["tasks"] if row["id"] == registration.task_id)
    budget = dict(task.get("budget") or {})
    budget.update(overrides)
    store.update_task(registration.task_id, budget=budget)
    return kernel, registration, store


def test_safe_failure_is_attempted_only_once_per_daemon_run(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="one attempt each run")
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=5,
        session_factory=lambda root: _CrashBeforeEffects(),
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.processed == 1
    assert result.released == 1
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "pendiente"
    assert task["attempts"] == 1


def test_retry_budget_blocks_after_third_safe_failure(tmp_path: Path) -> None:
    kernel, registration = _register(tmp_path, objective="retry budget")
    kwargs = {
        "state_path": tmp_path / "state.sqlite3",
        "longrun_root": tmp_path / "longruns",
        "max_missions": 1,
        "session_factory": lambda root: _CrashBeforeEffects(),
    }
    first = AutonomousCampaignRuntime(**kwargs).run(heartbeat_seconds=0.05)
    second = AutonomousCampaignRuntime(**kwargs).run(heartbeat_seconds=0.05)
    third = AutonomousCampaignRuntime(**kwargs).run(heartbeat_seconds=0.05)
    assert (first.released, second.released, third.blocked) == (1, 1, 1)
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "bloqueada"
    assert task["attempts"] == 3
    checkpoints = OrchestrationStateStore(tmp_path / "state.sqlite3").checkpoints(
        registration.task_id
    )
    exhausted = next(row for row in checkpoints if row["label"] == "runtime-retry-exhausted")
    assert exhausted["payload"]["attempts"] == 3
    assert exhausted["payload"]["max_retries"] == 2


def test_token_budget_blocks_campaign_run(tmp_path: Path) -> None:
    kernel, registration, store = _budget_task(
        tmp_path, "token budget", max_tokens=100
    )
    session = _UsageBudgetSession(total_tokens=101)
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: session,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.blocked == 1
    assert session.cancelled is True
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "bloqueada"
    checkpoint = next(
        row for row in store.checkpoints(registration.task_id)
        if row["label"] == "runtime-budget-exhausted"
    )
    assert checkpoint["payload"]["budget_exhausted"] is True
    assert any("token budget exceeded" in error for error in result.errors)


def test_subagent_budget_stops_before_second_effect(tmp_path: Path) -> None:
    kernel, registration, store = _budget_task(
        tmp_path, "subagent budget", max_subagent_calls=0
    )
    session = _SubagentBudgetSession()
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: session,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.blocked == 1
    assert session.cancelled is True
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "bloqueada"
    assert any("subagent budget exceeded" in error for error in result.errors)
    assert any(
        row["label"] == "runtime-budget-exhausted"
        for row in store.checkpoints(registration.task_id)
    )


def test_cost_budget_blocks_campaign_run(tmp_path: Path) -> None:
    kernel, registration, _store = _budget_task(
        tmp_path, "cost budget", max_usd=0.01
    )
    session = _UsageBudgetSession(total_tokens=10, cost=0.02)
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: session,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.blocked == 1
    assert session.cancelled is True
    assert kernel.status(registration.mission_id)["status"] == "bloqueada"
    assert any("cost budget exceeded" in error for error in result.errors)


def test_wall_budget_blocks_slow_no_effect_session(tmp_path: Path) -> None:
    kernel, registration, store = _budget_task(
        tmp_path, "wall budget", max_wall_seconds=1
    )
    session = _SlowNoEffectSession()
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        max_wall_seconds=10,
        session_factory=lambda root: session,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.blocked == 1
    assert session.cancelled is True
    assert kernel.status(registration.mission_id)["status"] == "bloqueada"
    assert any("mission wall budget exceeded" in error for error in result.errors)
    assert any(
        row["label"] == "runtime-budget-exhausted"
        for row in store.checkpoints(registration.task_id)
    )


def test_runtime_applies_iteration_and_response_token_caps(tmp_path: Path) -> None:
    _kernel, _registration, _store = _budget_task(
        tmp_path,
        "config budget",
        max_iterations=7,
        max_tokens=200,
    )
    session = _UsageBudgetSession(total_tokens=10)
    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=lambda root: session,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.released == 1
    assert session.config.max_iterations == 7
    assert session.config.max_tokens == 200


class _BudgetCompleteSession(_CompleteSession):
    def __init__(self, kernel, policy, *, usage=None, cost=0.0):
        super().__init__(kernel)
        from lilith_cli.config import YggdrasilConfig

        self.config = YggdrasilConfig(
            memory={"enabled": False},
            campaign_budgets=policy,
        )
        self._usage = usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._cost = cost
        self.stream_calls = 0
        self.provider.closed = False

    @property
    def total_usage(self):
        return dict(self._usage)

    @property
    def per_model_usage(self):
        return {"fixture": {**self._usage, "cost": self._cost}}

    async def process_message_stream(self, prompt: str):
        self.stream_calls += 1
        async for event in super().process_message_stream(prompt):
            yield event


def test_aggregate_budget_blocks_before_model_stream(tmp_path: Path) -> None:
    from lilith_cli.config import CampaignBudgetConfig

    kernel, registration = _register(tmp_path, objective="aggregate blocked")
    store = OrchestrationStateStore(tmp_path / "state.sqlite3")
    store.record_cost(
        "lilith", "experiential", {"total_tokens": 5, "cost_usd": 0.0},
        session_id=registration.mission_id,
    )
    policy = CampaignBudgetConfig(daily_max_calls=1, daily_max_usd=None)
    holder = {}

    def factory(root):
        session = _BudgetCompleteSession(kernel, policy)
        holder["session"] = session
        return session

    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=factory,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.blocked == 1
    assert holder["session"].stream_calls == 0
    task = kernel.status(registration.mission_id)
    assert task and task["status"] == "bloqueada"
    checkpoints = store.checkpoints(registration.task_id)
    assert any(row["label"] == "runtime-budget-exhausted" for row in checkpoints)


def test_primary_runtime_usage_is_recorded_under_root_campaign(tmp_path: Path) -> None:
    from lilith_cli.config import CampaignBudgetConfig

    kernel, registration = _register(tmp_path, objective="usage recorded")
    policy = CampaignBudgetConfig(daily_max_usd=None, campaign_max_usd=None)
    holder = {}

    def factory(root):
        session = _BudgetCompleteSession(
            kernel,
            policy,
            usage={"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            cost=0.0,
        )
        holder["session"] = session
        return session

    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        max_missions=1,
        session_factory=factory,
    )
    result = runtime.run(heartbeat_seconds=0.05)
    assert result.completed == 1
    rows = OrchestrationStateStore(tmp_path / "state.sqlite3").events(limit=100)
    cost_events = [row for row in rows if row["event_type"] == "cost.recorded"]
    assert len(cost_events) == 1
    payload = cost_events[0]["payload"]
    assert payload["preset"] == "lilith"
    assert payload["session_id"] == registration.mission_id
    assert payload["usage"]["total_tokens"] == 10


def test_zero_usage_does_not_create_phantom_cost_call(tmp_path: Path) -> None:
    from lilith_cli.config import CampaignBudgetConfig

    runtime = AutonomousCampaignRuntime(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "longruns",
        session_factory=lambda root: None,
    )
    kernel, _ = _register(tmp_path, objective="zero usage")
    session = _BudgetCompleteSession(
        kernel,
        CampaignBudgetConfig(daily_max_usd=None),
    )
    runtime._record_primary_usage(session, "campaign-zero")
    rows = OrchestrationStateStore(tmp_path / "state.sqlite3").events(limit=100)
    assert not any(row["event_type"] == "cost.recorded" for row in rows)
