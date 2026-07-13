"""Tests for the /pipeline slash command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilith_cli.pipeline_command import (
    _DEFAULT_PIPELINES,
    _PIPELINE_STORE,
    _PipelineStore,
    _parse_steps,
    run_pipeline_command,
)


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
        self._tool_registry = None

    def _all_tool_names(self) -> set[str]:
        return {"tool_a", "tool_b", "tool_c", "format_file", "run_linter", "run_test", "git_operation", "package_guard"}

    async def execute_tool(self, tool_call) -> object:
        return SimpleNamespace(
            name=tool_call.name,
            content=f"ok: {tool_call.name}",
            tool_call_id=tool_call.id,
        )


@pytest.fixture
def isolated_pipeline_store(request, monkeypatch, tmp_path: Path) -> Path:
    """Override the pipeline storage path to a temporary directory."""
    store = _PipelineStore()
    store.pipeline_dir = tmp_path / "pipelines"
    store.pipeline_file = store.pipeline_dir / "pipelines.json"
    monkeypatch.setattr("lilith_cli.pipeline_command._PIPELINE_STORE", store)
    # Ensure defaults are written so the store exists and is isolated.
    store.ensure()
    request.node.store = store  # type: ignore[attr-defined]
    return store.pipeline_file


@pytest.mark.asyncio
async def test_pipeline_list_shows_defaults(isolated_pipeline_store: Path) -> None:
    """Pipeline list should display the default pipelines."""
    session = _DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(text)

    with patch("lilith_cli.pipeline_command.console.print", side_effect=capture):
        await run_pipeline_command(session, "list")

    output = "\n".join(str(p) for p in prints)
    for name in _DEFAULT_PIPELINES:
        assert name in output


@pytest.mark.asyncio
async def test_pipeline_show_and_save_cycle(request, isolated_pipeline_store: Path) -> None:
    """Show a default pipeline and save a custom one."""
    session = _DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(text)

    with patch("lilith_cli.pipeline_command.console.print", side_effect=capture):
        await run_pipeline_command(session, "show check-code")

    output = "\n".join(str(p) for p in prints)
    assert "check-code" in output
    assert "format_file" in output
    assert "run_linter" in output
    assert "run_test" in output

    with patch("lilith_cli.pipeline_command.console.print"):
        await run_pipeline_command(
            session, 'save mi-pipeline [{"name": "tool_a", "args": {"x": 1}}, {"name": "tool_b"}]'
        )

    assert isolated_pipeline_store.exists()
    data = request.node.store.load()  # type: ignore[attr-defined]
    assert "tool_a" in [s["name"] for s in data["mi-pipeline"]]
    assert "tool_b" in [s["name"] for s in data["mi-pipeline"]]


@pytest.mark.asyncio
async def test_pipeline_run_executes_steps(isolated_pipeline_store: Path) -> None:
    """Running a pipeline should execute each tool in order."""
    session = _DummySession()
    session.execute_tool = AsyncMock(side_effect=session.execute_tool)

    with patch("lilith_cli.pipeline_command.console.print"):
        await run_pipeline_command(session, "run check-code")

    assert session.execute_tool.call_count == len(_DEFAULT_PIPELINES["check-code"])
    names = [call.args[0].name for call in session.execute_tool.call_args_list]
    assert names == ["format_file", "run_linter", "run_test"]


@pytest.mark.asyncio
async def test_pipeline_delete(request, isolated_pipeline_store: Path) -> None:
    """Deleting a pipeline should remove it from storage."""
    session = _DummySession()

    with patch("lilith_cli.pipeline_command.console.print"):
        await run_pipeline_command(session, "delete review-changes")

    data = request.node.store.load()  # type: ignore[attr-defined]
    assert "review-changes" not in data


@pytest.mark.asyncio
async def test_pipeline_run_unknown(isolated_pipeline_store: Path) -> None:
    """Running an unknown pipeline should render an error."""
    session = _DummySession()
    errors = []

    def capture_error(text: str = "") -> None:
        errors.append(text)

    with patch("lilith_cli.pipeline_command.render_error", side_effect=capture_error):
        await run_pipeline_command(session, "run no-existe")

    assert any("no encontrado" in str(e) for e in errors)


@pytest.mark.asyncio
async def test_pipeline_save_missing_steps(request, isolated_pipeline_store: Path) -> None:
    """Saving a pipeline without steps should render an error and not create a file."""
    session = _DummySession()
    errors = []

    def capture_error(text: str = "") -> None:
        errors.append(text)

    # Remove the pre-created file so we can assert it is not created by the error path.
    if isolated_pipeline_store.exists():
        isolated_pipeline_store.unlink()

    with patch("lilith_cli.pipeline_command.render_error", side_effect=capture_error):
        with patch.object(request.node.store, "ensure"):  # type: ignore[attr-defined]
            await run_pipeline_command(session, "save vacio")

    assert any("Uso" in str(e) for e in errors)
    assert not isolated_pipeline_store.exists()


def test_parse_steps_json() -> None:
    """Parsing steps should support JSON arrays."""
    steps = _parse_steps('[{"name": "tool_a", "args": {"x": 1}}]')
    assert steps == [{"name": "tool_a", "args": {"x": 1}}]


def test_parse_steps_semicolon() -> None:
    """Parsing steps should support semicolon-separated tool names."""
    steps = _parse_steps("tool_a; tool_b")
    assert steps == [{"name": "tool_a", "args": {}}, {"name": "tool_b", "args": {}}]
