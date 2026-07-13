"""Tests for the /redact slash command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_redact_command, _redact_text


class DummySession:
    def __init__(self):
        self.config = SimpleNamespace(
            model="test",
            provider="test",
            providers={},
            api_key="",
            system_prompt="",
        )
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.mark.asyncio
async def test_redact_command_prints_to_stdout(tmp_path, monkeypatch):
    """/redact <file> imprime la versión redactada por stdout."""
    source = tmp_path / "secrets.env"
    source.write_text(
        "API_KEY=sk-123...cdef\nEMAIL=admin@example.com\nSSN=123-45-6789\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_redact_command(session, "secrets.env")

    output = "\n".join(str(p) for p in prints)
    assert "[REDACTED]" in output
    assert "admin@example.com" not in output
    assert "123-45-6789" not in output


@pytest.mark.asyncio
async def test_redact_command_writes_to_output_file(tmp_path, monkeypatch):
    """/redact <file> --out <out> escribe la versión redactada a disco."""
    source = tmp_path / "config.env"
    output = tmp_path / "config.env.redacted"
    source.write_text(
        "PASSWORD=supersecret\nCREDIT_CARD=1234 5678 9012 3456\nTOKEN=abc123xyz\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_redact_command(session, "config.env --out config.env.redacted")

    assert output.exists()
    redacted = output.read_text(encoding="utf-8")
    assert "[REDACTED]" in redacted
    assert "supersecret" not in redacted
    assert "1234 5678 9012 3456" not in redacted
    assert "abc123xyz" not in redacted


def test_redact_text_handles_all_builtin_patterns():
    """_redact_text reemplaza todos los patrones sensibles por [REDACTED]."""
    text = (
        "api_key=sk-1234567890abcdef\n"
        "password=supersecret\n"
        "secret=abc123xyz\n"
        "contact: admin@example.com\n"
        "ssn: 123-45-6789\n"
        "cc: 1234 5678 9012 3456\n"
    )
    redacted = _redact_text(text)
    assert redacted.count("[REDACTED]") == 6
    assert "sk-1234567890abcdef" not in redacted
    assert "supersecret" not in redacted
    assert "abc123xyz" not in redacted
    assert "admin@example.com" not in redacted
    assert "123-45-6789" not in redacted
    assert "1234 5678 9012 3456" not in redacted
