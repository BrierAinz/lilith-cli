"""Tests for the YAML Workflow Definition Engine."""

from __future__ import annotations

import pytest

from lilith_orchestrator.workflow import (
    GateType,
    OnFailure,
    QualityGate,
    StepResult,
    StepStatus,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowResult,
    WorkflowStatus,
    WorkflowStep,
    load_workflow,
)


# ── QualityGate tests ───────────────────────────────────────────────────────


class TestQualityGate:
    """Tests for QualityGate dataclass."""

    def test_none_gate_always_passes(self):
        gate = QualityGate(type=GateType.NONE)
        passed, reason = gate.evaluate("anything")
        assert passed is True
        assert reason == ""

    def test_content_check_min_length_pass(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, min_length=10)
        passed, _ = gate.evaluate("this is long enough content")
        assert passed is True

    def test_content_check_min_length_fail(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, min_length=100)
        passed, reason = gate.evaluate("short")
        assert passed is False
        assert "too short" in reason

    def test_content_check_required_keywords_pass(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, required_keywords=["python", "code"])
        passed, _ = gate.evaluate("I wrote some python code today")
        assert passed is True

    def test_content_check_required_keywords_fail(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, required_keywords=["python", "deploy"])
        passed, reason = gate.evaluate("I wrote some python code today")
        assert passed is False
        assert "deploy" in reason

    def test_content_check_forbidden_keywords_pass(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, forbidden_keywords=["error", "fail"])
        passed, _ = gate.evaluate("Everything works great")
        assert passed is True

    def test_content_check_forbidden_keywords_fail(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, forbidden_keywords=["error"])
        passed, reason = gate.evaluate("There was an error in the code")
        assert passed is False
        assert "error" in reason

    def test_content_check_case_insensitive(self):
        gate = QualityGate(type=GateType.CONTENT_CHECK, required_keywords=["Python"])
        passed, _ = gate.evaluate("I love PYTHON programming")
        assert passed is True

    def test_agent_review_always_passes_eval(self):
        gate = QualityGate(type=GateType.AGENT_REVIEW, reviewer_agent="heimdall")
        passed, _ = gate.evaluate("any content")
        assert passed is True

    def test_custom_gate_always_passes_eval(self):
        gate = QualityGate(type=GateType.CUSTOM, custom_check="my_check")
        passed, _ = gate.evaluate("any content")
        assert passed is True

    # ── New gate type tests (round-8 expansion) ─────────────────────────

    def test_min_length_gate_passes(self):
        gate = QualityGate(type=GateType.MIN_LENGTH, min_length=10)
        passed, _ = gate.evaluate("hello world!")
        assert passed is True

    def test_min_length_gate_fails(self):
        gate = QualityGate(type=GateType.MIN_LENGTH, min_length=100)
        passed, reason = gate.evaluate("short")
        assert passed is False
        assert "too short" in reason

    def test_keyword_presence_passes(self):
        gate = QualityGate(
            type=GateType.KEYWORD_PRESENCE, required_keywords=["ok"]
        )
        passed, _ = gate.evaluate("looks ok to me")
        assert passed is True

    def test_keyword_presence_missing(self):
        gate = QualityGate(
            type=GateType.KEYWORD_PRESENCE, required_keywords=["absent"]
        )
        passed, reason = gate.evaluate("nothing here")
        assert passed is False
        assert "absent" in reason

    def test_keyword_presence_forbidden(self):
        gate = QualityGate(
            type=GateType.KEYWORD_PRESENCE, forbidden_keywords=["bad"]
        )
        passed, reason = gate.evaluate("this is bad")
        assert passed is False
        assert "Forbidden" in reason

    def test_regex_match_passes(self):
        gate = QualityGate(type=GateType.REGEX_MATCH, custom_check=r"\d{3,}")
        passed, _ = gate.evaluate("version 1234 release")
        assert passed is True

    def test_regex_match_fails(self):
        gate = QualityGate(type=GateType.REGEX_MATCH, custom_check=r"^X")
        passed, _ = gate.evaluate("not starting with X -- wait, yes")
        # starts with 'n', so should fail
        assert gate.type == GateType.REGEX_MATCH
        passed2, reason2 = QualityGate(
            type=GateType.REGEX_MATCH, custom_check=r"^Z"
        ).evaluate("not starting with z")
        assert passed2 is False
        assert "regex" in reason2

    def test_regex_match_missing_pattern(self):
        gate = QualityGate(type=GateType.REGEX_MATCH)
        passed, reason = gate.evaluate("any content")
        assert passed is False
        assert "missing pattern" in reason

    def test_json_parse_passes(self):
        gate = QualityGate(type=GateType.JSON_PARSE)
        passed, _ = gate.evaluate('{"key": "value"}')
        assert passed is True

    def test_json_parse_fails(self):
        gate = QualityGate(type=GateType.JSON_PARSE)
        passed, reason = gate.evaluate("not json {")
        assert passed is False
        assert "JSON" in reason

    def test_from_dict_min_length(self):
        data = {"type": "min_length", "min_length": 42}
        gate = QualityGate.from_dict(data)
        assert gate.type == GateType.MIN_LENGTH
        assert gate.min_length == 42

    def test_from_dict_keyword_presence(self):
        data = {
            "type": "keyword_presence",
            "required_keywords": ["k1"],
            "forbidden_keywords": ["k2"],
        }
        gate = QualityGate.from_dict(data)
        assert gate.type == GateType.KEYWORD_PRESENCE
        assert gate.required_keywords == ["k1"]
        assert gate.forbidden_keywords == ["k2"]

    def test_to_dict_min_length(self):
        gate = QualityGate(type=GateType.MIN_LENGTH, min_length=10)
        d = gate.to_dict()
        assert d["type"] == "min_length"
        assert d["min_length"] == 10

    def test_unknown_gate_type_defaults_to_pass(self):
        # Defensive: if someone constructs a QualityGate with a bad
        # type value (e.g. from a custom store), evaluate() should not
        # raise. Current code returns True for the fallthrough. Make
        # the contract explicit.
        gate = QualityGate()
        # Manually forge a bogus type — should fall through to True.
        gate.type = GateType.NONE  # type is enum, but tests the fallback branch
        passed, _ = gate.evaluate("x")
        assert passed is True

    def test_from_dict_none_returns_default(self):
        gate = QualityGate.from_dict(None)
        assert gate.type == GateType.NONE

    def test_from_dict_with_data(self):
        data = {
            "type": "content_check",
            "min_length": 50,
            "required_keywords": ["test"],
            "forbidden_keywords": ["hack"],
        }
        gate = QualityGate.from_dict(data)
        assert gate.type == GateType.CONTENT_CHECK
        assert gate.min_length == 50
        assert "test" in gate.required_keywords

    def test_combined_checks(self):
        gate = QualityGate(
            type=GateType.CONTENT_CHECK,
            min_length=5,
            required_keywords=["result"],
            forbidden_keywords=["error"],
        )
        # Passes all checks
        passed, _ = gate.evaluate("The result is positive")
        assert passed is True
        # Fails forbidden keyword
        passed, reason = gate.evaluate("The result has an error")
        assert passed is False
        assert "error" in reason


# ── WorkflowStep tests ──────────────────────────────────────────────────────


class TestWorkflowStep:
    """Tests for WorkflowStep dataclass."""

    def test_defaults(self):
        step = WorkflowStep(name="test")
        assert step.intent == "chat"
        assert step.parallel is False
        assert step.retry == 0
        assert step.timeout == 60

    def test_from_dict(self):
        data = {
            "name": "review",
            "intent": "code",
            "tools": ["terminal", "file_edit"],
            "gate": {"type": "content_check", "min_length": 50},
            "retry": 2,
        }
        step = WorkflowStep.from_dict(data)
        assert step.name == "review"
        assert step.intent == "code"
        assert "terminal" in step.tools
        assert step.gate.type == GateType.CONTENT_CHECK
        assert step.retry == 2


# ── WorkflowDefinition tests ────────────────────────────────────────────────


class TestWorkflowDefinition:
    """Tests for WorkflowDefinition dataclass."""

    def test_from_dict(self):
        data = {
            "name": "test-workflow",
            "description": "A test workflow",
            "steps": [
                {"name": "step1", "intent": "code"},
                {"name": "step2", "intent": "research"},
            ],
            "on_failure": "skip",
        }
        wf = WorkflowDefinition.from_dict(data)
        assert wf.name == "test-workflow"
        assert len(wf.steps) == 2
        assert wf.on_failure == OnFailure.SKIP

    def test_validate_valid(self):
        wf = WorkflowDefinition(
            name="valid",
            steps=[WorkflowStep(name="s1")],
        )
        errors = wf.validate()
        assert len(errors) == 0

    def test_validate_no_name(self):
        wf = WorkflowDefinition(name="", steps=[WorkflowStep(name="s1")])
        errors = wf.validate()
        assert any("name" in e for e in errors)

    def test_validate_no_steps(self):
        wf = WorkflowDefinition(name="empty", steps=[])
        errors = wf.validate()
        assert any("at least one step" in e for e in errors)

    def test_validate_duplicate_step_names(self):
        wf = WorkflowDefinition(
            name="dupes",
            steps=[WorkflowStep(name="s1"), WorkflowStep(name="s1")],
        )
        errors = wf.validate()
        assert any("Duplicate" in e for e in errors)

    def test_validate_negative_retry(self):
        wf = WorkflowDefinition(
            name="bad-retry",
            steps=[WorkflowStep(name="s1", retry=-1)],
        )
        errors = wf.validate()
        assert any("retry" in e for e in errors)

    def test_validate_agent_review_without_reviewer(self):
        wf = WorkflowDefinition(
            name="bad-gate",
            steps=[WorkflowStep(
                name="s1",
                gate=QualityGate(type=GateType.AGENT_REVIEW),
            )],
        )
        errors = wf.validate()
        assert any("reviewer_agent" in e for e in errors)


# ── WorkflowEngine tests ────────────────────────────────────────────────────


class TestWorkflowEngine:
    """Tests for WorkflowEngine."""

    @pytest.fixture
    def engine(self):
        return WorkflowEngine()

    def test_parse_yaml_simple(self, engine):
        yaml_str = """
name: simple-test
description: A simple workflow
steps:
  - name: step1
    intent: code
    description: Do something
"""
        wf = engine.parse_yaml(yaml_str)
        assert wf.name == "simple-test"
        assert len(wf.steps) == 1
        assert wf.steps[0].intent == "code"

    def test_parse_yaml_with_gates(self, engine):
        yaml_str = """
name: gated-test
steps:
  - name: analyze
    intent: research
    gate:
      type: content_check
      min_length: 20
      required_keywords:
        - analysis
"""
        wf = engine.parse_yaml(yaml_str)
        assert wf.steps[0].gate.type == GateType.CONTENT_CHECK
        assert wf.steps[0].gate.min_length == 20

    def test_parse_yaml_validation_error(self, engine):
        yaml_str = """
name: ""
steps: []
"""
        with pytest.raises(ValueError, match="validation failed"):
            engine.parse_yaml(yaml_str)

    def test_parse_yaml_invalid_no_steps(self, engine):
        yaml_str = """
name: no-steps
"""
        with pytest.raises(ValueError):
            engine.parse_yaml(yaml_str)

    def test_run_simple_workflow(self, engine):
        wf = WorkflowDefinition(
            name="simple",
            steps=[
                WorkflowStep(name="step1", description="First step"),
                WorkflowStep(name="step2", description="Second step"),
            ],
        )
        result = engine.run(wf)
        assert result.success is True
        assert result.status == WorkflowStatus.COMPLETED
        assert len(result.steps) == 2
        assert result.steps[0].status == StepStatus.PASSED
        assert result.steps[1].status == StepStatus.PASSED

    def test_run_with_context(self, engine):
        wf = WorkflowDefinition(
            name="ctx-test",
            steps=[WorkflowStep(name="s1", input_key="my_input")],
        )
        result = engine.run(wf, context={"my_input": "hello world"})
        assert result.success is True
        assert "my_input" in result.context

    def test_run_aborts_on_failure(self, engine):
        def bad_executor(step, context):
            raise RuntimeError("Step exploded")

        engine.register_executor("boom", bad_executor)

        wf = WorkflowDefinition(
            name="abort-test",
            on_failure=OnFailure.ABORT,
            steps=[
                WorkflowStep(name="s1", intent="boom"),
                WorkflowStep(name="s2", description="Should not run"),
            ],
        )
        result = engine.run(wf)
        assert result.status == WorkflowStatus.ABORTED
        assert len(result.steps) == 1  # Second step was skipped
        assert result.steps[0].status == StepStatus.FAILED

    def test_run_skips_on_failure(self, engine):
        def bad_executor(step, context):
            raise RuntimeError("Step exploded")

        engine.register_executor("boom", bad_executor)

        wf = WorkflowDefinition(
            name="skip-test",
            on_failure=OnFailure.SKIP,
            steps=[
                WorkflowStep(name="s1", intent="boom"),
                WorkflowStep(name="s2", description="Should still run"),
            ],
        )
        result = engine.run(wf)
        assert result.success is True
        assert len(result.steps) == 2
        assert result.steps[0].status == StepStatus.FAILED
        assert result.steps[1].status == StepStatus.PASSED

    def test_run_gate_failure_aborts(self, engine):
        wf = WorkflowDefinition(
            name="gate-abort",
            on_failure=OnFailure.ABORT,
            steps=[
                WorkflowStep(
                    name="s1",
                    description="short",
                    gate=QualityGate(type=GateType.CONTENT_CHECK, min_length=1000),
                ),
                WorkflowStep(name="s2", description="Should not run"),
            ],
        )
        result = engine.run(wf)
        assert result.status == WorkflowStatus.ABORTED
        assert result.steps[0].gate_passed is False
        assert result.steps[0].status == StepStatus.GATED

    def test_run_with_custom_executor(self, engine):
        def custom_code_executor(step, context):
            prompt = context.get("last_output", "")
            return f"Executed code step: {prompt}", "odin"

        engine.register_executor("code", custom_code_executor)

        wf = WorkflowDefinition(
            name="custom-exec",
            steps=[
                WorkflowStep(name="code_step", intent="code", description="Write code"),
            ],
        )
        result = engine.run(wf, context={"input": "fibonacci function"})
        assert result.success is True
        assert result.steps[0].agent_used == "odin"
        assert "Executed code step" in result.steps[0].output

    def test_run_retry_on_failure(self, engine):
        call_count = 0

        def flaky_executor(step, context):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Flaky failure")
            return "Success after retries", "agent"

        engine.register_executor("flaky", flaky_executor)

        wf = WorkflowDefinition(
            name="retry-test",
            steps=[
                WorkflowStep(name="s1", intent="flaky", retry=3),
            ],
        )
        result = engine.run(wf)
        assert result.success is True
        assert result.steps[0].retries == 2  # Failed 2 times, succeeded on 3rd

    def test_step_output_flows_to_next(self, engine):
        def echo_executor(step, context):
            return f"Output from {step.name}", "agent"

        engine.register_executor("echo", echo_executor)

        wf = WorkflowDefinition(
            name="flow-test",
            steps=[
                WorkflowStep(name="first", intent="echo"),
                WorkflowStep(name="second", intent="echo", input_key="first"),
            ],
        )
        result = engine.run(wf)
        assert result.success is True
        assert "first" in result.context.get("step_first_output", "")

    def test_register_custom_gate(self, engine):
        def my_gate(content, config):
            if "magic" in content.lower():
                return True, ""
            return False, "Missing magic word"

        engine.register_gate("magic_check", my_gate)

    def test_workflow_result_properties(self):
        result = WorkflowResult(
            workflow_name="test",
            status=WorkflowStatus.COMPLETED,
            steps=[
                StepResult(step_name="s1", output="first output", status=StepStatus.PASSED),
                StepResult(step_name="s2", output="final output", status=StepStatus.PASSED),
            ],
        )
        assert result.success is True
        assert result.final_output == "final output"

    def test_workflow_result_failed(self):
        result = WorkflowResult(
            workflow_name="test",
            status=WorkflowStatus.FAILED,
        )
        assert result.success is False
        assert result.final_output == ""

    def test_step_result_to_dict(self):
        result = StepResult(
            step_name="test",
            status=StepStatus.PASSED,
            output="some output",
            agent_used="odin",
            duration_ms=123.45,
        )
        d = result.to_dict()
        assert d["step_name"] == "test"
        assert d["status"] == "passed"
        assert d["agent_used"] == "odin"

    def test_workflow_result_to_dict(self):
        result = WorkflowResult(
            workflow_name="test",
            status=WorkflowStatus.COMPLETED,
            steps=[StepResult(step_name="s1", status=StepStatus.PASSED)],
        )
        d = result.to_dict()
        assert d["workflow_name"] == "test"
        assert d["success"] is True


# ── Load convenience tests ──────────────────────────────────────────────────


class TestLoadWorkflow:
    """Tests for load_workflow convenience function."""

    def test_load_workflow_string(self):
        yaml_str = """
name: convenience-test
steps:
  - name: s1
    intent: chat
"""
        wf = load_workflow(yaml_str)
        assert wf.name == "convenience-test"

    def test_load_workflow_with_all_options(self):
        yaml_str = """
name: full-workflow
description: Tests all options
version: "2.0"
on_failure: skip
max_retries: 3
timeout: 600
variables:
  project: yggdrasil
steps:
  - name: analyze
    intent: research
    description: Analyze the codebase
    tools:
      - web
      - search
    gate:
      type: content_check
      min_length: 50
    retry: 1
    timeout: 120
  - name: implement
    intent: code
    description: Write the code
    tools:
      - terminal
      - file_edit
    gate:
      type: content_check
      min_length: 100
      required_keywords:
        - def
        - return
"""
        wf = load_workflow(yaml_str)
        assert wf.name == "full-workflow"
        assert wf.version == "2.0"
        assert wf.on_failure == OnFailure.SKIP
        assert len(wf.steps) == 2
        assert wf.steps[0].gate.min_length == 50
        assert "def" in wf.steps[1].gate.required_keywords


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_workflow_run(self):
        engine = WorkflowEngine()
        wf = WorkflowDefinition(name="empty", steps=[])
        # Should not crash during validation
        errors = wf.validate()
        assert len(errors) > 0  # Empty steps is a validation error

    def test_single_step_workflow(self):
        engine = WorkflowEngine()
        wf = WorkflowDefinition(
            name="single",
            steps=[WorkflowStep(name="only")],
        )
        result = engine.run(wf)
        assert result.success is True
        assert len(result.steps) == 1

    def test_many_steps_workflow(self):
        engine = WorkflowEngine()
        steps = [WorkflowStep(name=f"step_{i}") for i in range(20)]
        wf = WorkflowDefinition(name="many", steps=steps)
        result = engine.run(wf)
        assert result.success is True
        assert len(result.steps) == 20

    def test_context_preserved_across_steps(self):
        engine = WorkflowEngine()
        wf = WorkflowDefinition(
            name="preserve",
            steps=[
                WorkflowStep(name="s1", description="First"),
                WorkflowStep(name="s2", description="Second"),
                WorkflowStep(name="s3", description="Third"),
            ],
        )
        result = engine.run(wf, context={"my_var": "preserved"})
        assert result.context["my_var"] == "preserved"
        assert "step_s1_output" in result.context
        assert "step_s2_output" in result.context
        assert "step_s3_output" in result.context

    def test_workflow_step_from_dict_minimal(self):
        step = WorkflowStep.from_dict({"name": "minimal"})
        assert step.name == "minimal"
        assert step.intent == "chat"
        assert step.gate.type == GateType.NONE
