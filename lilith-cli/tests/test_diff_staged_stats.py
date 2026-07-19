"""Tests for run_diff_staged_command and _render_diff_staged_stats."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import _render_diff_staged_stats, run_diff_staged_command


# ── /diff-staged stats parser ─────────────────────────────────────────


def _render(numstat: str, capsys):
    """Render the numstat output and return (rows_added, console_output)."""
    from rich.table import Table as RealTable

    captured: list = []

    def spy_constructor(*a, **kw):
        t = RealTable(*a, **kw)
        # Monkey-patch add_row on this instance to capture rows.
        original_add_row = t.add_row
        captured_rows: list = []

        def spy_add_row(*values):
            captured_rows.append(values)
            return original_add_row(*values)

        t.add_row = spy_add_row  # type: ignore[method-assign]
        captured.append((t, captured_rows))
        return t

    # Patch BOTH the public symbol and the module-level alias, since
    # _render_diff_staged_stats does `from rich.table import Table`
    # (which binds a local name) and we want the spy to take effect.
    with patch("rich.table.Table", spy_constructor):
        with patch.object(
            __import__("lilith_cli.extra_commands", fromlist=["Table"]),
            "Table",
            spy_constructor,
            create=True,
        ):
            _render_diff_staged_stats(numstat)

    rows = captured[0][1] if captured else []
    return rows, capsys.readouterr().out


def test_render_diff_staged_stats_handles_typical_output(capsys):
    """Three text files with different add/remove counts and one binary."""
    numstat = (
        "12\t3\tsrc/foo.py\n"
        "0\t5\tsrc/bar.py\n"
        "100\t100\tsrc/big.py\n"
        "-\t-\tassets/icon.png\n"
    )
    rows, out = _render(numstat, capsys)
    assert rows is not None
    assert len(rows) == 4

    # Totals line at the end.
    assert "12" in out and "3" in out
    assert "100" in out


def test_render_diff_staged_stats_handles_empty(capsys):
    rows, _ = _render("", capsys)
    # No rows added, no totals line.
    assert rows == []


def test_render_diff_staged_stats_handles_malformed_lines(capsys):
    """Lines with fewer than 3 tab-separated fields are skipped silently."""
    numstat = (
        "12\t3\tsrc/good.py\n"
        "garbage_line_no_tabs\n"
        "5\t2\tsrc/also_good.py\n"
    )
    rows, _ = _render(numstat, capsys)
    assert len(rows) == 2  # only the well-formed rows


def test_render_diff_staged_stats_handles_binary_marker():
    """Binary files report '-' for both counts — must not crash int()."""
    from unittest.mock import patch
    numstat = "-\t-\timg/banner.png\n"
    with patch("lilith_cli.extra_commands.console"):
        # Should not raise.
        _render_diff_staged_stats(numstat)


# ── run_diff_staged_command end-to-end (subprocess mocked) ────────────


class _Result:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.mark.asyncio
async def test_diff_staged_empty_repo(capsys, tmp_path, monkeypatch):
    """/diff-staged when nothing is staged prints the dim 'no changes' line."""
    monkeypatch.chdir(tmp_path)
    # init repo so git diff doesn't crash
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_path, check=True, capture_output=True)

    await run_diff_staged_command(None, "")
    out = capsys.readouterr().out
    assert "No hay cambios" in out


@pytest.mark.asyncio
async def test_diff_staged_stats_renders_table(capsys, tmp_path, monkeypatch):
    """/diff-staged stats calls git with --numstat and renders a table."""
    monkeypatch.chdir(tmp_path)
    fake_stdout = "12\t3\tsrc/foo.py\n5\t2\tsrc/bar.py\n"
    with patch(
        "lilith_cli.extra_commands.subprocess.run",
        return_value=_Result(stdout=fake_stdout, returncode=0),
    ):
        await run_diff_staged_command(None, "stats")

    out = capsys.readouterr().out
    assert "src/foo.py" in out
    assert "src/bar.py" in out
    # Totals line.
    assert "17" in out or "+17" in out


@pytest.mark.asyncio
async def test_diff_staged_full_patch_uses_no_numstat(capsys, monkeypatch):
    """/diff-staged (no args) calls git without --numstat so the full
    patch comes back; stats rendering is opt-in only."""
    captured_cmd: dict = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return _Result(stdout="diff --git a/foo\n", returncode=0)

    with patch(
        "lilith_cli.extra_commands.subprocess.run",
        side_effect=fake_run,
    ):
        await run_diff_staged_command(None, "")

    assert "--numstat" not in captured_cmd["cmd"]


@pytest.mark.asyncio
async def test_diff_staged_renders_git_error(capsys):
    """When git returns non-zero, the stderr is rendered."""
    with patch(
        "lilith_cli.extra_commands.subprocess.run",
        return_value=_Result(stderr="fatal: not a git repo", returncode=128),
    ):
        await run_diff_staged_command(None, "")

    out = capsys.readouterr().out
    assert "fatal: not a git repo" in out


@pytest.mark.asyncio
async def test_diff_staged_file_filter_passes_through(capsys, monkeypatch):
    """/diff-staged <path> appends -- <path> to git's command line."""
    captured_cmd: dict = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return _Result(stdout="diff content", returncode=0)

    with patch(
        "lilith_cli.extra_commands.subprocess.run",
        side_effect=fake_run,
    ):
        await run_diff_staged_command(None, "src/foo.py")

    assert "--" in captured_cmd["cmd"]
    assert "src/foo.py" in captured_cmd["cmd"]
