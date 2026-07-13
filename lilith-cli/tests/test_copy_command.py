"""Tests for the /copy slash command."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def patched_subprocess(monkeypatch):
    """Replace subprocess.run inside extra_commands so the clipboard is never touched."""
    import lilith_cli.extra_commands as ec

    captured: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    return {"captured": captured}


@pytest.mark.asyncio
async def test_copy_with_no_history_reports_error(fake_session, patched_subprocess, capsys):
    """/copy on a session with no assistant messages must print a usage error."""
    from lilith_cli.extra_commands import run_copy_command

    fake_session.history = []

    await run_copy_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "asistente" in combined.lower() or "no hay" in combined.lower()
    # No subprocess should have been spawned.
    assert patched_subprocess["captured"] == []


@pytest.mark.asyncio
async def test_copy_invokes_subprocess_with_assistant_content(fake_session, patched_subprocess, capsys):
    """/copy must spawn subprocess.run with the last assistant message bytes."""
    from lilith_cli.extra_commands import run_copy_command

    fake_session.history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "respuesta del asistente"},
        {"role": "user", "content": "mas"},
    ]

    await run_copy_command(fake_session, "")

    assert len(patched_subprocess["captured"]) == 1
    call = patched_subprocess["captured"][0]
    payload = call["kwargs"].get("input")
    assert payload == "respuesta del asistente".encode("utf-8")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "copiad" in combined.lower() or "portapapeles" in combined.lower()


@pytest.mark.asyncio
async def test_copy_with_invalid_subcommand_reports_error(fake_session, patched_subprocess, capsys):
    """/copy with an unknown subcommand must print a usage error and not spawn anything."""
    from lilith_cli.extra_commands import run_copy_command

    fake_session.history = [{"role": "assistant", "content": "x"}]

    await run_copy_command(fake_session, "bogus")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "uso" in combined.lower()
    assert patched_subprocess["captured"] == []


@pytest.mark.asyncio
async def test_copy_last_alias_works(fake_session, patched_subprocess, capsys):
    """/copy last must behave like /copy and copy the last assistant message."""
    from lilith_cli.extra_commands import run_copy_command

    fake_session.history = [
        {"role": "assistant", "content": "primera"},
        {"role": "assistant", "content": "ultima"},
    ]

    await run_copy_command(fake_session, "last")

    assert len(patched_subprocess["captured"]) == 1
    payload = patched_subprocess["captured"][0]["kwargs"].get("input")
    assert payload == "ultima".encode("utf-8")


@pytest.mark.asyncio
async def test_copy_falls_back_to_console_on_subprocess_failure(fake_session, monkeypatch, capsys):
    """If the clipboard copy fails the command must still print the text to stdout."""
    import lilith_cli.extra_commands as ec

    def failing_run(*args, **kwargs):
        raise OSError("clipboard unavailable")

    monkeypatch.setattr(ec.subprocess, "run", failing_run)

    from lilith_cli.extra_commands import run_copy_command

    fake_session.history = [{"role": "assistant", "content": "fallback test"}]

    await run_copy_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "fallback test" in combined
    # Failure path uses a 'warning' or fallback message.
    assert "portapapeles" in combined.lower() or "no se pudo" in combined.lower()