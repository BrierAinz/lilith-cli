"""Tests for streaming plan progress display in the REPL toolbar."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure lilith_cli is importable when running tests directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.plan import AgentPlan, PlanStep


@pytest.fixture
def fake_session():
    """Return a lightweight AgentSession with a mocked provider."""
    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)
    session.provider = MagicMock()
    session.provider.stream = AsyncMock(return_value=iter([]))
    return session


def test_get_plan_progress_str_empty_when_no_plan(fake_session):
    """An empty string is returned when no plan is active."""
    assert fake_session.get_plan_progress_str() == ""


def test_get_plan_progress_str_shows_current_step(fake_session):
    """The progress string marks done steps and highlights the current one."""
    fake_session.current_plan = AgentPlan(
        goal="Refactor auth",
        steps=[
            PlanStep(number=1, description="Read file", done=True),
            PlanStep(number=2, description="Edit config", done=False),
            PlanStep(number=3, description="Run tests", done=False),
        ],
    )
    progress = fake_session.get_plan_progress_str()

    assert progress.startswith("[Plan: 1/3]")
    assert "✓ Read file" in progress
    assert "▶ Edit config" in progress
    assert "· Run tests" in progress


def test_get_plan_progress_str_completed_plan(fake_session):
    """When every step is done, there is no current step arrow."""
    fake_session.current_plan = AgentPlan(
        goal="Ship it",
        steps=[
            PlanStep(number=1, description="Format", done=True),
            PlanStep(number=2, description="Commit", done=True),
        ],
    )
    progress = fake_session.get_plan_progress_str()

    assert "[Plan: 2/2]" in progress
    assert "✓ Format" in progress
    assert "✓ Commit" in progress
    assert "▶" not in progress
