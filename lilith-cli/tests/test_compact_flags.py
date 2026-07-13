"""Tests for /compact --dry-run, --force, and --keep-last flags."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_compact_dry_run_does_not_modify_history(fake_session, capsys):
    """/compact --dry-run shows summary without modifying session.history."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [
        {"role": "user", "content": "msg 1"},
        {"role": "assistant", "content": "msg 2"},
        {"role": "user", "content": "msg 3"},
    ]
    history_before = list(fake_session.history)

    with patch("lilith_cli.extra_commands._compact_messages", return_value="summary text"):
        _run(run_compact_command(fake_session, "--dry-run"))

    out = capsys.readouterr().out
    assert "Dry-run" in out or "dry-run" in out
    # History NOT modified
    assert fake_session.history == history_before


def test_compact_dry_run_with_count(fake_session, capsys):
    """/compact 2 --dry-run reports correct count."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
        {"role": "user", "content": "e"},
    ]

    with patch("lilith_cli.extra_commands._compact_messages", return_value="sum"):
        _run(run_compact_command(fake_session, "2 --dry-run"))

    out = capsys.readouterr().out
    assert "2 mensajes" in out


def test_compact_force_overrides_warning(fake_session, capsys):
    """/compact with large N requires --force."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [{"role": "user", "content": f"msg {i}"} for i in range(10)]

    # Without --force and large N: should warn, not apply
    with patch("lilith_cli.extra_commands._compact_messages", return_value="sum"):
        _run(run_compact_command(fake_session, "7"))
    out = capsys.readouterr().out
    assert "70%" in out or "force" in out.lower()
    # History unchanged
    assert len(fake_session.history) == 10

    # With --force: should apply
    with patch("lilith_cli.extra_commands._compact_messages", return_value="sum"):
        _run(run_compact_command(fake_session, "7 --force"))
    out = capsys.readouterr().out
    assert "compactados" in out


def test_compact_invalid_count_shows_usage(fake_session, capsys):
    """/compact with non-numeric argument shows usage."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [{"role": "user", "content": "x"}]

    _run(run_compact_command(fake_session, "abc"))
    out = capsys.readouterr().out
    assert "Uso:" in out


def test_compact_small_count_applies_without_force(fake_session, capsys):
    """/compact 1 (small) applies directly without --force."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [{"role": "user", "content": f"msg {i}"} for i in range(10)]

    with patch("lilith_cli.extra_commands._compact_messages", return_value="sum"):
        _run(run_compact_command(fake_session, "1"))

    out = capsys.readouterr().out
    assert "compactados" in out


def test_compact_keep_last_preserves_recent(fake_session, capsys):
    """/compact --keep-last 3 keeps last 3 messages after compaction."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [
        {"role": "user", "content": f"msg {i}"} for i in range(10)
    ]

    with patch("lilith_cli.extra_commands._compact_messages", return_value="summary"):
        # Compact 5 of first 7, keep last 3
        _run(run_compact_command(fake_session, "5 --keep-last 3 --force"))

    out = capsys.readouterr().out
    assert "compactados" in out
    assert "conservados" in out
    # New history = [summary] + [last 3]
    assert len(fake_session.history) == 4  # 1 summary + 3 kept
    # Last 3 originals preserved
    assert fake_session.history[-1]["content"] == "msg 9"
    assert fake_session.history[-2]["content"] == "msg 8"
    assert fake_session.history[-3]["content"] == "msg 7"


def test_compact_keep_last_no_explicit_n(fake_session, capsys):
    """/compact --keep-last 2 (no count) compacts all-but-last."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [
        {"role": "user", "content": f"msg {i}"} for i in range(6)
    ]

    with patch("lilith_cli.extra_commands._compact_messages", return_value="summary"):
        # No explicit n, so compact 6 - 2 = 4 messages
        _run(run_compact_command(fake_session, "--keep-last 2 --force"))

    out = capsys.readouterr().out
    # 4 compacted + 2 kept = 5 total (1 summary + 2 originals)
    assert "compactados" in out
    assert "conservados" in out
    assert len(fake_session.history) == 3  # 1 summary + 2 kept
    assert fake_session.history[-1]["content"] == "msg 5"
    assert fake_session.history[-2]["content"] == "msg 4"


def test_compact_keep_last_invalid_value(fake_session, capsys):
    """/compact --keep-last abc shows error."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [{"role": "user", "content": "x"}]

    _run(run_compact_command(fake_session, "--keep-last abc"))
    out = capsys.readouterr().out
    assert "integer" in out.lower() or "entero" in out.lower() or "Uso:" in out


def test_compact_keep_last_dry_run(fake_session, capsys):
    """/compact --dry-run --keep-last 3 reports both numbers."""
    from lilith_cli.extra_commands import run_compact_command

    fake_session.history = [{"role": "user", "content": f"m{i}"} for i in range(10)]

    with patch("lilith_cli.extra_commands._compact_messages", return_value="s"):
        _run(run_compact_command(fake_session, "4 --dry-run --keep-last 3"))

    out = capsys.readouterr().out
    assert "Dry-run" in out
    assert "conserv" in out.lower()  # "conservarán" / "conservados"
    # History unchanged
    assert len(fake_session.history) == 10