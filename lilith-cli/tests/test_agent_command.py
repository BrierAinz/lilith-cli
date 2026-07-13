"""Tests for the /agent slash command and agent mode policy integration."""

from __future__ import annotations

import pytest

from lilith_cli.agent_modes import (
    AgentMode,
    apply_agent_mode,
    get_agent_mode,
    get_current_agent_mode,
    is_valid_agent_mode,
    list_agent_modes,
)
from lilith_cli.commands import AgentCommand


class DummyConfig:
    """Minimal config stand-in for AgentSession tests."""

    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""
        self.confirm_write = True


class DummySession:
    """Lightweight session that supports the attributes touched by apply_agent_mode."""

    def __init__(self):
        self.config = DummyConfig()
        self.agent_mode = "default"
        self._agent_allow_writes = True
        self._agent_plan_first = False


@pytest.mark.asyncio
async def test_agent_command_default_shows_current_mode():
    session = DummySession()
    cmd = AgentCommand(session)

    assert cmd.name == "agent"
    # Should not raise and should leave mode unchanged.
    await cmd.execute("")
    assert session.agent_mode == "default"


@pytest.mark.asyncio
async def test_agent_command_lists_modes():
    session = DummySession()
    cmd = AgentCommand(session)

    await cmd.execute("list")
    # No exception is the primary assertion; additionally verify the helper.
    modes = list_agent_modes()
    assert len(modes) == 4
    assert any(m.name == "review-only" for m in modes)


@pytest.mark.asyncio
async def test_agent_command_switch_applies_policy():
    session = DummySession()
    cmd = AgentCommand(session)

    await cmd.execute("auto-edit")
    assert session.agent_mode == "auto-edit"
    assert session.config.confirm_write is False
    assert session._agent_allow_writes is True

    await cmd.execute("review-only")
    assert session.agent_mode == "review-only"
    assert session._agent_allow_writes is False
    assert session.config.confirm_write is True


@pytest.mark.asyncio
async def test_agent_command_unknown_mode_errors():
    session = DummySession()
    cmd = AgentCommand(session)

    # Should not raise; invalid input is reported via render_error.
    await cmd.execute("unknown-mode")
    assert session.agent_mode == "default"


def test_agent_mode_helpers():
    assert is_valid_agent_mode("plan-first") is True
    assert is_valid_agent_mode("invalid") is False
    assert get_agent_mode("default") is not None
    assert get_agent_mode("default").confirm_write is True
    assert get_current_agent_mode(DummySession()) == "default"


def test_apply_agent_mode_to_real_session(fake_session):
    """Use the real AgentSession fixture to verify integration."""
    mode = get_agent_mode("auto-edit")
    apply_agent_mode(fake_session, mode)
    assert fake_session.agent_mode == "auto-edit"
    assert fake_session.config.confirm_write is False
    assert fake_session._agent_allow_writes is True
    assert fake_session._agent_plan_first is False
