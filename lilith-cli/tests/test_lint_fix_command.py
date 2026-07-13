"""Tests for /lint-fix slash command."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_lint_fix_no_linters_installed(fake_session, capsys):
    """/lint-fix renders clear error when neither ruff nor black is installed."""
    from lilith_cli.extra_commands import run_lint_fix_command

    with patch("lilith_cli.extra_commands.shutil.which", return_value=None):
        _run(run_lint_fix_command(fake_session, "."))

    out = capsys.readouterr().out
    assert "ruff" in out.lower() or "black" in out.lower()
    assert "install" in out.lower() or "pip" in out.lower()


def test_lint_fix_uses_ruff_when_available(fake_session, capsys):
    """/lint-fix prefers ruff when both are available."""
    from lilith_cli.extra_commands import run_lint_fix_command

    fake_proc = type("P", (), {
        "stdout": "fixed 3 issues",
        "stderr": "",
        "returncode": 0,
    })()

    def fake_run(*args, **kwargs):
        return fake_proc

    with patch("lilith_cli.extra_commands.shutil.which", return_value="C:/fake/ruff.exe"), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "."))

    out = capsys.readouterr().out
    assert "ruff" in out
    assert "all issues fixed" in out


def test_lint_fix_falls_back_to_black(fake_session, capsys):
    """/lint-fix falls back to black when ruff is not installed."""
    from lilith_cli.extra_commands import run_lint_fix_command

    fake_proc = type("P", (), {
        "stdout": "reformatted 2 files",
        "stderr": "",
        "returncode": 0,
    })()

    def fake_which(name):
        return "C:/fake/black.exe" if name == "black" else None

    def fake_run(*args, **kwargs):
        return fake_proc

    with patch("lilith_cli.extra_commands.shutil.which", side_effect=fake_which), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "src/"))

    out = capsys.readouterr().out
    assert "black" in out
    assert "src/" in out or "all files reformatted" in out


def test_lint_fix_handles_timeout(fake_session, capsys):
    """/lint-fix renders timeout error when subprocess exceeds 60s."""
    from lilith_cli.extra_commands import run_lint_fix_command
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "ruff", timeout=60)

    with patch("lilith_cli.extra_commands.shutil.which", return_value="C:/fake/ruff.exe"), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "."))

    out = capsys.readouterr().out
    assert "timed out" in out.lower() or "timeout" in out.lower()


def test_lint_fix_reports_unfixable_issues(fake_session, capsys):
    """/lint-fix reports ruff exit code when issues are unfixable."""
    from lilith_cli.extra_commands import run_lint_fix_command

    fake_proc = type("P", (), {
        "stdout": "E501 line too long (3 unfixable)",
        "stderr": "",
        "returncode": 1,
    })()

    def fake_run(*args, **kwargs):
        return fake_proc

    with patch("lilith_cli.extra_commands.shutil.which", return_value="C:/fake/ruff.exe"), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "."))

    out = capsys.readouterr().out
    assert "unfixable" in out or "exit 1" in out or "exit" in out.lower()