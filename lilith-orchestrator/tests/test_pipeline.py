"""Tests for the 5-phase pipeline (IDEA→RESEARCH→DESIGN→PLAN→CODE)."""

from __future__ import annotations

import pytest

from lilith_orchestrator.graph.pipeline import (
    DEFAULT_GATES,
    DEFAULT_NODES,
    GateResult,
    PipelinePhase,
    PipelineResult,
    PipelineRunner,
    QualityGate,
    code_node,
    design_node,
    idea_node,
    plan_node,
    research_node,
)
from lilith_orchestrator.graph.state import GraphState


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def initial_state():
    """GraphState with a user message."""
    return GraphState(
        messages=[{"role": "user", "content": "Build a REST API for a todo app with SQLite"}],
    )


@pytest.fixture
def runner():
    """Default PipelineRunner."""
    return PipelineRunner(max_retries=2)


# ── PipelinePhase tests ──────────────────────────────────────────────────────


class TestPipelinePhase:
    """Tests for the PipelinePhase enum."""

    def test_phase_values(self):
        assert PipelinePhase.IDEA.value == "idea"
        assert PipelinePhase.RESEARCH.value == "research"
        assert PipelinePhase.DESIGN.value == "design"
        assert PipelinePhase.PLAN.value == "plan"
        assert PipelinePhase.CODE.value == "code"

    def test_phase_order(self):
        assert PipelinePhase.IDEA.order == 0
        assert PipelinePhase.RESEARCH.order == 1
        assert PipelinePhase.DESIGN.order == 2
        assert PipelinePhase.PLAN.order == 3
        assert PipelinePhase.CODE.order == 4

    def test_next_phase(self):
        assert PipelinePhase.IDEA.next() == PipelinePhase.RESEARCH
        assert PipelinePhase.RESEARCH.next() == PipelinePhase.DESIGN
        assert PipelinePhase.DESIGN.next() == PipelinePhase.PLAN
        assert PipelinePhase.PLAN.next() == PipelinePhase.CODE
        assert PipelinePhase.CODE.next() is None

    def test_from_string(self):
        assert PipelinePhase.from_string("idea") == PipelinePhase.IDEA
        assert PipelinePhase.from_string("CODE") == PipelinePhase.CODE
        assert PipelinePhase.from_string("Research") == PipelinePhase.RESEARCH

    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Unknown phase"):
            PipelinePhase.from_string("invalid")


# ── Node function tests ──────────────────────────────────────────────────────


class TestNodeFunctions:
    """Tests for individual phase node functions."""

    def test_idea_node_captures_input(self, initial_state):
        result = idea_node(initial_state)
        idea = result.context.get("idea", {})
        assert idea["summary"] == "Build a REST API for a todo app with SQLite"
        assert idea["raw_input"] != ""
        assert result.current_node == "idea"

    def test_idea_node_empty_messages(self):
        state = GraphState(messages=[])
        result = idea_node(state)
        idea = result.context.get("idea", {})
        assert idea["summary"] == ""

    def test_research_node(self, initial_state):
        # Run idea first to populate context
        state = idea_node(initial_state)
        result = research_node(state)
        research = result.context.get("research", {})
        assert research["query"] != ""
        assert result.current_node == "research"

    def test_design_node(self, initial_state):
        result = design_node(initial_state)
        design = result.context.get("design", {})
        assert "components" in design
        assert result.current_node == "design"

    def test_plan_node(self, initial_state):
        result = plan_node(initial_state)
        plan = result.context.get("plan", {})
        assert "tasks" in plan
        assert result.current_node == "plan"

    def test_code_node(self, initial_state):
        result = code_node(initial_state)
        code = result.context.get("code", {})
        assert "files_created" in code
        assert result.current_node == "code"


# ── Quality gate tests ───────────────────────────────────────────────────────


class TestQualityGates:
    """Tests for default quality gates."""

    def test_idea_gate_passes_with_content(self, initial_state):
        state = idea_node(initial_state)
        gate = DEFAULT_GATES[PipelinePhase.IDEA]
        result = gate.evaluate(state)
        assert result.passed

    def test_idea_gate_fails_empty(self):
        state = GraphState(messages=[])
        state = idea_node(state)
        gate = DEFAULT_GATES[PipelinePhase.IDEA]
        result = gate.evaluate(state)
        assert not result.passed
        assert "empty" in result.reason.lower() or "short" in result.reason.lower()

    def test_research_gate_passes(self, initial_state):
        state = idea_node(initial_state)
        state = research_node(state)
        gate = DEFAULT_GATES[PipelinePhase.RESEARCH]
        result = gate.evaluate(state)
        assert result.passed

    def test_design_gate_passes(self, initial_state):
        state = design_node(initial_state)
        gate = DEFAULT_GATES[PipelinePhase.DESIGN]
        result = gate.evaluate(state)
        assert result.passed

    def test_plan_gate_passes(self, initial_state):
        state = plan_node(initial_state)
        gate = DEFAULT_GATES[PipelinePhase.PLAN]
        result = gate.evaluate(state)
        assert result.passed

    def test_code_gate_fails_no_files(self, initial_state):
        state = code_node(initial_state)
        gate = DEFAULT_GATES[PipelinePhase.CODE]
        result = gate.evaluate(state)
        assert not result.passed
        assert "no files" in result.reason.lower()

    def test_code_gate_passes_with_files(self, initial_state):
        state = code_node(initial_state)
        # Simulate files created
        state.context["code"]["files_created"] = ["app.py", "models.py"]
        gate = DEFAULT_GATES[PipelinePhase.CODE]
        result = gate.evaluate(state)
        assert result.passed

    def test_code_gate_passes_test_only(self, initial_state):
        state = code_node(initial_state)
        state.context["code"]["tests_run"] = True
        gate = DEFAULT_GATES[PipelinePhase.CODE]
        result = gate.evaluate(state)
        assert result.passed


# ── PipelineRunner tests ─────────────────────────────────────────────────────


class TestPipelineRunner:
    """Tests for the full pipeline runner."""

    def test_full_pipeline_success(self, runner, initial_state):
        """Pipeline should complete all 5 phases when gates pass."""
        # We need to simulate code node creating files
        original_code_node = DEFAULT_NODES[PipelinePhase.CODE]

        def code_with_files(state):
            state = original_code_node(state)
            state.context["code"]["files_created"] = ["app.py"]
            return state

        runner.nodes[PipelinePhase.CODE] = code_with_files

        result = runner.run(initial_state)
        assert result.success
        assert result.phase_count == 5
        assert PipelinePhase.CODE in result.completed_phases
        assert result.aborted_at is None
        assert len(result.errors) == 0

    def test_pipeline_aborts_on_gate_failure(self, runner):
        """Pipeline should abort when a gate fails and retries are exhausted."""
        # Custom gate that always fails
        failing_gate = QualityGate(
            name="always_fail",
            check=lambda s: (False, "intentional failure"),
        )
        runner.gates[PipelinePhase.IDEA] = failing_gate

        state = GraphState(messages=[{"role": "user", "content": "test"}])
        result = runner.run(state)
        assert not result.success
        assert result.aborted_at == PipelinePhase.IDEA
        assert len(result.errors) > 0
        assert "intentional failure" in result.errors[0]

    def test_pipeline_retry_then_pass(self, runner, initial_state):
        """A gate that fails first then passes should retry and succeed."""
        call_count = [0]

        def flaky_gate_check(state):
            call_count[0] += 1
            if call_count[0] < 2:
                return (False, "not ready yet")
            return (True, "ready now")

        runner.gates[PipelinePhase.IDEA] = QualityGate(
            name="flaky", check=flaky_gate_check,
        )
        # Also patch code node to produce files so CODE gate passes
        original_code = DEFAULT_NODES[PipelinePhase.CODE]
        def code_ok(state):
            state = original_code(state)
            state.context["code"]["files_created"] = ["app.py"]
            return state
        runner.nodes[PipelinePhase.CODE] = code_ok

        result = runner.run(initial_state)
        assert result.success
        assert call_count[0] >= 2

    def test_partial_pipeline(self, runner, initial_state):
        """Running from RESEARCH to PLAN should skip IDEA."""
        result = runner.run(
            initial_state,
            start_phase=PipelinePhase.RESEARCH,
            end_phase=PipelinePhase.PLAN,
        )
        assert result.success
        assert result.phase_count == 3
        assert PipelinePhase.IDEA not in result.completed_phases
        assert PipelinePhase.RESEARCH in result.completed_phases
        assert PipelinePhase.DESIGN in result.completed_phases
        assert PipelinePhase.PLAN in result.completed_phases

    def test_run_single_phase(self, runner, initial_state):
        """run_phase should execute one phase and return gate result."""
        state, gate_result = runner.run_phase(PipelinePhase.IDEA, initial_state)
        assert state.current_node == "idea"
        assert gate_result.passed

    def test_pipeline_result_properties(self):
        """Test PipelineResult dataclass properties."""
        r = PipelineResult(success=True)
        assert r.phase_count == 0
        assert r.errors == []
        assert r.aborted_at is None

    def test_gate_result_defaults(self):
        """Test GateResult dataclass defaults."""
        r = GateResult(gate_name="test", passed=True)
        assert r.reason == ""
        assert r.timestamp > 0

    def test_custom_gates(self, initial_state):
        """PipelineRunner should accept custom gates."""
        custom_gates = {
            PipelinePhase.IDEA: QualityGate(name="custom", check=lambda s: (True, "custom pass")),
        }
        custom_runner = PipelineRunner(gates=custom_gates, max_retries=0)
        # Only IDEA has a custom gate; others use defaults
        # But we only provided IDEA gate, so others have no gate = auto-pass
        result = custom_runner.run(initial_state, end_phase=PipelinePhase.IDEA)
        assert result.success

    def test_max_retries_zero(self, initial_state):
        """With max_retries=0, first failure aborts immediately."""
        failing_gate = QualityGate(
            name="fail", check=lambda s: (False, "no retries allowed"),
        )
        r = PipelineRunner(max_retries=0)
        r.gates[PipelinePhase.IDEA] = failing_gate
        result = r.run(initial_state)
        assert not result.success
        assert result.aborted_at == PipelinePhase.IDEA

    def test_pipeline_tracks_duration(self, runner, initial_state):
        """Pipeline should track total duration."""
        original_code = DEFAULT_NODES[PipelinePhase.CODE]

        def code_ok(state):
            state = original_code(state)
            state.context["code"]["files_created"] = ["x.py"]
            return state

        runner.nodes[PipelinePhase.CODE] = code_ok
        result = runner.run(initial_state)
        assert result.total_duration_ms >= 0
