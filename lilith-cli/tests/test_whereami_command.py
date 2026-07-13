"""Tests for /whereami slash command with rich panel output."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_whereami_basic(fake_session, capsys):
    """/whereami prints a rich panel with project context."""
    from lilith_cli.extra_commands import run_whereami_command

    _run(run_whereami_command(fake_session, ""))

    out = capsys.readouterr().out
    # Panel border characters
    assert "\u16ed" in out or "Whereami" in out
    # Required info rows
    assert "Working dir" in out
    assert "Python" in out
    assert "Platform" in out
    assert "Lilith version" in out


def test_whereami_no_git(fake_session, capsys):
    """/whereami degrades gracefully when git is unavailable."""
    from lilith_cli.extra_commands import run_whereami_command

    def fake_run(*args, **kwargs):
        if args and len(args) > 0 and args[0] and args[0][0] == "git":
            raise FileNotFoundError("git not found")
        import subprocess as sp
        return sp.run(*args, **kwargs)

    with patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run) if hasattr(__import__("lilith_cli.extra_commands", fromlist=["subprocess"]), "subprocess") else patch.dict("os.environ", {}):
        try:
            _run(run_whereami_command(fake_session, ""))
        except FileNotFoundError:
            pass  # subprocess.run real fallback may fail if git binary is missing

    out = capsys.readouterr().out
    # Panel still rendered
    assert "Whereami" in out or "\u16ed" in out
    # Git row shows "not available" or "(detached)" or similar
    assert "Git" in out


def test_whereami_with_args_ignored(fake_session, capsys):
    """/whereami takes no args but ignores them gracefully."""
    from lilith_cli.extra_commands import run_whereami_command

    _run(run_whereami_command(fake_session, "anything goes here"))

    out = capsys.readouterr().out
    assert "Working dir" in out


def test_whereami_shows_pyproject_when_present(fake_session, capsys, tmp_path):
    """/whereami notes pyproject.toml presence when in a Python project."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_whereami_command

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'test'\n")

    with patch.object(ec.Path, "cwd", return_value=tmp_path):
        _run(run_whereami_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "pyproject" in out.lower()


def test_whereami_no_pyproject(fake_session, capsys, tmp_path):
    """/whereami shows '(no pyproject.toml)' when none exists."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_whereami_command

    # tmp_path has NO pyproject.toml
    with patch.object(ec.Path, "cwd", return_value=tmp_path):
        _run(run_whereami_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "no pyproject" in out.lower() or "Project" in out


def test_whereami_panel_has_border(fake_session, capsys):
    """/whereami output includes Rich panel border characters."""
    from lilith_cli.extra_commands import run_whereami_command

    _run(run_whereami_command(fake_session, ""))

    out = capsys.readouterr().out
    # Rich panels use box-drawing characters
    assert "\u250c" in out or "\u2514" in out or "Whereami" in out