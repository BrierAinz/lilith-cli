"""Tests for /history slash command: timestamps + --tool filter + role colors."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_history_default_no_filter(fake_session, capsys):
    """/history default shows messages with role icons."""
    from lilith_cli.extra_commands import run_history_command

    fake_session.history = [
        {"role": "user", "content": "hola", "timestamp": "2026-07-11T10:00:00"},
        {"role": "assistant", "content": "buenas", "timestamp": "2026-07-11T10:00:05"},
    ]
    _run(run_history_command(fake_session, ""))

    out = capsys.readouterr().out
    # Header with rune
    assert "\u16ed" in out
    assert "Historial" in out
    # Timestamps
    assert "10:00:00" in out
    assert "10:00:05" in out
    # Role icons rendered (Unicode chars)
    assert "\u276f" in out   # user icon
    assert "\u25cb" in out   # assistant icon (○)
    # Role labels
    assert "user:" in out
    assert "assistant:" in out


def test_history_tool_filter(fake_session, capsys):
    """/history --tool file_read only shows file_read entries."""
    from lilith_cli.extra_commands import run_history_command

    fake_session.history = []
    fake_session._tool_call_history = [
        {"name": "file_read", "arguments": {"path": "a.py"}, "timestamp": "2026-07-11T10:00:00"},
        {"name": "shell", "arguments": {"command": "ls"}, "timestamp": "2026-07-11T10:00:05"},
        {"name": "file_read", "arguments": {"path": "b.py"}, "timestamp": "2026-07-11T10:00:10"},
    ]
    _run(run_history_command(fake_session, "--tool file_read"))

    out = capsys.readouterr().out
    assert "filtrado por: file_read" in out
    assert out.count("file_read(") == 2
    assert "shell(" not in out


def test_history_no_timestamps_handled(fake_session, capsys):
    """/history shows placeholder for entries without timestamp."""
    from lilith_cli.extra_commands import run_history_command

    fake_session.history = [{"role": "user", "content": "no timestamp here"}]
    _run(run_history_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "--:--:--" in out


def test_history_empty_session(fake_session, capsys):
    """/history on empty session shows friendly message."""
    from lilith_cli.extra_commands import run_history_command

    fake_session.history = []
    fake_session._tool_call_history = []
    _run(run_history_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "No hay historial" in out


def test_format_history_timestamp_iso():
    """_format_history_timestamp handles ISO strings."""
    from lilith_cli.extra_commands import _format_history_timestamp

    assert _format_history_timestamp("2026-07-11T15:30:45") == "15:30:45"
    assert _format_history_timestamp("2026-07-11T15:30:45Z") == "15:30:45"
    assert _format_history_timestamp(None) == "--:--:--"
    assert _format_history_timestamp("") == "--:--:--"
    assert _format_history_timestamp("garbage") == "--:--:--"


def test_history_role_icons_per_role(fake_session, capsys):
    """/history shows distinct icons for each role."""
    from lilith_cli.extra_commands import run_history_command

    fake_session.history = [
        {"role": "user", "content": "hi", "timestamp": "2026-07-11T10:00:00"},
        {"role": "system", "content": "sys prompt", "timestamp": "2026-07-11T10:00:01"},
        {"role": "assistant", "content": "hello!", "timestamp": "2026-07-11T10:00:02"},
    ]
    _run(run_history_command(fake_session, ""))

    out = capsys.readouterr().out
    # Each role has a distinct Unicode icon
    assert "\u276f" in out  # user: ❯
    assert "\u2699" in out  # system: ⚙
    assert "\u25cb" in out  # assistant: ○


def test_history_role_labels_in_output(fake_session, capsys):
    """/history shows role labels in the format 'role: content'."""
    from lilith_cli.extra_commands import run_history_command

    fake_session.history = [
        {"role": "user", "content": "hi", "timestamp": "2026-07-11T10:00:00"},
        {"role": "assistant", "content": "hello!", "timestamp": "2026-07-11T10:00:01"},
    ]
    _run(run_history_command(fake_session, ""))

    out = capsys.readouterr().out
    lines = [l for l in out.split("\n") if "10:00:" in l]
    assert len(lines) == 2
    assert any("user:" in l for l in lines)
    assert any("assistant:" in l for l in lines)


def test_history_truncates_long_content(fake_session, capsys):
    """/history truncates content at 200 chars (Rich wraps long lines)."""
    from lilith_cli.extra_commands import run_history_command

    long_content = "x" * 250
    fake_session.history = [
        {"role": "user", "content": long_content, "timestamp": "2026-07-11T10:00:00"},
    ]
    _run(run_history_command(fake_session, ""))

    out = capsys.readouterr().out
    # Count x's: should be truncated at 200 (not all 250)
    x_count = out.count("x")
    # Must be <= 200 (not the full 250)
    assert x_count <= 200
    # Ellipsis appended (indicates truncation)
    assert "\u2026" in out