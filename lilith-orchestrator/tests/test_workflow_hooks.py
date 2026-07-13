"""Tests for the WorkflowEngine ↔ lilith_core.hooks integration.

These tests exercise the new hook firing points inside
``WorkflowEngine.run()`` and ``WorkflowEngine._execute_step()``:

    * on_session_start / on_session_end at the workflow boundary
    * pre_tool_call / post_tool_call for each step
    * on_error for step failures and gate failures
    * Prompt rewrite + abort-by-returning-None from pre_tool_call

The HookRegistry under test is the real :mod:`lilith_core.hooks`
registry — we don't stub it. Each test uses a private registry passed
into the WorkflowEngine so state is fully isolated.
"""

from __future__ import annotations

import pytest

from lilith_core.hooks import HookContext, HookRegistry, HookType

from lilith_orchestrator.workflow import (
    GateType,
    OnFailure,
    QualityGate,
    StepStatus,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowStep,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_workflow(steps: list[WorkflowStep], on_failure: OnFailure = OnFailure.ABORT) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition from a list of steps."""
    return WorkflowDefinition(
        name="hook_test_wf",
        version="1.0",
        steps=steps,
        on_failure=on_failure,
    )


def _make_step(name: str, intent: str = "chat", description: str | None = None) -> WorkflowStep:
    """Build a minimal WorkflowStep that always passes its gate."""
    return WorkflowStep(
        name=name,
        intent=intent,
        description=description or f"step {name}",
        gate=QualityGate(type=GateType.NONE),
    )


# ── Tests ──────────────────────────────────────────────────────────────────


class TestWorkflowHookFire:
    """Hook firing at the workflow and step boundary."""

    def test_fires_session_start_and_end(self):
        """A run with no steps still fires on_session_start / on_session_end."""
        registry = HookRegistry()
        events: list[tuple[str, str]] = []

        def capture(ctx: HookContext) -> HookContext:
            events.append((ctx.hook_type.value, ctx.data.get("event", "")))
            return ctx

        registry.register(HookType.ON_SESSION_START, capture, "cap_start")
        registry.register(HookType.ON_SESSION_END, capture, "cap_end")

        engine = WorkflowEngine(hook_registry=registry, session_id="sess-1")
        wf = _make_workflow([])
        result = engine.run(wf)

        assert result.status == WorkflowStatus.COMPLETED
        kinds = [k for k, _ in events]
        assert HookType.ON_SESSION_START.value in kinds
        assert HookType.ON_SESSION_END.value in kinds
        # end event is the completion marker
        assert any(
            k == HookType.ON_SESSION_END.value and e == "workflow_completed"
            for k, e in events
        )

    def test_fires_pre_and_post_tool_for_each_step(self):
        """Each step fires pre_tool_call + post_tool_call with matching step names."""
        registry = HookRegistry()
        seen: list[tuple[str, str]] = []

        def capture(ctx: HookContext) -> HookContext:
            seen.append((ctx.hook_type.value, ctx.data.get("step", "")))
            return ctx

        registry.register(HookType.PRE_TOOL_CALL, capture, "cap_pre")
        registry.register(HookType.POST_TOOL_CALL, capture, "cap_post")

        engine = WorkflowEngine(hook_registry=registry, session_id="sess-2")
        wf = _make_workflow(
            [
                _make_step("alpha"),
                _make_step("beta"),
                _make_step("gamma"),
            ]
        )
        result = engine.run(wf)

        assert result.status == WorkflowStatus.COMPLETED
        # 3 steps × 2 hooks = 6 events
        assert len(seen) == 6
        pre = [s for k, s in seen if k == HookType.PRE_TOOL_CALL.value]
        post = [s for k, s in seen if k == HookType.POST_TOOL_CALL.value]
        assert pre == ["alpha", "beta", "gamma"]
        assert post == ["alpha", "beta", "gamma"]

    def test_engine_creates_default_registry(self):
        """A WorkflowEngine with no hook_registry still has a registry
        attribute and can run workflows without raising."""
        engine = WorkflowEngine()
        # Attribute exists and is a HookRegistry (or None if lilith_core
        # somehow not importable — both are safe per the implementation)
        assert engine.hook_registry is None or isinstance(engine.hook_registry, HookRegistry)
        wf = _make_workflow([_make_step("a")])
        result = engine.run(wf)
        assert result.status == WorkflowStatus.COMPLETED

    def test_session_id_is_stable_across_runs_until_overridden(self):
        """The engine session_id is set at construction and propagates to hooks."""
        registry = HookRegistry()
        seen_sessions: list[str] = []

        def capture(ctx: HookContext) -> HookContext:
            seen_sessions.append(ctx.session_id)
            return ctx

        registry.register(HookType.ON_SESSION_START, capture, "cap_sess")

        engine = WorkflowEngine(hook_registry=registry, session_id="deterministic-sid")
        engine.run(_make_workflow([_make_step("a")]))
        assert seen_sessions == ["deterministic-sid"]

    def test_pre_hook_can_rewrite_prompt(self):
        """pre_tool_call can mutate data['prompt'] and the rewritten value
        flows into the custom executor's view of the prompt."""
        registry = HookRegistry()
        observed: list[str] = []

        def rewrite(ctx: HookContext) -> HookContext:
            ctx.data["prompt"] = "REWRITTEN BY HOOK"
            return ctx

        registry.register(HookType.PRE_TOOL_CALL, rewrite, "rewriter")

        engine = WorkflowEngine(hook_registry=registry)

        def custom_executor(step, context):
            # The orchestrator stashes the (possibly rewritten) prompt
            # at context["effective_prompt"] before invoking the executor.
            prompt = context.get("effective_prompt", "")
            observed.append(prompt)
            return f"executor saw: {prompt}", "custom_agent"

        engine.register_executor("chat", custom_executor)
        wf = _make_workflow([_make_step("rewrite_me", intent="chat")])
        result = engine.run(wf, context={"last_output": "ORIGINAL"})
        # custom_executor was called once and saw the rewritten prompt
        assert len(observed) == 1
        assert observed[0] == "REWRITTEN BY HOOK"
        assert result.status == WorkflowStatus.COMPLETED

    def test_pre_hook_returning_none_aborts_step(self):
        """If a pre_tool_call hook returns None, the step is marked FAILED
        with a deterministic error message — and post_tool_call does not
        fire (the chain was cut)."""
        registry = HookRegistry()
        post_calls: list[str] = []

        def aborter(ctx: HookContext) -> HookContext | None:
            return None  # abort

        def capture_post(ctx: HookContext) -> HookContext:
            post_calls.append(ctx.data.get("step", ""))
            return ctx

        registry.register(HookType.PRE_TOOL_CALL, aborter, "aborter")
        registry.register(HookType.POST_TOOL_CALL, capture_post, "cap_post")

        engine = WorkflowEngine(hook_registry=registry)
        wf = _make_workflow([_make_step("aborted_step")], on_failure=OnFailure.ABORT)
        result = engine.run(wf)

        assert result.status == WorkflowStatus.ABORTED
        assert len(result.steps) == 1
        assert result.steps[0].status == StepStatus.FAILED
        assert "aborted by pre_tool_call hook" in (result.steps[0].error or "")
        # post never fired
        assert post_calls == []

    def test_on_error_fires_on_step_exception(self):
        """An exception in the executor path triggers on_error."""
        registry = HookRegistry()
        error_events: list[dict] = []

        def capture_error(ctx: HookContext) -> HookContext:
            error_events.append(dict(ctx.data))
            return ctx

        registry.register(HookType.ON_ERROR, capture_error, "cap_err")

        engine = WorkflowEngine(hook_registry=registry)

        def boom(step, context):
            raise RuntimeError("kaboom")

        engine.register_executor("chat", boom)
        wf = _make_workflow(
            [_make_step("explode", intent="chat")], on_failure=OnFailure.ABORT
        )
        result = engine.run(wf)

        assert result.status == WorkflowStatus.ABORTED
        assert error_events, "expected at least one on_error event"
        # The step_failed event records the error message
        step_failed = [e for e in error_events if e.get("event") == "step_failed"]
        assert step_failed, f"missing step_failed event in {error_events}"
        assert "kaboom" in step_failed[0].get("error", "")

    def test_no_registry_means_silent_noop(self):
        """Even with hook_registry=None, run() completes successfully."""
        engine = WorkflowEngine(hook_registry=None)
        wf = _make_workflow([_make_step("noop")])
        result = engine.run(wf)
        assert result.status == WorkflowStatus.COMPLETED
        assert result.steps[0].status == StepStatus.PASSED

    def test_agent_name_propagates_to_hook_context(self):
        """A custom agent_name is reflected on every hook context."""
        registry = HookRegistry()
        agents_seen: list[str] = []

        def capture(ctx: HookContext) -> HookContext:
            agents_seen.append(ctx.agent_name)
            return ctx

        registry.register(HookType.PRE_TOOL_CALL, capture, "cap_agnt")
        registry.register(HookType.ON_SESSION_START, capture, "cap_agnt_start")

        engine = WorkflowEngine(
            hook_registry=registry,
            agent_name="skadi_runecarver",
        )
        engine.run(_make_workflow([_make_step("a")]))
        assert all(a == "skadi_runecarver" for a in agents_seen)
        assert agents_seen  # at least one event fired
