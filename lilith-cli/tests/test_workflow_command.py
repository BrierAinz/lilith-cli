"""Tests for the /workflow slash command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.workflow_command import (
    _DEFAULT_WORKFLOWS,
    _WORKFLOW_STORE,
    run_workflow_command,
)

import json


class _DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""


class _DummySession:
    def __init__(self) -> None:
        self.config = _DummyConfig()
        self.memory = None
        self.history = []
        self.provider = MagicMock()
        self.system_prompt = ""

    async def process_message(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": f"echo: {text}"})


@pytest.fixture
def isolated_workflow_store(monkeypatch, tmp_path: Path) -> Path:
    """Override the workflow storage path to a temporary directory."""
    workflow_dir = tmp_path / "workflows"
    workflow_file = workflow_dir / "workflows.json"
    store = SimpleNamespace(
        workflow_dir=workflow_dir,
        workflow_file=workflow_file,
    )
    monkeypatch.setattr("lilith_cli.workflow_command._WORKFLOW_STORE", store)
    return workflow_file


@pytest.mark.asyncio
async def test_workflow_list_shows_defaults(isolated_workflow_store: Path) -> None:
    """Workflow list should display the default workflows."""
    session = _DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(text)

    with patch("lilith_cli.workflow_command.console.print", side_effect=capture):
        await run_workflow_command(session, "list")

    output = "\n".join(str(p) for p in prints)
    for name in _DEFAULT_WORKFLOWS:
        assert name in output


@pytest.mark.asyncio
async def test_workflow_show_and_save_cycle(isolated_workflow_store: Path) -> None:
    """Show a default workflow and save a custom one."""
    session = _DummySession()

    # Show a default workflow.
    prints = []

    def capture(text: str = "") -> None:
        prints.append(text)

    with patch("lilith_cli.workflow_command.console.print", side_effect=capture):
        await run_workflow_command(session, "show fix-tests")

    output = "\n".join(str(p) for p in prints)
    assert "fix-tests" in output
    assert any("Leer el archivo" in str(p) or "tests" in str(p) for p in prints)

    # Save a custom workflow.
    with patch("lilith_cli.workflow_command.console.print"):
        await run_workflow_command(session, "save mi-flow Paso 1; Paso 2; Paso 3")

    assert isolated_workflow_store.exists()
    data = json.loads(isolated_workflow_store.read_text(encoding="utf-8"))
    assert data["mi-flow"] == ["Paso 1", "Paso 2", "Paso 3"]


@pytest.mark.asyncio
async def test_workflow_run_executes_steps(isolated_workflow_store: Path) -> None:
    """Running a workflow should post each step as a prompt to the session."""
    session = _DummySession()

    with patch("lilith_cli.workflow_command.console.print"):
        # Patch the step runner so we don't need the full streaming REPL.
        from lilith_cli import workflow_command

        original_runner = workflow_command._run_workflow_steps

        async def _stub_run_steps(session, name, steps):
            for i, step in enumerate(steps, start=1):
                prompt = f"[Workflow '{name}' - Paso {i}/{len(steps)}] {step}"
                session.history.append({"role": "user", "content": prompt})
                await session.process_message(prompt)

        workflow_command._run_workflow_steps = _stub_run_steps
        try:
            await run_workflow_command(session, "run fix-tests")
        finally:
            workflow_command._run_workflow_steps = original_runner

    # The session's process_message was called once per step and stored assistant replies.
    assistant_replies = [m for m in session.history if m.get("role") == "assistant"]
    assert len(assistant_replies) == len(_DEFAULT_WORKFLOWS["fix-tests"])
    for i, step in enumerate(_DEFAULT_WORKFLOWS["fix-tests"], start=1):
        expected = f"[Workflow 'fix-tests' - Paso {i}/{len(_DEFAULT_WORKFLOWS['fix-tests'])}] {step}"
        assert any(expected in m.get("content", "") for m in session.history)


@pytest.mark.asyncio
async def test_workflow_run_unknown(isolated_workflow_store: Path) -> None:
    """Running an unknown workflow should render an error."""
    session = _DummySession()
    errors = []

    def capture_error(text: str = "") -> None:
        errors.append(text)

    with patch("lilith_cli.workflow_command.render_error", side_effect=capture_error):
        await run_workflow_command(session, "run no-existe")

    assert any("no encontrado" in str(e) for e in errors)


@pytest.mark.asyncio
async def test_workflow_save_missing_steps(isolated_workflow_store: Path) -> None:
    """Saving a workflow without steps should render an error and not create a file."""
    session = _DummySession()
    errors = []

    def capture_error(text: str = "") -> None:
        errors.append(text)

    with patch("lilith_cli.workflow_command.render_error", side_effect=capture_error):
        await run_workflow_command(session, "save vacio")

    assert any("Uso" in str(e) for e in errors)
    assert not isolated_workflow_store.exists()
