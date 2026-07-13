"""Tests for the /usage command and session statistics aggregation."""

import pytest
from lilith_cli.agent import AgentSession
from lilith_cli.commands import UsageCommand
from lilith_cli.extra_commands import run_usage_command
from lilith_cli.config import YggdrasilConfig


def _make_session(model: str = "gpt-4o") -> AgentSession:
    cfg = YggdrasilConfig(provider="openai", model=model)
    return AgentSession(cfg)


@pytest.mark.asyncio
async def test_usage_command_shows_session_stats(capsys):
    """/usage should display tokens, cost, tool calls, message counts and session info."""
    session = _make_session("gpt-4o")
    session._track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 250, "total_tokens": 1250},
        "gpt-4o",
    )
    # Simulate a tool call turn in history.
    session.history.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": "{}"},
                },
                {
                    "id": "tc2",
                    "type": "function",
                    "function": {"name": "directory_list", "arguments": "{}"},
                },
            ],
        },
    )
    session.history.append({"role": "tool", "tool_call_id": "tc1", "content": "file content"})
    session.history.append({"role": "user", "content": "hi"})

    await run_usage_command(session, "")

    captured = capsys.readouterr().out
    assert "Estadísticas de la sesión" in captured
    assert "1,000" in captured
    assert "250" in captured
    assert "1,250" in captured
    assert "file_read: 1" in captured
    assert "directory_list: 1" in captured
    assert "usuario" in captured
    assert "asistente" in captured
    assert "herramienta" in captured
    assert "Inicio" in captured
    assert "Duración" in captured


@pytest.mark.asyncio
async def test_usage_command_json_output(capsys):
    """/usage json should emit machine-readable JSON with expected keys."""
    session = _make_session("gpt-4o-mini")
    session._track_usage(
        {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250},
        "gpt-4o-mini",
    )
    session.history.append({"role": "user", "content": "hello"})

    await run_usage_command(session, "json")

    captured = capsys.readouterr().out
    import json

    data = json.loads(captured.strip())
    assert data["tokens"]["prompt"] == 200
    assert data["tokens"]["completion"] == 50
    assert data["tokens"]["total"] == 250
    assert "total_usd" in data["cost"]
    assert "per_model" in data["cost"]
    assert data["messages"]["user"] == 1
    assert data["session"]["duration_seconds"] >= 0
    assert "duration_human" in data["session"]


@pytest.mark.asyncio
async def test_usage_command_per_model_breakdown(capsys):
    """/usage should show per-model breakdown when multiple models were used."""
    session = _make_session("gpt-4o")
    session._track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 250, "total_tokens": 1250},
        "gpt-4o",
    )
    session._track_usage(
        {"prompt_tokens": 4000, "completion_tokens": 1000, "total_tokens": 5000},
        "gpt-4o-mini",
    )

    await run_usage_command(session, "")

    captured = capsys.readouterr().out
    assert "Desglose por modelo" in captured
    assert "gpt-4o" in captured
    assert "gpt-4o-mini" in captured
