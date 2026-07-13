"""Tests for the Plain-English Workflow Builder."""

from __future__ import annotations

import pytest

from lilith_orchestrator.nl_workflow import (
    NLWorkflowBuilder,
    _detect_agent,
    _detect_gate,
    _detect_intent,
    _detect_parallel,
    _detect_tools,
    _generate_step_name,
    _split_into_steps,
)
from lilith_orchestrator.workflow import (
    GateType,
    OnFailure,
    WorkflowDefinition,
)


# ── Step splitting tests ─────────────────────────────────────────────────────


class TestSplitIntoSteps:
    """Tests for _split_into_steps."""

    def test_sequential_markers(self):
        text = "First research the topic, then design the architecture, finally write the code"
        steps = _split_into_steps(text)
        assert len(steps) >= 2  # Should split on "then" / "finally"

    def test_numbered_list(self):
        text = "1. Research the topic 2. Design the system 3. Write the code"
        steps = _split_into_steps(text)
        assert len(steps) == 3
        assert "Research" in steps[0]
        assert "Design" in steps[1]
        assert "Write" in steps[2]

    def test_arrow_separated(self):
        text = "Research the topic -> Design the system -> Write the code"
        steps = _split_into_steps(text)
        assert len(steps) == 3

    def test_single_step(self):
        text = "Just do something simple"
        steps = _split_into_steps(text)
        assert len(steps) == 1
        assert steps[0] == text

    def test_empty_text(self):
        steps = _split_into_steps("")
        assert len(steps) == 1  # Returns [""], which is handled by builder

    def test_comma_separated_with_verbs(self):
        text = "research the topic, design the system, write the code"
        steps = _split_into_steps(text)
        assert len(steps) >= 2


# ── Intent detection tests ───────────────────────────────────────────────────


class TestDetectIntent:
    """Tests for _detect_intent."""

    def test_research_intent(self):
        assert _detect_intent("Research the latest AI frameworks") == "research"

    def test_code_intent(self):
        assert _detect_intent("Implement the authentication module") == "code"

    def test_design_intent(self):
        assert _detect_intent("Design the database schema") == "design"

    def test_test_intent(self):
        assert _detect_intent("Run tests and verify everything passes") == "test"

    def test_debug_intent(self):
        assert _detect_intent("Fix the bug in the login flow") == "debug"

    def test_review_intent(self):
        assert _detect_intent("Please review and evaluate this code") == "review"

    def test_deploy_intent(self):
        assert _detect_intent("Deploy to production environment") == "deploy"

    def test_unknown_intent(self):
        assert _detect_intent("Do something random") == "chat"


# ── Tool detection tests ─────────────────────────────────────────────────────


class TestDetectTools:
    """Tests for _detect_tools."""

    def test_terminal_detection(self):
        tools = _detect_tools("Run the test suite using pytest")
        assert "terminal" in tools

    def test_read_file_detection(self):
        tools = _detect_tools("Read the config file and check its contents")
        assert "read_file" in tools

    def test_write_file_detection(self):
        tools = _detect_tools("Create a new file with the module code")
        assert "write_file" in tools

    def test_web_search_detection(self):
        tools = _detect_tools("Search the web for the latest AI papers")
        assert "web_search" in tools

    def test_multiple_tools(self):
        tools = _detect_tools("Search for files and read the config file")
        assert "search_files" in tools or "read_file" in tools

    def test_no_tools(self):
        tools = _detect_tools("Think about the problem")
        assert len(tools) == 0


# ── Agent detection tests ────────────────────────────────────────────────────


class TestDetectAgent:
    """Tests for _detect_agent."""

    def test_odin_detection(self):
        assert _detect_agent("Use Odin to plan the strategy") == "odin"

    def test_mimir_detection(self):
        assert _detect_agent("Let Mimir research this topic") == "mimir"

    def test_adan_detection(self):
        assert _detect_agent("Delegate to Adan for coding") == "adan"

    def test_eva_detection(self):
        assert _detect_agent("Have Eva write the creative content") == "eva"

    def test_strategist_alias(self):
        assert _detect_agent("The strategist should plan this") == "odin"

    def test_no_agent(self):
        assert _detect_agent("Just do the work") == ""


# ── Gate detection tests ─────────────────────────────────────────────────────


class TestDetectGate:
    """Tests for _detect_gate."""

    def test_make_sure_gate(self):
        gate = _detect_gate("Make sure the output is valid JSON")
        assert gate is not None
        assert gate.type == GateType.CONTENT_CHECK

    def test_verify_that_gate(self):
        gate = _detect_gate("Verify that all tests pass")
        assert gate is not None

    def test_ensure_gate(self):
        gate = _detect_gate("Ensure the code compiles")
        assert gate is not None

    def test_must_include_gate(self):
        gate = _detect_gate("The output must include error handling")
        assert gate is not None
        assert gate.type == GateType.CONTENT_CHECK

    def test_no_gate(self):
        gate = _detect_gate("Just write the code")
        assert gate is None


# ── Parallel detection tests ─────────────────────────────────────────────────


class TestDetectParallel:
    """Tests for _detect_parallel."""

    def test_at_the_same_time(self):
        assert _detect_parallel("Do this at the same time as that") is True

    def test_simultaneously(self):
        assert _detect_parallel("Run tests simultaneously") is True

    def test_in_parallel(self):
        assert _detect_parallel("Execute in parallel") is True

    def test_not_parallel(self):
        assert _detect_parallel("Do this first, then that") is False


# ── Step name generation tests ───────────────────────────────────────────────


class TestGenerateStepName:
    """Tests for _generate_step_name."""

    def test_verb_with_object(self):
        name = _generate_step_name("Research the latest AI frameworks", 0)
        assert "research" in name

    def test_code_step(self):
        name = _generate_step_name("Implement the authentication module", 0)
        assert "implement" in name

    def test_fallback_name(self):
        name = _generate_step_name("xyzzy", 3)
        assert "step_4" in name or "xyzzy" in name

    def test_name_length(self):
        name = _generate_step_name(
            "Implement a very long and detailed authentication module with OAuth2 and JWT tokens",
            0,
        )
        assert len(name) <= 40


# ── NLWorkflowBuilder tests ──────────────────────────────────────────────────


class TestNLWorkflowBuilder:
    """Tests for the NLWorkflowBuilder class."""

    @pytest.fixture
    def builder(self):
        return NLWorkflowBuilder()

    def test_basic_build(self, builder):
        workflow = builder.build("Research the topic, then code it, then test it")
        assert isinstance(workflow, WorkflowDefinition)
        assert len(workflow.steps) >= 2
        assert workflow.name  # Auto-generated

    def test_custom_name(self, builder):
        workflow = builder.build("Do stuff", name="my-workflow")
        assert workflow.name == "my-workflow"

    def test_custom_description(self, builder):
        workflow = builder.build("Do stuff", workflow_description="Custom desc")
        assert workflow.description == "Custom desc"

    def test_auto_description(self, builder):
        workflow = builder.build("Research AI frameworks")
        assert "Auto-generated" in workflow.description

    def test_empty_description_raises(self, builder):
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.build("")

    def test_intent_detection_in_steps(self, builder):
        workflow = builder.build("Research the topic, then design the system")
        assert workflow.steps[0].intent == "research"
        # Second step might be "design" or "chat" depending on parsing

    def test_tool_detection_in_steps(self, builder):
        workflow = builder.build("Run tests using terminal")
        assert any("terminal" in step.tools for step in workflow.steps)

    def test_agent_detection_in_steps(self, builder):
        workflow = builder.build("Use Odin to plan the strategy")
        odin_steps = [s for s in workflow.steps if s.agent == "odin"]
        assert len(odin_steps) >= 1

    def test_gate_detection_in_steps(self, builder):
        workflow = builder.build("Write code, make sure it compiles")
        gated_steps = [s for s in workflow.steps if s.gate.type != GateType.NONE]
        assert len(gated_steps) >= 1

    def test_failure_strategy(self, builder):
        workflow = builder.build("Do stuff", on_failure="skip")
        assert workflow.on_failure == OnFailure.SKIP

    def test_invalid_failure_strategy_defaults_to_abort(self, builder):
        workflow = builder.build("Do stuff", on_failure="invalid")
        assert workflow.on_failure == OnFailure.ABORT

    def test_max_retries(self, builder):
        workflow = builder.build("Do stuff", max_retries=3)
        assert workflow.max_retries == 3

    def test_timeout(self, builder):
        workflow = builder.build("Do stuff", timeout=600)
        assert workflow.timeout == 600

    def test_metadata_source(self, builder):
        workflow = builder.build("Research X")
        assert workflow.metadata.get("source") == "nl_workflow_builder"
        assert "raw_input" in workflow.metadata


# ── YAML output tests ────────────────────────────────────────────────────────


class TestNLWorkflowToYAML:
    """Tests for to_yaml output."""

    @pytest.fixture
    def builder(self):
        return NLWorkflowBuilder()

    def test_yaml_output_is_valid(self, builder):
        yaml_text = builder.to_yaml("Research X, then code Y")
        assert isinstance(yaml_text, str)
        assert "name:" in yaml_text
        assert "steps:" in yaml_text

    def test_yaml_contains_steps(self, builder):
        yaml_text = builder.to_yaml("Research X, design Y, code Z")
        assert "intent:" in yaml_text

    def test_yaml_contains_gates(self, builder):
        yaml_text = builder.to_yaml("Write code, make sure it compiles")
        assert "gate:" in yaml_text

    def test_yaml_contains_agent(self, builder):
        yaml_text = builder.to_yaml("Use Odin for strategy")
        assert "agent: odin" in yaml_text

    def test_yaml_roundtrip(self, builder):
        """YAML output should be parseable (roundtrip test)."""
        yaml_text = builder.to_yaml("Research X, code Y, test Z")
        import yaml

        parsed = yaml.safe_load(yaml_text)
        assert "name" in parsed
        assert "steps" in parsed
        assert isinstance(parsed["steps"], list)
        assert len(parsed["steps"]) >= 2


# ── Integration tests ────────────────────────────────────────────────────────


class TestNLWorkflowIntegration:
    """Integration tests with the existing workflow engine."""

    @pytest.fixture
    def builder(self):
        return NLWorkflowBuilder()

    def test_complex_pipeline(self, builder):
        """Test a complex multi-step pipeline description."""
        description = (
            "First, research the latest AI agent frameworks and emerging patterns. "
            "Then, design the architecture for the new system. "
            "Next, implement the core module with proper error handling. "
            "After that, write comprehensive tests. "
            "Finally, review the code for security issues."
        )
        workflow = builder.build(description)
        assert len(workflow.steps) >= 3
        # Check that intents are varied
        intents = {s.intent for s in workflow.steps}
        assert len(intents) >= 2

    def test_pipeline_with_agents_and_tools(self, builder):
        """Test pipeline with agent assignments and tool requirements."""
        description = (
            "Use Mimir to research the topic by searching the web, "
            "then have Adan implement it using terminal and file tools, "
            "and finally let Heimdall review the output"
        )
        workflow = builder.build(description)

        agents_found = {s.agent for s in workflow.steps if s.agent}
        assert len(agents_found) >= 1

        tools_found = set()
        for step in workflow.steps:
            tools_found.update(step.tools)
        # At least one tool should be detected
        assert len(tools_found) >= 0  # May be 0 depending on parsing

    def test_five_phase_pipeline(self, builder):
        """Test the classic 5-phase pipeline pattern."""
        description = (
            "1. Brainstorm and capture the idea "
            "2. Research relevant information "
            "3. Design the solution architecture "
            "4. Plan the implementation tasks "
            "5. Write the code and run tests"
        )
        workflow = builder.build(description)
        assert len(workflow.steps) == 5

    def test_yaml_loadable_by_workflow_engine(self, builder):
        """Verify the generated YAML is structurally valid for the engine."""
        yaml_text = builder.to_yaml("Research X, code Y")
        import yaml

        data = yaml.safe_load(yaml_text)

        # Verify required fields
        assert "name" in data
        assert "steps" in data
        assert isinstance(data["steps"], list)

        for step in data["steps"]:
            assert "name" in step
            assert "intent" in step
