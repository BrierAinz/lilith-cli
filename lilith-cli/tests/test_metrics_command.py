"""Tests for the /metrics slash command."""

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.commands import CommandRegistry, MetricsCommand
from lilith_cli.extra_commands import run_metrics_command
from lilith_cli.config import YggdrasilConfig


def _make_session(model: str = "gpt-4o") -> AgentSession:
    cfg = YggdrasilConfig(provider="openai", model=model)
    return AgentSession(cfg)


@pytest.mark.asyncio
async def test_metrics_command_summary(capsys):
    """/metrics should show aggregated tool, command and file-edit metrics."""
    session = _make_session("gpt-4o")
    session._track_usage(
        {"prompt_tokens": 300, "completion_tokens": 100, "total_tokens": 400},
        "gpt-4o",
    )
    session._tool_call_history = [
        {"name": "file_read", "duration": 0.1},
        {"name": "file_read", "duration": 0.3},
        {"name": "terminal", "duration": 0.5},
    ]
    session._command_history = [
        {"name": "help"},
        {"name": "help"},
        {"name": "tools"},
    ]
    session._file_edit_history = [
        {"path": "src/main.py", "tool": "file_write"},
        {"path": "src/main.py", "tool": "file_edit"},
        {"path": "tests/test_x.py", "tool": "file_write"},
    ]

    await run_metrics_command(session, "")

    captured = capsys.readouterr().out
    assert "Métricas de la sesión" in captured
    assert "300" in captured
    assert "100" in captured
    assert "400" in captured
    assert "file_read: 2" in captured
    assert "terminal: 1" in captured
    assert "/help: 2" in captured
    assert "/tools: 1" in captured
    assert "src/main.py: 2" in captured
    assert "tests/test_x.py: 1" in captured


@pytest.mark.asyncio
async def test_metrics_command_subcommands(capsys):
    """/metrics tools, /metrics commands and /metrics files should emit detailed tables."""
    session = _make_session("gpt-4o")
    session._tool_call_history = [
        {"name": "search_files", "duration": 0.2},
        {"name": "search_files", "duration": 0.4},
    ]
    session._command_history = [{"name": "cost"}, {"name": "cost"}, {"name": "usage"}]
    session._file_edit_history = [{"path": "README.md", "tool": "file_write"}]

    for subcmd, expected_title in [
        ("tools", "Métricas de herramientas"),
        ("commands", "Métricas de comandos"),
        ("files", "Métricas de archivos editados"),
    ]:
        await run_metrics_command(session, subcmd)
        captured = capsys.readouterr().out
        assert expected_title in captured

    # tools subcommand should contain a table with counts and average duration.
    await run_metrics_command(session, "tools")
    captured = capsys.readouterr().out
    assert "search_files" in captured
    assert "2" in captured
    # Average duration of (0.2 + 0.4) / 2 = 0.3s
    assert "300ms" in captured

    # files subcommand should show file path and edit count.
    await run_metrics_command(session, "files")
    captured = capsys.readouterr().out
    assert "README.md" in captured
    assert "1" in captured


@pytest.mark.asyncio
async def test_metrics_command_json(capsys):
    """/metrics json should emit a JSON payload with all expected sections."""
    session = _make_session("gpt-4o-mini")
    session._track_usage(
        {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
        "gpt-4o-mini",
    )
    session._tool_call_history = [{"name": "directory_list", "duration": 0.15}]
    session._command_history = [{"name": "metrics"}]
    session._file_edit_history = [{"path": "pyproject.toml", "tool": "file_edit"}]

    await run_metrics_command(session, "json")

    import json

    captured = capsys.readouterr().out.strip()
    data = json.loads(captured)
    assert data["tokens"]["total_tokens"] == 75
    assert data["tools"]["total"] == 1
    assert data["tools"]["counts"]["directory_list"] == 1
    assert "directory_list" in data["tools"]["average_duration"]
    assert data["commands"]["metrics"] == 1
    assert data["files"]["pyproject.toml"] == 1
    assert "duration_seconds" in data["session"]


@pytest.mark.asyncio
async def test_metrics_command_registry_registered():
    """The /metrics command should be discoverable in the registry."""
    session = _make_session()
    registry = CommandRegistry(session)
    registry.discover()
    assert registry.get("metrics") is not None
    assert registry.get("mtr") is not None
    assert registry.get("metrics").name == "metrics"
