"""Tests for lilith_cli.extra_commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import run_git_command, run_todos_command


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


@pytest.mark.asyncio
async def test_git_command_runs_git_operation_tool(tmp_path, monkeypatch):
    """/git status delega en GitOperationTool y muestra stdout."""
    monkeypatch.chdir(tmp_path)
    # git_operation ancla al _SESSION_ROOT capturado en import (los sandboxes
    # desplazan el cwd); anclarlo al tmp_path para que el test sea hermético.
    from lilith_tools import git_tools

    monkeypatch.setattr(git_tools, "_SESSION_ROOT", tmp_path)
    # Inicializar repo para que git status tenga éxito.
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    session = DummySession()
    console_prints = []

    def capture(text: str):
        console_prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_git_command(session, "status")

    assert any("nothing to commit" in str(p).lower() or "nada para confirmar" in str(p).lower() for p in console_prints)


@pytest.mark.asyncio
async def test_todos_command_adds_and_lists(tmp_path, monkeypatch):
    """/todos add foo crea una tarea y /todos list la muestra."""
    todo_file = tmp_path / "todos.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    # Monkeypatchar la ruta por defecto en TodoManager para evitar ~ real.
    from lilith_tools import todos as todos_mod

    original_path = todos_mod._TODO_PATH
    todos_mod._TODO_PATH = todo_file
    try:
        session = DummySession()
        prints = []

        def capture(text: str = ""):
            prints.append(text)

        with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
            await run_todos_command(session, "add comprar leche")
            await run_todos_command(session, "")

        assert any("comprar leche" in str(p) for p in prints)
    finally:
        todos_mod._TODO_PATH = original_path
