"""Tests for the /retry and /continue slash commands."""

from __future__ import annotations

import pytest


class _DummySession:
    def __init__(self):
        self.history = []
        self._last_user_message = ""


@pytest.mark.asyncio
async def test_retry_command_metadata():
    from lilith_cli.commands import RetryCommand

    cmd = RetryCommand(_DummySession())
    assert cmd.name == "retry"
    assert "reintentar" in cmd.aliases
    assert "reenviar" in cmd.description.lower()


@pytest.mark.asyncio
async def test_continue_command_metadata():
    from lilith_cli.commands import ContinueCommand

    cmd = ContinueCommand(_DummySession())
    assert cmd.name == "continue"
    assert "cont" in cmd.aliases
    assert "respuesta" in cmd.description.lower()


@pytest.mark.asyncio
async def test_retry_no_history_records_error(capfd):
    from lilith_cli.commands import RetryCommand

    session = _DummySession()
    cmd = RetryCommand(session)
    await cmd.execute("")
    captured = capfd.readouterr()
    assert "No hay mensaje anterior" in captured.out


@pytest.mark.asyncio
async def test_continue_appends_prompt(monkeypatch):
    from lilith_cli.commands import ContinueCommand

    session = _DummySession()
    cmd = ContinueCommand(session)

    calls = []

    async def fake_process(session, text):
        calls.append(text)

    def fake_render_turn_start(_):
        pass

    monkeypatch.setattr("lilith_cli.repl._process_with_streaming", fake_process)
    monkeypatch.setattr("lilith_cli.render.render_turn_start", fake_render_turn_start)

    await cmd.execute("")
    assert session.history[-1]["role"] == "user"
    assert "continu" in session.history[-1]["content"].lower()
    assert session._last_user_message == session.history[-1]["content"]
    assert len(calls) == 1
    assert calls[0] == session.history[-1]["content"]
