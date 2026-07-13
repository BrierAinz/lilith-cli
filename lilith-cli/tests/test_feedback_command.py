"""Tests for the /feedback slash command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_cli.commands import FeedbackCommand, CommandRegistry


class _DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""


class _DummySession:
    def __init__(self) -> None:
        self.config = _DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.fixture
def tmp_feedback_path(monkeypatch, tmp_path: Path) -> Path:
    """Override the feedback storage path to a temporary directory."""
    feedback_path = tmp_path / "feedback.json"
    monkeypatch.setattr(
        "lilith_cli.commands._FEEDBACK_PATH",
        feedback_path,
    )
    return feedback_path


@pytest.mark.asyncio
async def test_feedback_command_submit(tmp_feedback_path: Path) -> None:
    """FeedbackCommand should persist a rating and optional comment."""
    session = _DummySession()
    cmd = FeedbackCommand(session)
    assert cmd.name == "feedback"

    await cmd.execute("4 Muy útil")

    assert tmp_feedback_path.exists()
    data = json.loads(tmp_feedback_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["rating"] == 4
    assert data[0]["comment"] == "Muy útil"
    assert data[0]["id"] == 1


@pytest.mark.asyncio
async def test_feedback_command_list_stats_clear(tmp_feedback_path: Path) -> None:
    """FeedbackCommand should list, aggregate stats, and clear feedback."""
    session = _DummySession()
    cmd = FeedbackCommand(session)

    await cmd.execute("5 Excelente")
    await cmd.execute("3 Regular")
    await cmd.execute("list")
    await cmd.execute("stats")
    await cmd.execute("clear")

    data = json.loads(tmp_feedback_path.read_text(encoding="utf-8"))
    assert data == []


@pytest.mark.asyncio
async def test_feedback_command_registry(fake_session) -> None:
    """FeedbackCommand should be discoverable by the CommandRegistry."""
    registry = CommandRegistry(fake_session)
    registry.discover()
    assert registry.get("feedback") is not None
    assert registry.get("fb") is not None
