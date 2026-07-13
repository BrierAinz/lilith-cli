"""Tests for /doctor --deep spinner behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_doctor_deep_uses_status_context(fake_session, capsys):
    """/doctor --deep wraps the call in console.status for spinner."""
    from lilith_cli.extra_commands import run_doctor_command

    base_results = [{"check": "A", "status": "ok", "message": "a"}]
    deep_results = [{"check": "B", "status": "ok", "message": "b"}]

    # Track whether console.status was used as a context manager
    status_used = []

    class MockStatus:
        def __enter__(self):
            status_used.append("enter")
            return self

        def __exit__(self, *args):
            status_used.append("exit")
            return False

        def update(self, *args, **kwargs):
            pass

    def mock_status(*args, **kwargs):
        return MockStatus()

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=base_results), \
         patch("lilith_cli.extra_commands._run_deep_checks", return_value=deep_results), \
         patch("lilith_cli.extra_commands.console.status", side_effect=mock_status):
        _run(run_doctor_command(fake_session, "--deep"))

    # Spinner should have been entered and exited
    assert "enter" in status_used
    assert "exit" in status_used


def test_doctor_deep_skips_spinner_in_quiet_mode(fake_session, capsys):
    """/doctor --deep --quiet does NOT show the spinner."""
    from lilith_cli.extra_commands import run_doctor_command

    base_results = [{"check": "A", "status": "ok", "message": "a"}]
    deep_results = [{"check": "B", "status": "ok", "message": "b"}]

    status_used = []

    class MockStatus:
        def __enter__(self):
            status_used.append("enter")
            return self

        def __exit__(self, *args):
            status_used.append("exit")
            return False

    def mock_status(*args, **kwargs):
        return MockStatus()

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=base_results), \
         patch("lilith_cli.extra_commands._run_deep_checks", return_value=deep_results), \
         patch("lilith_cli.extra_commands.console.status", side_effect=mock_status):
        _run(run_doctor_command(fake_session, "--deep --quiet"))

    # Spinner should NOT have been entered in quiet mode
    assert "enter" not in status_used


def test_doctor_default_no_spinner(fake_session, capsys):
    """/doctor (no --deep) does NOT use spinner."""
    from lilith_cli.extra_commands import run_doctor_command

    fake_results = [{"check": "A", "status": "ok", "message": "a"}]

    status_used = []

    class MockStatus:
        def __enter__(self):
            status_used.append("enter")
            return self

        def __exit__(self, *args):
            return False

    def mock_status(*args, **kwargs):
        return MockStatus()

    with patch("lilith_cli.extra_commands.run_diagnostics", return_value=fake_results), \
         patch("lilith_cli.extra_commands.console.status", side_effect=mock_status):
        _run(run_doctor_command(fake_session, ""))

    assert "enter" not in status_used