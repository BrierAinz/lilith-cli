"""Focused tests for /macro edit editor selection and command parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lilith_cli.commands import MacroCommand


class _Session:
    history: list = []


@pytest.fixture
def macros_path(tmp_path, monkeypatch):
    path = tmp_path / "macros.json"
    path.write_text(json.dumps({"deploy": ["/status"]}), encoding="utf-8")
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", path)
    return path


@pytest.mark.asyncio
async def test_macro_edit_uses_editor_command_with_arguments(
    macros_path, monkeypatch
):
    """EDITOR may contain arguments such as ``code --wait``."""
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text("/status\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.setattr("subprocess.run", fake_run)

    await MacroCommand(_Session()).execute("edit deploy")

    assert calls and calls[0][:2] == ["code", "--wait"]
    assert calls[0][-1].endswith(".lilith-macro")


@pytest.mark.asyncio
async def test_macro_edit_defaults_to_notepad_on_windows(
    macros_path, monkeypatch
):
    """Without EDITOR, Windows uses notepad as the required fallback."""
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text("/status\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("lilith_cli.commands.sys.platform", "win32")
    monkeypatch.setattr("subprocess.run", fake_run)

    await MacroCommand(_Session()).execute("edit deploy")

    assert calls and calls[0][0] == "notepad"
