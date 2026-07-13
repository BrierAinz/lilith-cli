"""Tests for LilithEngine.process_workflow() integration.

Tests the wiring of WorkflowEngine → TaskDispatcher → LilithEngine,
including hook lifecycle, error handling, and agent dispatch.
"""
import pytest
from unittest.mock import MagicMock, patch

from lilith_orchestrator.engine import LilithEngine, EngineUsage
from lilith_orchestrator.workflow import (
    WorkflowStatus,
    StepStatus,
    GateType,
)


# ── Helpers ────────────────────────────────────────────────────────────────


class _StubConfig:
    """Minimal config stub for LilithEngine."""
    model = "test-model"
    base_url = "http://localhost:1234/v1"
    api_key = "test-key"
    max_tokens = 256
    temperature = 0.7
    system_prompt = "You are a test assistant."
    cache_size = 16
    cache_ttl_seconds = 60.0
    token_budget = 10000


def _make_engine() -> LilithEngine:
    """Create a LilithEngine with stub config and a fresh hook registry."""
    engine = LilithEngine(config=_StubConfig())
    # Clear any hooks from previous tests (singleton registry)
    from lilith_core.hooks import HookType
    engine._hooks._hooks = {ht: [] for ht in HookType}
    return engine


SIMPLE_WORKFLOW = """\
name: test-pipeline
description: Simple test workflow
steps:
  - name: step1
    intent: code
    description: Write a hello world function
  - name: step2
    intent: research
    description: Research best practices
on_failure: abort
"""

GATED_WORKFLOW = """\
name: gated-pipeline
steps:
  - name: step1
    intent: code
    description: Generate code
    gate:
      type: content_check
      min_length: 10
  - name: step2
    intent: chat
    description: Summarize
on_failure: abort
"""

WORKFLOW_WITH_RETRY = """\
name: retry-pipeline
steps:
  - name: flaky_step
    intent: code
    description: Might fail
    retry: 2
on_failure: abort
"""


# ── Basic Workflow Processing ──────────────────────────────────────────────


class TestProcessWorkflowBasic:
    """Basic workflow processing tests."""

    def test_process_workflow_returns_dict(self):
        """process_workflow() returns a dict with expected keys."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "Generated output",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert isinstance(result, dict)
        assert "workflow_result" in result
        assert "success" in result
        assert "final_output" in result
        assert "steps_completed" in result
        assert "total_duration_ms" in result

    def test_process_workflow_success(self):
        """A simple workflow completes successfully."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "Here is the code output with enough content",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert result["success"] is True
        assert result["steps_completed"] == 2
        assert result["workflow_result"]["status"] == "completed"

    def test_process_workflow_final_output(self):
        """final_output is the last step's output."""
        engine = _make_engine()
        call_count = [0]
        responses = ["First step output", "Second step output"]

        def mock_fallback(message, context, session_id=""):
            resp = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return {"response": resp, "usage": EngineUsage(), "tool_call": None}

        with patch.object(engine, "_process_llm_fallback", side_effect=mock_fallback):
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert result["final_output"] == "Second step output"

    def test_process_workflow_with_context(self):
        """Initial context is passed through to steps."""
        engine = _make_engine()
        captured_contexts = []

        def mock_fallback(message, context, session_id=""):
            captured_contexts.append(dict(context))
            return {"response": "output", "usage": EngineUsage(), "tool_call": None}

        with patch.object(engine, "_process_llm_fallback", side_effect=mock_fallback):
            engine.process_workflow(SIMPLE_WORKFLOW, context={"user_query": "test"})

        # At least one call should have user_query in context
        assert any("user_query" in ctx for ctx in captured_contexts)


# ── Error Handling ─────────────────────────────────────────────────────────


class TestProcessWorkflowErrors:
    """Error handling in workflow processing."""

    def test_missing_steps_returns_error(self):
        """Workflow with missing steps key returns error."""
        engine = _make_engine()
        result = engine.process_workflow("name: empty\n")

        assert result["success"] is False
        assert "error" in result

    def test_empty_steps_returns_error(self):
        """Workflow with no steps returns error."""
        engine = _make_engine()
        result = engine.process_workflow("name: empty\nsteps: []")

        assert result["success"] is False
        assert "error" in result

    def test_llm_failure_returns_error(self):
        """When LLM fails, workflow handles the exception."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.side_effect = Exception("LLM unavailable")
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        # Workflow should still return a result
        assert "workflow_result" in result
        assert result["total_duration_ms"] >= 0


# ── Gated Workflows ────────────────────────────────────────────────────────


class TestProcessWorkflowGated:
    """Tests for workflows with quality gates."""

    def test_gate_passes_with_long_content(self):
        """Content check gate passes when output meets min_length."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "A" * 50,  # Well above min_length: 10
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(GATED_WORKFLOW)

        assert result["success"] is True

    def test_gate_fails_with_short_content(self):
        """Content check gate blocks when output is too short."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "Hi",  # Below min_length: 10
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(GATED_WORKFLOW)

        # Gate failure should abort the workflow
        assert result["success"] is False
        assert result["workflow_result"]["status"] == "aborted"


# ── Agent Dispatch ─────────────────────────────────────────────────────────


class TestProcessWorkflowDispatch:
    """Tests for agent dispatch integration."""

    def test_dispatch_creates_dispatcher(self):
        """_create_dispatcher returns None when Vanaheim not found."""
        engine = _make_engine()
        with patch("os.path.isdir", return_value=False):
            dispatcher = engine._create_dispatcher()
        # Should return None gracefully when Vanaheim dir not found
        assert dispatcher is None

    def test_workflow_runs_without_dispatcher(self):
        """Workflow still runs when dispatcher is None (no agent cards)."""
        engine = _make_engine()
        with patch.object(engine, "_create_dispatcher", return_value=None):
            with patch.object(engine, "_process_llm_fallback") as mock_llm:
                mock_llm.return_value = {
                    "response": "Output content here",
                    "usage": EngineUsage(),
                    "tool_call": None,
                }
                result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert result["success"] is True

    def test_dispatch_with_agent_cards(self):
        """When agent cards are available, dispatch selects the right agent."""
        engine = _make_engine()

        # Mock dispatcher that returns a card
        mock_card = MagicMock()
        mock_card.name = "Odin"
        mock_card.role = "Code architect"
        mock_card.tools = ["terminal", "file_edit"]

        mock_dispatcher = MagicMock()
        mock_dispatcher.route.return_value = mock_card

        with patch.object(engine, "_create_dispatcher", return_value=mock_dispatcher):
            with patch.object(engine, "_process_llm_fallback") as mock_llm:
                mock_llm.return_value = {
                    "response": "Code output from Odin",
                    "usage": EngineUsage(),
                    "tool_call": None,
                }
                result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert result["success"] is True
        # Dispatcher should have been called for each step
        assert mock_dispatcher.route.call_count >= 2


# ── Hook Lifecycle ─────────────────────────────────────────────────────────


class TestProcessWorkflowHooks:
    """Tests for hook integration in workflow processing."""

    def test_pre_llm_hook_can_abort_step(self):
        """pre_llm_call hook returning None aborts the step."""
        from lilith_core.hooks import HookType

        engine = _make_engine()
        abort_calls = []

        def abort_hook(ctx):
            if ctx.hook_type == HookType.PRE_LLM_CALL:
                abort_calls.append(ctx.data.get("step"))
                return None  # Abort
            return ctx

        engine._hooks.register(HookType.PRE_LLM_CALL, abort_hook)

        result = engine.process_workflow(SIMPLE_WORKFLOW)

        # At least the first step should have been aborted by hook
        assert len(abort_calls) >= 1

    def test_post_llm_hook_can_rewrite_output(self):
        """post_llm_call hook can modify step output."""
        from lilith_core.hooks import HookType

        engine = _make_engine()

        def rewrite_hook(ctx):
            if ctx.hook_type == HookType.POST_LLM_CALL:
                ctx.data["response"] = "REWRITTEN: " + ctx.data.get("response", "")
            return ctx

        engine._hooks.register(HookType.POST_LLM_CALL, rewrite_hook)

        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "original output",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        # The output should be rewritten
        assert "REWRITTEN" in result["final_output"]


# ── Stats and Cache ────────────────────────────────────────────────────────


class TestProcessWorkflowStats:
    """Tests for stats tracking in workflow processing."""

    def test_workflow_updates_request_count(self):
        """process_workflow increments the request counter."""
        engine = _make_engine()
        initial_count = engine._request_count

        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "output",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            engine.process_workflow(SIMPLE_WORKFLOW)

        assert engine._request_count == initial_count + 1

    def test_workflow_tracks_latency(self):
        """process_workflow updates total latency."""
        engine = _make_engine()

        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "output",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert result["total_duration_ms"] >= 0
        assert engine._total_latency_ms >= 0


# ── Workflow Result Structure ──────────────────────────────────────────────


class TestProcessWorkflowResult:
    """Tests for the workflow result structure."""

    def test_workflow_result_has_all_fields(self):
        """Result dict has all required fields."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "output",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert "workflow_result" in result
        assert "success" in result
        assert "final_output" in result
        assert "steps_completed" in result
        assert "total_duration_ms" in result

    def test_workflow_result_dict_serializable(self):
        """Workflow result dict is JSON-serializable."""
        import json
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "output",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        # Should not raise
        json_str = json.dumps(result)
        assert isinstance(json_str, str)

    def test_steps_completed_count(self):
        """steps_completed counts passed steps correctly."""
        engine = _make_engine()
        with patch.object(engine, "_process_llm_fallback") as mock_llm:
            mock_llm.return_value = {
                "response": "Good output with enough content",
                "usage": EngineUsage(),
                "tool_call": None,
            }
            result = engine.process_workflow(SIMPLE_WORKFLOW)

        assert result["steps_completed"] == 2
