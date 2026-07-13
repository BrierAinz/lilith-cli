"""Tests for hook integration in the 5-phase pipeline runner.

The PipelineRunner fires hooks at lifecycle points:
    - PRE_PIPELINE_START
    - PRE_PIPELINE_PHASE
    - POST_PIPELINE_PHASE
    - POST_PIPELINE_END

Hooks can:
    - Observe pipeline progress
    - Modify phase data (e.g. abort a phase)
    - Abort the entire pipeline by returning None
"""

from __future__ import annotations

import pytest

from lilith_core.hooks import HookContext, HookRegistry, HookType

from lilith_orchestrator.graph.pipeline import (
    PipelinePhase,
    PipelineRunner,
)
from lilith_orchestrator.graph.state import GraphState


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def initial_state() -> GraphState:
    """GraphState with a user message that passes default gates."""
    return GraphState(
        messages=[{"role": "user", "content": "Build a CLI todo app"}],
    )


@pytest.fixture
def code_node_patched():
    """Patch the default code_node to set files_created so CODE gate passes."""
    from lilith_orchestrator.graph import pipeline as pipeline_mod

    original = pipeline_mod.DEFAULT_NODES[PipelinePhase.CODE]

    def patched(state: GraphState) -> GraphState:
        state = original(state)
        state.context["code"]["files_created"] = ["app.py"]
        return state

    return patched


@pytest.fixture
def fresh_hooks() -> HookRegistry:
    """Provide a fresh, isolated HookRegistry for each test."""
    return HookRegistry()


# ── Basic hook firing tests ────────────────────────────────────────────────


class TestPipelineHookFiring:
    """Verify hooks fire at the right times with the right data."""

    def test_pre_pipeline_start_fires(
        self, initial_state, code_node_patched, fresh_hooks
    ):
        events = []

        def capture_start(ctx: HookContext) -> HookContext:
            events.append(("start", ctx.data.copy()))
            return ctx

        fresh_hooks.register(
            HookType.PRE_PIPELINE_START, capture_start, name="start"
        )
        runner = PipelineRunner(
            max_retries=2,
            hooks=fresh_hooks,
        )
        runner.nodes[PipelinePhase.CODE] = code_node_patched

        result = runner.run(initial_state)
        assert result.success
        assert events and events[0][0] == "start"
        start_data = events[0][1]
        assert start_data["start_phase"] == "idea"
        assert start_data["end_phase"] == "code"
        assert start_data["total_phases"] == 5

    def test_pre_pipeline_phase_fires_for_each_phase(
        self, initial_state, code_node_patched, fresh_hooks
    ):
        phases_seen: list[str] = []

        def capture(ctx: HookContext) -> HookContext:
            phases_seen.append(ctx.data["phase"])
            return ctx

        fresh_hooks.register(
            HookType.PRE_PIPELINE_PHASE, capture, name="phase_capture"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        runner.nodes[PipelinePhase.CODE] = code_node_patched

        result = runner.run(initial_state)
        assert result.success
        assert phases_seen == ["idea", "research", "design", "plan", "code"]

    def test_post_pipeline_phase_fires_with_gate_result(
        self, initial_state, code_node_patched, fresh_hooks
    ):
        post_events: list[dict] = []

        def capture(ctx: HookContext) -> HookContext:
            post_events.append(ctx.data.copy())
            return ctx

        fresh_hooks.register(
            HookType.POST_PIPELINE_PHASE, capture, name="post_capture"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        runner.nodes[PipelinePhase.CODE] = code_node_patched

        result = runner.run(initial_state)
        assert result.success
        # 5 phases × 1 attempt each = 5 post events
        assert len(post_events) == 5
        # All should be passing
        assert all(e["gate_passed"] for e in post_events)

    def test_post_pipeline_end_fires(
        self, initial_state, code_node_patched, fresh_hooks
    ):
        captured: list[dict] = []

        def capture(ctx: HookContext) -> HookContext:
            captured.append(ctx.data.copy())
            return ctx

        fresh_hooks.register(
            HookType.POST_PIPELINE_END, capture, name="end"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        runner.nodes[PipelinePhase.CODE] = code_node_patched

        result = runner.run(initial_state)
        assert result.success
        assert len(captured) == 1
        end_data = captured[0]
        assert end_data["total_phases"] == 5
        assert end_data["completed_phases"] == [
            "idea", "research", "design", "plan", "code"
        ]
        assert end_data["duration_ms"] >= 0

    def test_session_id_passed_through(
        self, initial_state, code_node_patched, fresh_hooks
    ):
        seen_sids: set[str] = set()

        def capture(ctx: HookContext) -> HookContext:
            seen_sids.add(ctx.session_id)
            return ctx

        fresh_hooks.register(
            HookType.PRE_PIPELINE_PHASE, capture, name="sid_capture"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        runner.nodes[PipelinePhase.CODE] = code_node_patched

        runner.run(initial_state, session_id="test-session-123")
        # All phases share the same session_id
        assert seen_sids == {"test-session-123"}


# ── Hook behaviour: abort ───────────────────────────────────────────────────


class TestPipelineHookAbort:
    """Verify hooks can abort the pipeline."""

    def test_pre_pipeline_start_abort(self, initial_state, fresh_hooks):
        def abort(ctx: HookContext) -> HookContext | None:
            return None  # Abort

        fresh_hooks.register(
            HookType.PRE_PIPELINE_START, abort, name="abort_start"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        result = runner.run(initial_state)

        assert not result.success
        assert result.completed_phases == []
        assert any("PRE_PIPELINE_START" in e for e in result.errors)

    def test_pre_pipeline_phase_abort(self, initial_state, fresh_hooks):
        """Hook can abort in the middle of the pipeline."""
        phases_seen: list[str] = []

        def selective_abort(ctx: HookContext) -> HookContext | None:
            phases_seen.append(ctx.data["phase"])
            if ctx.data["phase"] == "design":
                return None  # Abort at DESIGN
            return ctx

        fresh_hooks.register(
            HookType.PRE_PIPELINE_PHASE, selective_abort, name="abort_design"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        result = runner.run(initial_state)

        assert not result.success
        assert phases_seen == ["idea", "research", "design"]
        assert PipelinePhase.IDEA in result.completed_phases
        assert PipelinePhase.RESEARCH in result.completed_phases
        assert result.aborted_at == PipelinePhase.DESIGN

    def test_post_hook_override_gate_pass(
        self, initial_state, fresh_hooks
    ):
        """A POST_PIPELINE_PHASE hook can mark a phase as failed."""

        def override(ctx: HookContext) -> HookContext:
            if ctx.data["phase"] == "idea":
                # Override pass → fail
                ctx.data["gate_passed"] = False
                ctx.data["gate_reason"] = "overridden by hook"
            return ctx

        fresh_hooks.register(
            HookType.POST_PIPELINE_PHASE, override, name="override"
        )
        runner = PipelineRunner(max_retries=0, hooks=fresh_hooks)
        result = runner.run(initial_state)

        assert not result.success
        assert result.aborted_at == PipelinePhase.IDEA
        assert any("overridden by hook" in e for e in result.errors)


# ── Hook behaviour: skip phase ─────────────────────────────────────────────


class TestPipelineHookSkip:
    """Verify hooks can skip a phase via abort=True."""

    def test_skip_phase_via_abort_data(
        self, initial_state, fresh_hooks
    ):
        """When PRE_PIPELINE_PHASE returns data with abort=True, phase is skipped."""
        phases_seen: list[str] = []

        def skip_design(ctx: HookContext) -> HookContext:
            phases_seen.append(ctx.data["phase"])
            if ctx.data["phase"] == "design":
                ctx.data["abort"] = True
            return ctx

        fresh_hooks.register(
            HookType.PRE_PIPELINE_PHASE, skip_design, name="skip"
        )
        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        # Patch code node so CODE gate passes
        from lilith_orchestrator.graph import pipeline as pipeline_mod

        original = pipeline_mod.DEFAULT_NODES[PipelinePhase.CODE]

        def patched(state):
            state = original(state)
            state.context["code"]["files_created"] = ["x.py"]
            return state

        runner.nodes[PipelinePhase.CODE] = patched

        result = runner.run(initial_state)
        assert result.success
        # DESIGN was seen in hook but not added to completed
        assert phases_seen == ["idea", "research", "design", "plan", "code"]
        assert PipelinePhase.DESIGN not in result.completed_phases
        # Other phases completed
        assert PipelinePhase.IDEA in result.completed_phases
        assert PipelinePhase.RESEARCH in result.completed_phases
        assert PipelinePhase.PLAN in result.completed_phases
        assert PipelinePhase.CODE in result.completed_phases


# ── Multiple hooks & priority ──────────────────────────────────────────────


class TestPipelineHookPriority:
    """Verify multiple hooks fire in priority order."""

    def test_multiple_post_hooks_fire(
        self, initial_state, code_node_patched, fresh_hooks
    ):
        order: list[str] = []

        def first(ctx: HookContext) -> HookContext:
            order.append("first")
            return ctx

        def second(ctx: HookContext) -> HookContext:
            order.append("second")
            return ctx

        fresh_hooks.register(
            HookType.POST_PIPELINE_PHASE, first, name="first", priority=10
        )
        fresh_hooks.register(
            HookType.POST_PIPELINE_PHASE, second, name="second", priority=5
        )

        runner = PipelineRunner(max_retries=2, hooks=fresh_hooks)
        runner.nodes[PipelinePhase.CODE] = code_node_patched

        result = runner.run(initial_state)
        assert result.success
        # 5 phases × 2 hooks = 10 calls
        assert len(order) == 10
        # Second fires first (lower priority value)
        assert order[0] == "second"
        assert order[1] == "first"


# ── Isolation: custom hooks don't leak ─────────────────────────────────────


class TestPipelineHookIsolation:
    """Verify the runner uses its own hook registry when provided."""

    def test_custom_registry_does_not_affect_global(self, initial_state):
        """The runner should not pollute global hook registry."""
        from lilith_core.hooks import get_hook_registry

        global_registry = get_hook_registry()
        initial_global_count = global_registry.hook_count

        # Capture via custom registry
        captured = []
        custom = HookRegistry()

        def capture(ctx: HookContext) -> HookContext:
            captured.append(ctx.data.copy())
            return ctx

        custom.register(HookType.PRE_PIPELINE_START, capture, name="local")

        runner = PipelineRunner(max_retries=0, hooks=custom)
        # Patch code node so CODE gate passes
        from lilith_orchestrator.graph import pipeline as pipeline_mod

        original = pipeline_mod.DEFAULT_NODES[PipelinePhase.CODE]

        def patched(state):
            state = original(state)
            state.context["code"]["files_created"] = ["x.py"]
            return state

        runner.nodes[PipelinePhase.CODE] = patched

        result = runner.run(initial_state)

        assert result.success
        assert len(captured) == 1
        # Global registry should be unchanged
        assert get_hook_registry().hook_count == initial_global_count