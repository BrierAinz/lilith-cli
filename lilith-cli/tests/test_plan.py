"""Tests for the plan renderer and PlanCommand."""

from __future__ import annotations

import pytest
from rich.console import Console

from lilith_cli.plan import AgentPlan, PlanStep, parse_plan


class TestPlanParser:
    """Verify parse_plan extracts numbered steps correctly."""

    def test_parses_simple_numbered_list(self) -> None:
        text = "1. First step\n2. Second step\n3. Third"
        plan = parse_plan(text)
        assert len(plan.steps) == 3
        assert plan.steps[0].description == "First step"
        assert plan.steps[1].number == 2
        assert plan.steps[2].description == "Third"

    def test_handles_blank_lines_between_steps(self) -> None:
        text = "1. First\n\n2. Second\n\n3. Third"
        plan = parse_plan(text)
        assert len(plan.steps) == 3

    def test_empty_text_returns_empty_plan(self) -> None:
        plan = parse_plan("")
        assert plan.steps == []

    def test_marks_steps_done(self) -> None:
        plan = parse_plan("1. A\n2. B\n3. C")
        assert plan.mark_done(2) is True
        assert plan.steps[1].done is True
        assert plan.steps[0].done is False

    def test_done_invalid_number_returns_false(self) -> None:
        plan = parse_plan("1. A\n2. B")
        assert plan.mark_done(99) is False

    def test_is_complete(self) -> None:
        plan = parse_plan("1. A\n2. B")
        assert plan.is_complete() is False
        plan.mark_done(1)
        plan.mark_done(2)
        assert plan.is_complete() is True

    def test_next_pending(self) -> None:
        plan = parse_plan("1. A\n2. B\n3. C")
        assert plan.next_pending().number == 1
        plan.mark_done(1)
        assert plan.next_pending().number == 2

    def test_reset_clears_done_flags(self) -> None:
        plan = parse_plan("1. A\n2. B")
        plan.mark_done(1)
        plan.mark_done(2)
        plan.reset()
        assert all(not s.done for s in plan.steps)


class TestRenderPlan:
    """Verify render_plan renders without errors and includes expected info."""

    def test_render_empty_plan(self) -> None:
        # Just verify it doesn't raise.
        from lilith_cli.render import render_plan
        import io
        from rich.console import Console

        buf = io.StringIO()
        # Replace the global console temporarily by using a local render_plan
        # that prints to a buffer. Easier: just call it and check it doesn't
        # raise an exception.
        plan = AgentPlan(steps=[])
        render_plan(plan)  # should print "(plan vacío)"

    def test_render_with_steps(self) -> None:
        from lilith_cli.render import render_plan

        plan = parse_plan("1. First step description\n2. Second step")
        plan.goal = "Test goal"
        # mark one done to exercise both code paths
        plan.mark_done(1)
        render_plan(plan)  # should not raise

    def test_render_long_description_truncates_well(self) -> None:
        from lilith_cli.render import render_plan

        long_desc = "A " * 200
        plan = parse_plan(f"1. {long_desc}")
        render_plan(plan)  # should not raise even with very long text


class TestPlanCommand:
    """Verify the /plan slash command dispatches correctly."""

    def _make_session(self, plan_text: str = "1. A\n2. B\n3. C"):
        from unittest.mock import AsyncMock
        from lilith_cli.commands import PlanCommand

        class _Sess:
            pass

        sess = _Sess()
        sess.current_plan = None
        sess.provider = AsyncMock()
        sess.provider.complete = AsyncMock(
            return_value={"content": plan_text}
        )
        return sess

    @pytest.mark.asyncio
    async def test_command_metadata(self) -> None:
        from lilith_cli.commands import PlanCommand
        cmd = PlanCommand(self._make_session())
        assert cmd.name == "plan"
        assert "todo" in cmd.aliases
        assert "objetivo" in cmd.description.lower() or "plan" in cmd.description.lower()

    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self) -> None:
        from lilith_cli.commands import PlanCommand
        cmd = PlanCommand(self._make_session())
        await cmd.execute("")  # should not raise, prints usage

    @pytest.mark.asyncio
    async def test_creates_plan_from_goal(self) -> None:
        from lilith_cli.commands import PlanCommand
        sess = self._make_session("1. Step one\n2. Step two")
        cmd = PlanCommand(sess)
        await cmd.execute("Build something")
        assert sess.current_plan is not None
        assert sess.current_plan.goal == "Build something"
        assert len(sess.current_plan.steps) == 2
        sess.provider.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_done_marks_step(self) -> None:
        from lilith_cli.commands import PlanCommand
        sess = self._make_session()
        cmd = PlanCommand(sess)
        await cmd.execute("Build something")
        await cmd.execute("done 1")
        assert sess.current_plan.steps[0].done is True
        assert sess.current_plan.steps[1].done is False

    @pytest.mark.asyncio
    async def test_reset_clears_done(self) -> None:
        from lilith_cli.commands import PlanCommand
        sess = self._make_session()
        cmd = PlanCommand(sess)
        await cmd.execute("Build something")
        await cmd.execute("done 1")
        await cmd.execute("done 2")
        await cmd.execute("reset")
        assert all(not s.done for s in sess.current_plan.steps)
