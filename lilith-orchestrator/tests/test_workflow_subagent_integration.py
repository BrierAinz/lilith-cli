"""WorkflowEngine <-> SubAgentRunner integration tests.

Closes the loop between the SubAgentRunner primitive (lilith_orchestrator.subagents)
and the WorkflowEngine (lilith_orchestrator.workflow). When a WorkflowStep declares
``subagent_type``, the engine should route the step through the runner, filter tools
by the matching SubAgentDefinition, and stash the SubAgentResult on the workflow
context for downstream inspection.

These tests use a stub executor that records spawn calls and returns a fixed output
- no real LLM is invoked.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure the package is importable when running pytest from Asgard root.
_ASGARD = Path(__file__).resolve().parents[1]
if str(_ASGARD) not in sys.path:
    sys.path.insert(0, str(_ASGARD))

from lilith_orchestrator.subagents import (  # noqa: E402
    SubAgentDefinition,
    SubAgentRunner,
    clear_registry,
    register,
)
from lilith_orchestrator.workflow import (  # noqa: E402
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStep,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the global SubAgentDefinition registry between tests."""
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def full_tool_pool():
    """Common tool pool used by all tests."""
    return [
        "read_file",
        "search_files",
        "write_file",
        "patch",
        "terminal",
        "web_search",
        "web_fetch",
    ]


@pytest.fixture
def stub_executor():
    """Async stub executor: returns a deterministic SubAgentResult.

    Records every call so tests can assert the runner actually invoked
    the executor with the expected (filtered) tool pool.

    Signature matches ``SubAgentRunner._spawn_one`` invocation:
    ``(system_prompt, user_input, tool_names, model_preference) -> str``.
    """
    calls: list[dict] = []

    async def _executor(
        system_prompt: str,
        user_input: str,
        tools: list[str],
        model_preference,
    ):
        calls.append(
            {
                "system_prompt": system_prompt,
                "task": user_input,
                "tools": list(tools),
                "model_preference": model_preference,
            }
        )
        return f"STUB[{user_input}]"

    _executor.calls = calls  # type: ignore[attr-defined]
    return _executor


# ─── Step-level routing ─────────────────────────────────────────────────────


class TestWorkflowStepSubagentField:
    """The new subagent_type / subagent_depth fields round-trip via from_dict/to_dict."""

    def test_defaults_are_empty(self):
        step = WorkflowStep(name="x")
        assert step.subagent_type == ""
        assert step.subagent_depth == 0

    def test_from_dict_round_trip(self):
        step = WorkflowStep.from_dict(
            {
                "name": "research",
                "subagent_type": "researcher",
                "subagent_depth": 1,
            }
        )
        assert step.subagent_type == "researcher"
        assert step.subagent_depth == 1

        # Round-trip preserves the new fields when non-zero.
        d = step.to_dict()
        assert d["subagent_type"] == "researcher"
        assert d["subagent_depth"] == 1

    def test_to_dict_omits_empty_fields(self):
        step = WorkflowStep(name="x")
        d = step.to_dict()
        assert "subagent_type" not in d
        assert "subagent_depth" not in d

    def test_from_dict_handles_missing_optional_fields(self):
        step = WorkflowStep.from_dict({"name": "x"})
        assert step.subagent_type == ""
        assert step.subagent_depth == 0


# ─── Engine constructor injection ───────────────────────────────────────────


class TestWorkflowEngineSubagentInjection:
    """WorkflowEngine accepts subagent_runner + full_tool_pool kwargs."""

    def test_default_runner_is_none(self):
        engine = WorkflowEngine()
        assert engine.subagent_runner is None
        # Default tool pool should contain the common Yggdrasil tools.
        assert "read_file" in engine.full_tool_pool
        assert "write_file" in engine.full_tool_pool
        assert "terminal" in engine.full_tool_pool

    def test_custom_tool_pool_is_used(self):
        engine = WorkflowEngine(full_tool_pool=["a", "b", "c"])
        assert engine.full_tool_pool == ["a", "b", "c"]

    def test_tool_pool_is_copied(self):
        original = ["x", "y"]
        engine = WorkflowEngine(full_tool_pool=original)
        engine.full_tool_pool.append("z")
        assert original == ["x", "y"]  # caller's list untouched


# ─── End-to-end routing ─────────────────────────────────────────────────────


class TestSubagentStepRouting:
    """A step with subagent_type routes through the runner, returns its output."""

    def test_step_routes_to_runner(self, stub_executor, full_tool_pool):
        register(
            SubAgentDefinition(
                agent_type="researcher",
                allowed_tools=["read_file", "search_files"],
                disallowed_tools=["write_file", "terminal"],
            )
        )
        runner = SubAgentRunner(
            full_tool_pool=full_tool_pool,
            executor=stub_executor,
        )
        engine = WorkflowEngine(
            subagent_runner=runner, full_tool_pool=full_tool_pool
        )

        wf = WorkflowDefinition(
            name="subagent-wf",
            steps=[
                WorkflowStep(
                    name="research",
                    description="Find usages of X",
                    subagent_type="researcher",
                )
            ],
        )
        result = engine.run(wf, context={})
        assert result.status.value == "completed"
        # The stub executor was invoked exactly once.
        assert len(stub_executor.calls) == 1
        call = stub_executor.calls[0]
        assert call["task"] == "Find usages of X"
        # Tool filter applied: write_file/terminal excluded.
        assert "read_file" in call["tools"]
        assert "search_files" in call["tools"]
        assert "write_file" not in call["tools"]
        assert "terminal" not in call["tools"]
        # Output ends up as the step's output.
        assert result.steps[0].output == "STUB[Find usages of X]"
        assert result.steps[0].agent_used == "subagent:researcher"

    def test_step_stashes_subagent_result_on_context(
        self, stub_executor, full_tool_pool
    ):
        register(
            SubAgentDefinition(
                agent_type="researcher",
                allowed_tools=["read_file"],
            )
        )
        runner = SubAgentRunner(
            full_tool_pool=full_tool_pool, executor=stub_executor
        )
        engine = WorkflowEngine(subagent_runner=runner)

        wf = WorkflowDefinition(
            name="wf",
            steps=[
                WorkflowStep(name="r", description="task", subagent_type="researcher")
            ],
        )
        result = engine.run(wf, context={})
        # The SubAgentResult is stashed on the workflow context so
        # downstream consumers can inspect duration, tools_used, etc.
        assert "last_subagent_result" in result.context
        r = result.context["last_subagent_result"]
        assert r.agent_type == "researcher"
        assert r.success is True
        assert r.tools_used == ["read_file"]

    def test_unknown_subagent_type_raises(self, stub_executor, full_tool_pool):
        runner = SubAgentRunner(
            full_tool_pool=full_tool_pool, executor=stub_executor
        )
        engine = WorkflowEngine(subagent_runner=runner)

        wf = WorkflowDefinition(
            name="wf",
            steps=[
                WorkflowStep(
                    name="x",
                    description="task",
                    subagent_type="nonexistent",
                )
            ],
        )
        # The step's retry loop will catch the ValueError; the step
        # itself ends up FAILED with the error message.
        result = engine.run(wf, context={})
        assert result.steps[0].status.value == "failed"
        assert "nonexistent" in result.steps[0].error

    def test_no_runner_attached_falls_back(
        self, stub_executor, full_tool_pool, caplog
    ):
        """If a step requests a sub-agent but no runner is attached,
        the engine falls back to the default executor (which echoes the
        intent). The step still completes."""
        register(SubAgentDefinition(agent_type="researcher"))
        # NO runner attached to engine.
        engine = WorkflowEngine()

        wf = WorkflowDefinition(
            name="wf",
            steps=[
                WorkflowStep(
                    name="x",
                    description="task",
                    subagent_type="researcher",
                    intent="chat",  # not used, but explicit for clarity
                )
            ],
        )
        result = engine.run(wf, context={})
        assert result.status.value == "completed"
        # The stub executor was NOT invoked.
        assert len(stub_executor.calls) == 0
        # Default executor emits a placeholder for the step.
        assert "researcher" not in result.steps[0].agent_used


# ─── Multi-step workflow ────────────────────────────────────────────────────


class TestMultiStepSubagentWorkflow:
    """A workflow with multiple sub-agent steps (sequential)."""

    def test_two_subagent_steps_in_sequence(self, stub_executor, full_tool_pool):
        register(
            SubAgentDefinition(agent_type="researcher", allowed_tools=["read_file"])
        )
        register(
            SubAgentDefinition(agent_type="editor", allowed_tools=["read_file", "patch"])
        )
        runner = SubAgentRunner(
            full_tool_pool=full_tool_pool, executor=stub_executor
        )
        engine = WorkflowEngine(subagent_runner=runner)

        wf = WorkflowDefinition(
            name="research-then-edit",
            steps=[
                WorkflowStep(
                    name="research",
                    description="gather context",
                    subagent_type="researcher",
                    output_key="research_output",
                ),
                WorkflowStep(
                    name="edit",
                    description="apply patch",
                    subagent_type="editor",
                    input_key="research_output",
                ),
            ],
        )
        result = engine.run(wf, context={})
        assert result.status.value == "completed"
        # Both steps invoked the runner.
        assert len(stub_executor.calls) == 2
        assert stub_executor.calls[1]["task"] == "apply patch"


# ─── YAML parsing ───────────────────────────────────────────────────────────


class TestYAMLParsingWithSubagent:
    """Workflow YAML can declare subagent_type on a step."""

    def test_yaml_subagent_round_trip(self, stub_executor, full_tool_pool):
        yaml_text = """
name: parse-wf
description: parse + dispatch via runner
steps:
  - name: research
    description: investigate X
    subagent_type: researcher
    subagent_depth: 1
    gate:
      type: min_length
      min_length: 1
"""
        engine = WorkflowEngine()
        wf = engine.parse_yaml(yaml_text)
        assert len(wf.steps) == 1
        s = wf.steps[0]
        assert s.subagent_type == "researcher"
        assert s.subagent_depth == 1

        # Round-trip via to_dict preserves the fields.
        d = wf.to_dict()
        assert d["steps"][0]["subagent_type"] == "researcher"
        assert d["steps"][0]["subagent_depth"] == 1


# ─── Stub executor failure path ─────────────────────────────────────────────


class TestSubagentStepFailure:
    """If the stub executor returns success=False, the step surfaces the error."""

    def test_failed_subagent_is_reported(self, full_tool_pool):
        register(SubAgentDefinition(agent_type="researcher"))

        async def failing_executor(system_prompt, user_input, tools, model_pref):
            raise RuntimeError("synthetic boom")

        runner = SubAgentRunner(
            full_tool_pool=full_tool_pool, executor=failing_executor
        )
        engine = WorkflowEngine(subagent_runner=runner)

        wf = WorkflowDefinition(
            name="wf",
            steps=[
                WorkflowStep(
                    name="x", description="task", subagent_type="researcher"
                )
            ],
        )
        result = engine.run(wf, context={})
        # The failure is surfaced in the step output.
        assert "FAILED" in result.steps[0].output
        assert "synthetic boom" in result.steps[0].output
        assert result.steps[0].agent_used == "subagent:researcher"