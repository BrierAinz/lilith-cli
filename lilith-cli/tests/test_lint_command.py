"""Tests for the /lint slash command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import run_lint_command


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.mark.asyncio
async def test_lint_command_all_runs_linter(tmp_path, monkeypatch):
    """/lint ejecuta el linter en el directorio de trabajo."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "foo.py").write_text("x = 1\n", encoding="utf-8")
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_lint_command(session, "")

    # RunLinterTool siempre encuentra un comando (ruff o py_compile).
    assert any("Lint" in str(p) for p in prints)


@pytest.mark.asyncio
async def test_lint_command_staged_empty_repo(tmp_path, monkeypatch):
    """/lint staged muestra un mensaje cuando no hay archivos staged."""
    import subprocess

    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_lint_command(session, "staged")

    assert any("No hay archivos staged" in str(p) for p in prints)
