"""Tests for /doctor slash command."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_doctor_runs_diagnostics_and_prints_results(fake_session, capsys):
    """/doctor runs all diagnostics and prints each check with status marker."""
    from lilith_cli.extra_commands import run_doctor_command

    _run(run_doctor_command(fake_session, ""))

    out = capsys.readouterr().out
    # Header
    assert "Doctor" in out
    assert "diagnosticando" in out.lower() or "diagnostic" in out.lower()
    # Summary
    assert "Resumen:" in out
    assert "OK" in out
    # At least one check ran (Python, API key, etc.)
    assert "Python" in out or "API key" in out


def test_doctor_suggests_fix_when_issues_found(fake_session, capsys):
    """/doctor without --fix suggests using --fix when issues are detected."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [
        {"check": "Test", "status": "warn", "message": "warning here"},
        {"check": "Test2", "status": "ok", "message": "fine"},
    ]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results):
        _run(run_doctor_command(fake_session, ""))

    out = capsys.readouterr().out
    # Summary shows 1 OK, 1 warning
    assert "1 OK" in out
    assert "1 warnings" in out
    # Suggestion to use --fix
    assert "--fix" in out


def test_doctor_with_fix_applies_fixes(fake_session, capsys):
    """/doctor --fix calls apply_fixes and prints the fix messages."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [
        {"check": "Test", "status": "warn", "message": "warning here"},
        {"check": "Test2", "status": "ok", "message": "fine"},
    ]

    fake_fixes = ["Created directory: /tmp/foo", "Wrote default config"]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results), \
         patch("lilith_cli.extra_commands.apply_fixes", return_value=fake_fixes):
        _run(run_doctor_command(fake_session, "--fix"))

    out = capsys.readouterr().out
    assert "Aplicando fixes" in out
    assert "Created directory" in out
    assert "Wrote default config" in out


def test_doctor_all_ok_no_fix_needed(fake_session, capsys):
    """/doctor with all OK results doesn't suggest --fix or apply anything."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [
        {"check": "A", "status": "ok", "message": "fine"},
        {"check": "B", "status": "ok", "message": "fine"},
    ]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results):
        _run(run_doctor_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "2 OK" in out
    # No --fix suggestion when nothing wrong
    assert "Pas\u00e1 --fix" not in out
    # No "0 warnings, 0 errors" — actually 0 0 is fine to show
    assert "warnings" in out
    assert "errors" in out