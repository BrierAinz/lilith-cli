"""Tests for /compare recent <mode> and the _compare_recent_paths helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import (
    _compare_recent_paths,
    _compare_text_stats,
    run_compare_command,
)


def _entry(path: str, ts: str = "2026-07-18T10:00:00+00:00") -> dict:
    return {"path": path, "tool": "file_write", "timestamp": ts}


class _Session:
    def __init__(self, history):
        self._file_edit_history = history


# ── _compare_recent_paths ────────────────────────────────────────────


def test_recent_paths_empty_when_no_telemetry():
    """When _file_edit_history attr is missing, return []."""
    class _Bare:
        pass
    assert _compare_recent_paths(_Bare()) == []


def test_recent_paths_empty_when_history_is_empty():
    sess = _Session([])
    assert _compare_recent_paths(sess) == []


def test_recent_paths_dedupes_by_path():
    """Multiple edits to the same path contribute only one entry."""
    sess = _Session([
        _entry("first.py", "2026-07-18T08:00:00+00:00"),
        _entry("first.py", "2026-07-18T09:00:00+00:00"),
        _entry("second.py", "2026-07-18T10:00:00+00:00"),
    ])
    paths = _compare_recent_paths(sess, count=2)
    # Most recent first; 'second.py' before 'first.py' (which kept its
    # newest occurrence from 09:00).
    assert paths == ["second.py", "first.py"]


def test_recent_paths_count_limit():
    """The helper respects the count limit."""
    sess = _Session([
        _entry(f"file_{i}.py") for i in range(10)
    ])
    paths = _compare_recent_paths(sess, count=3)
    assert len(paths) == 3


# ── /compare recent end-to-end ──────────────────────────────────────


@pytest.mark.asyncio
async def test_compare_recent_insufficient_history_errors(capsys):
    """/compare recent errors when fewer than 2 files have been edited."""
    sess = _Session([_entry("solo.py")])
    await run_compare_command(sess, "recent text")
    out = capsys.readouterr().out
    assert "al menos 2" in out
    assert "1" in out  # mentions the count


@pytest.mark.asyncio
async def test_compare_revent_default_mode_is_text(tmp_path, monkeypatch):
    """/compare recent (no mode) defaults to text stats.

    The first path passed to the comparator is the most recent edit
    (because /recent sorts most-recent-first), and the second is the
    one before it. Test verifies the dispatch hits _compare_text_stats
    with two paths; the actual ordering is intentional.
    """
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("hello\n" * 5, encoding="utf-8")
    b.write_text("hello\n" * 3, encoding="utf-8")

    sess = _Session([_entry(str(a)), _entry(str(b))])
    # Capture what _compare_text_stats prints.
    captured = []
    with patch(
        "lilith_cli.extra_commands._compare_text_stats",
        side_effect=lambda x, y: captured.append((str(x), str(y))),
    ):
        await run_compare_command(sess, "recent")
    assert len(captured) == 1
    assert str(captured[0][0]).endswith("a.py") or str(captured[0][1]).endswith("a.py")
    assert str(captured[0][0]).endswith("b.py") or str(captured[0][1]).endswith("b.py")


@pytest.mark.asyncio
async def test_compare_revent_files_mode_uses_diff(tmp_path):
    """/compare recent files dispatches to _compare_diff_files."""
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    sess = _Session([_entry(str(a)), _entry(str(b))])
    captured = []
    with patch(
        "lilith_cli.extra_commands._compare_diff_files",
        side_effect=lambda x, y: captured.append((str(x), str(y))),
    ):
        await run_compare_command(sess, "recent files")
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_compare_revent_json_mode_uses_json_diff(tmp_path):
    """/compare recent json dispatches to _compare_json_files."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text("{}", encoding="utf-8")
    b.write_text("{}", encoding="utf-8")

    sess = _Session([_entry(str(a)), _entry(str(b))])
    captured = []
    with patch(
        "lilith_cli.extra_commands._compare_json_files",
        side_effect=lambda x, y: captured.append((str(x), str(y))),
    ):
        await run_compare_command(sess, "recent json")
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_compare_unknown_subcommand_errors(capsys):
    """Unknown subcommand (other than recent) errors with the help pointer."""
    await run_compare_command(None, "frobnicate a b")
    out = capsys.readouterr().out
    assert "Subcomando desconocido" in out
    assert "recent" in out  # help mentions recent now
