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
async def test_lint_command_requires_explicit_path(tmp_path, monkeypatch):
    """/lint sin destino no escanea el directorio de trabajo implícito."""
    monkeypatch.chdir(tmp_path)
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_lint_command(session, "")

    assert any("ruta-relativa" in str(p) for p in prints)


@pytest.mark.asyncio
async def test_lint_command_accepts_explicit_relative_path(tmp_path, monkeypatch):
    """/lint acepta un archivo relativo y no agrega flags de auto-fix."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "foo.py").write_text("x = 1\n", encoding="utf-8")
    session = DummySession()
    fake_result = MagicMock(success=True, data={"command": "ruff check foo.py"})

    with patch("lilith_cli.extra_commands.RunLinterTool") as tool:
        tool.return_value.execute.return_value = fake_result
        await run_lint_command(session, "foo.py --tool ruff check")

    tool.return_value.execute.assert_called_once_with(path="foo.py", linter="ruff check")
    assert "--fix" not in tool.return_value.execute.call_args.kwargs.get("linter", "")


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
