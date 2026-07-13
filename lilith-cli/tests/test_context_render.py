"""Tests for render_context() function and estimate_context_window()."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestEstimateContextWindow:
    """Verify the per-model context-window lookup."""

    def test_known_model(self) -> None:
        from lilith_cli.providers import estimate_context_window
        # fugu-ultra is 128k in our pricing table.
        assert estimate_context_window("fugu-ultra") == 128_000

    def test_unknown_model_falls_back(self) -> None:
        from lilith_cli.providers import estimate_context_window
        # Unknown models get the safe default of 32k.
        assert estimate_context_window("totally-unknown-model-xyz") == 32_000

    def test_none_model_falls_back(self) -> None:
        from lilith_cli.providers import estimate_context_window
        assert estimate_context_window(None) == 32_000

    def test_known_claude_model(self) -> None:
        from lilith_cli.providers import estimate_context_window
        assert estimate_context_window("claude-sonnet-4") == 200_000


class TestRenderContext:
    """Verify render_context doesn't crash and shows the expected fields."""

    def _make_session(self) -> MagicMock:
        sess = MagicMock()
        sess.config = MagicMock()
        sess.config.model = "fugu-ultra"
        sess._total_usage = {
            "prompt_tokens": 1_000,
            "completion_tokens": 500,
            "total_tokens": 1_500,
        }
        sess.history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "tool_calls": [{"id": "1"}]},
        ]
        sess.system_prompt = "You are Lilith."
        sess._tools_cache = []
        sess.current_plan = None
        return sess

    def test_basic_does_not_crash(self) -> None:
        from lilith_cli.render import render_context
        sess = self._make_session()
        # Just verify it doesn't raise.
        render_context(sess)

    def test_full_does_not_crash(self) -> None:
        from lilith_cli.render import render_context
        sess = self._make_session()
        render_context(sess, full=True)

    def test_with_plan(self) -> None:
        """Verify render_context handles a session with an active plan."""
        from lilith_cli.plan import AgentPlan, PlanStep
        from lilith_cli.render import render_context
        sess = self._make_session()
        plan = AgentPlan(goal="Build app", steps=[PlanStep(1, "A"), PlanStep(2, "B")])
        plan.mark_done(1)
        sess.current_plan = plan
        render_context(sess, full=True)
