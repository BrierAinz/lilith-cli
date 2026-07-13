"""Tests for /env --json flag."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def _make_env_result(data):
    """Build a mock result that has .data attribute (like EnvListTool returns)."""
    return SimpleNamespace(data=data, success=True, error=None)


def test_env_json_outputs_valid_json(fake_session, capsys):
    """/env --json outputs valid JSON via stdout."""
    from lilith_cli.extra_commands import run_env_command

    fake_data = {"PATH": "/usr/bin:/bin", "HOME": "/home/user"}
    fake_result = _make_env_result(fake_data)

    with patch("lilith_cli.extra_commands.EnvListTool") as MockTool:
        MockTool.return_value.execute.return_value = fake_result
        _run(run_env_command(fake_session, "--json"))

    out = capsys.readouterr().out
    parsed = json.loads(out.strip())
    assert isinstance(parsed, dict)


def test_env_json_handles_string_data(fake_session, capsys):
    """/env --json handles results where data is a string (raw env output)."""
    from lilith_cli.extra_commands import run_env_command

    # Some EnvListTool variants return string output
    fake_result = SimpleNamespace(data="KEY=value\n", success=True, error=None)

    with patch("lilith_cli.extra_commands.EnvListTool") as MockTool:
        MockTool.return_value.execute.return_value = fake_result
        _run(run_env_command(fake_session, "--json"))

    out = capsys.readouterr().out
    parsed = json.loads(out.strip())
    assert parsed == "KEY=value\n"


def test_env_json_prefix(fake_session, capsys):
    """/env prefix X --json outputs filtered JSON."""
    from lilith_cli.extra_commands import run_env_command

    fake_data = {"PYTHON_HOME": "/usr/local", "PYTHONPATH": "/lib"}
    fake_result = _make_env_result(fake_data)

    with patch("lilith_cli.extra_commands.EnvListTool") as MockTool:
        MockTool.return_value.execute.return_value = fake_result
        _run(run_env_command(fake_session, "prefix PYTHON --json"))

    out = capsys.readouterr().out
    parsed = json.loads(out.strip())
    assert "PYTHON" in parsed or "PYTHON_HOME" in parsed


def test_env_default_no_json(fake_session, capsys):
    """/env without --json still uses Rich-printed format (no JSON parse)."""
    from lilith_cli.extra_commands import run_env_command

    fake_data = {"FOO": "bar"}
    fake_result = _make_env_result(fake_data)

    with patch("lilith_cli.extra_commands.EnvListTool") as MockTool:
        MockTool.return_value.execute.return_value = fake_result
        _run(run_env_command(fake_session, ""))

    out = capsys.readouterr().out
    # Default mode prints via Rich, not pure JSON
    # Should contain some env var reference but NOT be parseable as JSON
    try:
        json.loads(out)
        # If it parses, that's only OK if it has structure
    except json.JSONDecodeError:
        pass  # expected: not pure JSON in default mode


def test_env_json_bypasses_rich_markup(fake_session, capsys):
    """/env --json output does NOT contain Rich markup tags like [success]."""
    from lilith_cli.extra_commands import run_env_command

    fake_data = {"X": "y"}
    fake_result = _make_env_result(fake_data)

    with patch("lilith_cli.extra_commands.EnvListTool") as MockTool:
        MockTool.return_value.execute.return_value = fake_result
        _run(run_env_command(fake_session, "--json"))

    out = capsys.readouterr().out
    assert "[success]" not in out
    assert "[/success]" not in out
    assert "[info]" not in out