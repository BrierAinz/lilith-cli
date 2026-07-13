"""Integration tests: PolicyEngine wired into WorkflowEngine."""
from __future__ import annotations

import pytest

from lilith_orchestrator.policy import (
    PolicyConfig,
    PolicyDecision,
    PolicyEngine,
)
from lilith_orchestrator.workflow import (
    OnFailure,
    StepStatus,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStep,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def simple_workflow():
    """2-step workflow: research → code."""
    return WorkflowDefinition(
        name="test_wf",
        steps=[
            WorkflowStep(name="research", intent="research"),
            WorkflowStep(name="implement", intent="code"),
        ],
        on_failure=OnFailure.ABORT,
    )


# ── No policy → no behavior change ──────────────────────────────────────────


class TestNoPolicyEngine:
    def test_engine_works_without_policy(self, simple_workflow):
        engine = WorkflowEngine()
        result = engine.run(simple_workflow)
        assert result.status.value == "completed"
        assert all(s.status == StepStatus.PASSED for s in result.steps)

    def test_engine_with_none_policy_same_as_no_policy(self, simple_workflow):
        engine = WorkflowEngine(policy_engine=None)
        result = engine.run(simple_workflow)
        assert result.status.value == "completed"


# ── Allow-all policy → no behavior change ───────────────────────────────────


class TestAllowAllPolicy:
    def test_default_policy_lets_everything_through(self, simple_workflow):
        engine = WorkflowEngine(policy_engine=PolicyEngine())
        result = engine.run(simple_workflow)
        assert result.status.value == "completed"
        assert all(s.status == StepStatus.PASSED for s in result.steps)

    def test_audit_all_records_steps(self, simple_workflow):
        cfg = PolicyConfig(audit_all=True)
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(simple_workflow)
        assert result.status.value == "completed"
        # audit_all uses synthetic agent names (auto(research), auto(code))
        audit_a = engine.policy_engine.audit("auto(research)")
        audit_b = engine.policy_engine.audit("auto(code)")
        assert len(audit_a) >= 1
        assert len(audit_b) >= 1


# ── Forbidden intent → step denied ─────────────────────────────────────────


class TestForbiddenIntent:
    def test_code_intent_blocked(self, simple_workflow):
        cfg = PolicyConfig(forbidden_tools={"code"})
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(simple_workflow)
        assert result.status.value == "aborted"
        # First step (research) passes, second (code) fails with POLICY DENY
        assert result.steps[0].status == StepStatus.PASSED
        assert result.steps[1].status == StepStatus.FAILED
        assert "POLICY DENY" in result.steps[1].error
        assert "forbidden" in result.steps[1].error.lower()


# ── Allowed-only whitelist ──────────────────────────────────────────────────


class TestAllowedOnlyWhitelist:
    def test_only_research_allowed(self, simple_workflow):
        cfg = PolicyConfig(allowed_tools={"research"})
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(simple_workflow)
        # research passes, code is not in allowed → denied
        assert result.steps[0].status == StepStatus.PASSED
        assert result.steps[1].status == StepStatus.FAILED
        assert "POLICY DENY" in result.steps[1].error


# ── Path-level policy ───────────────────────────────────────────────────────


class TestPathPolicy:
    def test_forbidden_path_blocks_step(self, simple_workflow):
        cfg = PolicyConfig(forbidden_paths={"/etc"})
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(simple_workflow, context={"path": "/etc/passwd"})
        assert result.status.value == "aborted"
        assert "POLICY DENY" in result.steps[0].error
        assert "PATH_NOT_ALLOWED" in result.steps[0].error or "path" in result.steps[0].error.lower()

    def test_allowed_path_lets_step_through(self, simple_workflow):
        cfg = PolicyConfig(allowed_paths={"/work"})
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(simple_workflow, context={"path": "/work/file.py"})
        assert result.status.value == "completed"


# ── Resource limits ─────────────────────────────────────────────────────────


class TestResourceLimits:
    def test_tool_call_limit_triggers_deny(self):
        # Workflow with 5 steps, policy allows only 2 tool calls
        wf = WorkflowDefinition(
            name="long_wf",
            steps=[WorkflowStep(name=f"step_{i}", intent="research") for i in range(5)],
        )
        cfg = PolicyConfig(max_tool_calls=2)
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(wf)
        # First 2 pass, 3rd onwards denied
        assert result.steps[0].status == StepStatus.PASSED
        assert result.steps[1].status == StepStatus.PASSED
        assert result.steps[2].status == StepStatus.FAILED
        assert "POLICY DENY" in result.steps[2].error


# ── attach_policy after construction ────────────────────────────────────────


class TestAttachPolicy:
    def test_can_attach_policy_after_init(self, simple_workflow):
        engine = WorkflowEngine()  # no policy initially
        result1 = engine.run(simple_workflow)
        assert result1.status.value == "completed"

        # Now attach a policy that forbids code
        cfg = PolicyConfig(forbidden_tools={"code"})
        engine.attach_policy(PolicyEngine(cfg))
        result2 = engine.run(simple_workflow)
        assert result2.status.value == "aborted"
        assert result2.steps[1].status == StepStatus.FAILED

    def test_can_replace_policy(self, simple_workflow):
        engine = WorkflowEngine(policy_engine=PolicyEngine())
        result1 = engine.run(simple_workflow)
        assert result1.status.value == "completed"

        # Replace with restrictive policy
        cfg = PolicyConfig(forbidden_tools={"research", "code"})
        engine.attach_policy(PolicyEngine(cfg))
        result2 = engine.run(simple_workflow)
        assert result2.status.value == "aborted"


# ── on_failure=SKIP behavior ─────────────────────────────────────────────────


class TestSkipOnPolicyDeny:
    def test_skip_continues_after_policy_deny(self):
        wf = WorkflowDefinition(
            name="skip_wf",
            steps=[
                WorkflowStep(name="research", intent="research"),
                WorkflowStep(name="code", intent="code"),
                WorkflowStep(name="final", intent="research"),
            ],
            on_failure=OnFailure.SKIP,
        )
        cfg = PolicyConfig(forbidden_tools={"code"})
        engine = WorkflowEngine(policy_engine=PolicyEngine(cfg))
        result = engine.run(wf)
        # Workflow completes (no abort), code step is failed, others pass
        assert result.status.value == "completed"
        assert result.steps[0].status == StepStatus.PASSED
        assert result.steps[1].status == StepStatus.FAILED
        assert result.steps[2].status == StepStatus.PASSED
