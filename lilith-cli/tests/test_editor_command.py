"""Tests for the /editor slash command."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli import extra_commands
from lilith_cli.extra_commands import run_editor_command


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.fixture
async def reset_editor(monkeypatch):
    """Reset the in-memory and persisted editor state for isolated tests."""
    monkeypatch.setattr(extra_commands, "_FROZEN_EDITOR", None)
    monkeypatch.setattr(
        extra_commands, "EDITOR_CONFIG_FILE", extra_commands.CONFIG_DIR / "editor_test.json"
    )
    if extra_commands.EDITOR_CONFIG_FILE.exists():
        extra_commands.EDITOR_CONFIG_FILE.unlink()
    yield
    extra_commands._FROZEN_EDITOR = None


@pytest.mark.asyncio
async def test_editor_set_and_current(reset_editor):
    """/editor set guarda el editor y /editor current lo muestra."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_editor_command(session, "set nano")
        await run_editor_command(session, "current")

    assert any("configurado" in p and "nano" in p for p in prints)
    assert any("nano" in p and "Editor actual" in p for p in prints)
    assert extra_commands._FROZEN_EDITOR == "nano"


@pytest.mark.asyncio
async def test_editor_opens_file_at_line(tmp_path, monkeypatch, reset_editor):
    """/editor file:line abre el archivo con la línea correcta."""
    target = tmp_path / "file.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    monkeypatch.setenv("EDITOR", "vim")

    popen_calls = []

    def fake_popen(cmd, **_kwargs):
        popen_calls.append(cmd)
        return MagicMock()

    session = DummySession()
    with patch("lilith_cli.extra_commands.subprocess.Popen", side_effect=fake_popen):
        await run_editor_command(session, f"{target}:2")

    assert len(popen_calls) == 1
    cmd = popen_calls[0]
    assert "vim" in cmd[0]
    assert f"+2" in cmd
    assert str(target) in cmd
