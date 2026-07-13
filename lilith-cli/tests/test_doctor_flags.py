"""Tests for /doctor --json, --quiet, --deep flags."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_doctor_json_outputs_valid_json(fake_session, capsys):
    """/doctor --json outputs valid JSON parseable by json.loads."""
    from lilith_cli.extra_commands import run_doctor_command

    _run(run_doctor_command(fake_session, "--json"))

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) > 0
    assert all("check" in item and "status" in item for item in parsed)


def test_doctor_json_status_values(fake_session, capsys):
    """/doctor --json status values are ok/warn/error."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [
        {"check": "A", "status": "ok", "message": "fine"},
        {"check": "B", "status": "warn", "message": "watch out"},
        {"check": "C", "status": "error", "message": "broken"},
    ]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results):
        _run(run_doctor_command(fake_session, "--json"))

    out = capsys.readouterr().out
    parsed = json.loads(out)
    statuses = {item["status"] for item in parsed}
    assert statuses == {"ok", "warn", "error"}


def test_doctor_quiet_suppresses_summary(fake_session, capsys):
    """/doctor --quiet does NOT print the 'Resumen:' line."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [
        {"check": "Test", "status": "ok", "message": "fine"},
    ]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results):
        _run(run_doctor_command(fake_session, "--quiet"))

    out = capsys.readouterr().out
    # Summary line suppressed
    assert "Resumen:" not in out
    # Check marks also suppressed
    assert "[OK]" not in out and "[ok]" not in out.lower()


def test_doctor_json_quiet_combined(fake_session, capsys):
    """/doctor --json --quiet outputs JSON without extra UI noise."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [{"check": "X", "status": "ok", "message": "ok"}]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results):
        _run(run_doctor_command(fake_session, "--json --quiet"))

    out = capsys.readouterr().out
    # Just the JSON, nothing else
    parsed = json.loads(out.strip())
    assert parsed[0]["check"] == "X"


def test_doctor_deep_runs_extra_checks(fake_session, capsys):
    """/doctor --deep runs additional checks (disk, DNS, git remote, session)."""
    from lilith_cli.extra_commands import run_doctor_command

    base_results = [{"check": "Python", "status": "ok", "message": "fine"}]
    deep_results = [
        {"check": "Disk space", "status": "ok", "message": "10 GB free"},
        {"check": "Network DNS", "status": "ok", "message": "resolved in 5ms"},
    ]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=base_results), \
         patch("lilith_cli.extra_commands._run_deep_checks", return_value=deep_results):
        _run(run_doctor_command(fake_session, "--deep"))

    out = capsys.readouterr().out
    assert "Disk space" in out
    assert "Network DNS" in out
    assert "Python" in out


def test_doctor_deep_with_json(fake_session, capsys):
    """/doctor --deep --json includes both base and deep checks in JSON output."""
    from lilith_cli.extra_commands import run_doctor_command

    base_results = [{"check": "A", "status": "ok", "message": "a"}]
    deep_results = [{"check": "B", "status": "warn", "message": "b"}]

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=base_results), \
         patch("lilith_cli.extra_commands._run_deep_checks", return_value=deep_results):
        _run(run_doctor_command(fake_session, "--deep --json"))

    out = capsys.readouterr().out
    parsed = json.loads(out)
    checks = {item["check"] for item in parsed}
    assert "A" in checks
    assert "B" in checks