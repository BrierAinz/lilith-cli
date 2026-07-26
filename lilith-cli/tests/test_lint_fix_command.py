"""Tests for /lint-fix slash command."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_lint_fix_no_linters_installed(fake_session, capsys, tmp_path, monkeypatch):
    """/lint-fix requires a repo-relative explicit path."""
    from lilith_cli.extra_commands import run_lint_fix_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with patch("lilith_cli.extra_commands.shutil.which", return_value=None):
        _run(run_lint_fix_command(fake_session, "module.py"))

    out = capsys.readouterr().out
    assert "ruff" in out.lower() or "black" in out.lower()
    assert "instal" in out.lower()


def test_lint_fix_rejects_implicit_or_absolute_paths(fake_session, capsys, tmp_path, monkeypatch):
    """The audit must not accept '.' or paths outside the working tree."""
    from lilith_cli.extra_commands import run_lint_fix_command

    monkeypatch.chdir(tmp_path)
    with patch("lilith_cli.extra_commands.shutil.which") as which, patch(
        "lilith_cli.extra_commands.subprocess.run"
    ) as run:
        _run(run_lint_fix_command(fake_session, "."))
        _run(run_lint_fix_command(fake_session, str(tmp_path.parent / "outside.py")))

    out = capsys.readouterr().out
    assert "explícita" in out
    which.assert_not_called()
    run.assert_not_called()


def test_lint_fix_uses_ruff_check_without_fix(fake_session, capsys, tmp_path, monkeypatch):
    """Ruff mode is report-only and never passes --fix."""
    from lilith_cli.extra_commands import run_lint_fix_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_proc = type("P", (), {"stdout": "1 issue", "stderr": "", "returncode": 1})()
    with patch("lilith_cli.extra_commands.shutil.which", return_value="ruff"), patch(
        "lilith_cli.extra_commands.subprocess.run", return_value=fake_proc
    ) as run:
        _run(run_lint_fix_command(fake_session, "module.py"))

    command = run.call_args.args[0]
    assert command == ["ruff", "check", "module.py"]
    assert "--fix" not in command
    assert "solo reporte" in capsys.readouterr().out


def test_lint_fix_uses_ruff_when_available(fake_session, capsys, tmp_path, monkeypatch):
    """/lint-fix prefers ruff in report-only mode."""
    from lilith_cli.extra_commands import run_lint_fix_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_proc = type("P", (), {
        "stdout": "3 issues found",
        "stderr": "",
        "returncode": 0,
    })()

    def fake_run(*args, **kwargs):
        return fake_proc

    with patch("lilith_cli.extra_commands.shutil.which", return_value="C:/fake/ruff.exe"), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "module.py"))

    out = capsys.readouterr().out
    assert "ruff" in out
    assert "no se modificaron" in out


def test_lint_fix_falls_back_to_black(fake_session, capsys, tmp_path, monkeypatch):
    """/lint-fix uses black --check when ruff is not installed."""
    from lilith_cli.extra_commands import run_lint_fix_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_proc = type("P", (), {
        "stdout": "would reformat module.py",
        "stderr": "",
        "returncode": 0,
    })()

    def fake_which(name):
        return "C:/fake/black.exe" if name == "black" else None

    def fake_run(*args, **kwargs):
        return fake_proc

    with patch("lilith_cli.extra_commands.shutil.which", side_effect=fake_which), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "module.py"))

    out = capsys.readouterr().out
    assert "black" in out
    assert "no se modificaron" in out


def test_lint_fix_handles_timeout(fake_session, capsys, tmp_path, monkeypatch):
    """/lint-fix renders timeout error when subprocess exceeds 60s."""
    from lilith_cli.extra_commands import run_lint_fix_command
    import subprocess

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "ruff", timeout=60)

    with patch("lilith_cli.extra_commands.shutil.which", return_value="C:/fake/ruff.exe"), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "module.py"))

    out = capsys.readouterr().out
    assert "agotó el tiempo" in out.lower() or "timeout" in out.lower()


def test_lint_fix_reports_issues_without_fixing(fake_session, capsys, tmp_path, monkeypatch):
    """/lint-fix reports ruff findings without claiming to fix them."""
    from lilith_cli.extra_commands import run_lint_fix_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_proc = type("P", (), {
        "stdout": "E501 line too long (3 issues)",
        "stderr": "",
        "returncode": 1,
    })()

    def fake_run(*args, **kwargs):
        return fake_proc

    with patch("lilith_cli.extra_commands.shutil.which", return_value="C:/fake/ruff.exe"), \
         patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run):
        _run(run_lint_fix_command(fake_session, "module.py"))

    out = capsys.readouterr().out
    assert "exit 1" in out or "exit" in out.lower()
