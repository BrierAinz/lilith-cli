"""Tests for the /export slash command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilith_cli.commands import ExportCommand
from lilith_cli.plan import AgentPlan, PlanStep, plan_to_dict


class _DummyConfig:
    """Minimal config stand-in for ExportCommand tests."""

    def __init__(self) -> None:
        self.model = "test-model"
        self.provider = "test-provider"
        self.providers = {}
        self.api_key = ""

    def model_dump(self, **kwargs):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class _DummySession:
    """Minimal session stand-in for ExportCommand tests."""

    def __init__(self, history: list | None = None) -> None:
        self.config = _DummyConfig()
        self.history = list(history) if history is not None else []
        self.current_plan: AgentPlan | None = None
        self._session_id = "test-session-123"
        self._total_usage = {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }
        self._per_model_usage = {
            "test-model": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "cost": 0.0001,
            }
        }

    @property
    def total_usage(self):
        return dict(self._total_usage)

    @property
    def per_model_usage(self):
        return {model: dict(stats) for model, stats in self._per_model_usage.items()}


@pytest.mark.asyncio
async def test_export_command_json_creates_file(tmp_path: Path) -> None:
    """/export json should create a JSON file with session metadata."""
    out_path = tmp_path / "session.json"
    session = _DummySession(
        history=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Hola!"},
        ]
    )
    cmd = ExportCommand(session)

    await cmd.execute(f"json {out_path}")

    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["session_id"] == "test-session-123"
    assert data["model"] == "test-model"
    assert data["provider"] == "test-provider"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["usage"] == session.total_usage
    assert "per_model_usage" in data


@pytest.mark.asyncio
async def test_export_command_markdown_includes_tool_calls_and_plan(
    tmp_path: Path,
) -> None:
    """/export markdown should render tool calls and plan state."""
    out_path = tmp_path / "session.md"
    session = _DummySession(
        history=[
            {"role": "user", "content": "Leé el archivo"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": '{"path": "README.md"}',
                        },
                    }
                ],
            },
            {"role": "tool", "content": "contenido del archivo"},
            {"role": "assistant", "content": "Listo."},
        ]
    )
    session.current_plan = AgentPlan(
        goal="Documentar",
        steps=[
            PlanStep(number=1, description="Leer", done=True),
            PlanStep(number=2, description="Escribir", done=False),
        ],
    )
    cmd = ExportCommand(session)

    await cmd.execute(f"markdown {out_path}")

    content = out_path.read_text(encoding="utf-8")
    assert "# Sesión Yggdrasil" in content
    assert "ID de sesión" in content
    assert "test-session-123" in content
    assert "Leé el archivo" in content
    assert "Tool call" in content
    assert "file_read" in content
    assert "README.md" in content
    assert "contenido del archivo" in content
    assert "Documentar" in content
    assert "Plan" in content


@pytest.mark.asyncio
async def test_export_command_empty_history_errors() -> None:
    """/export with an empty history should report an error."""
    from unittest.mock import patch

    session = _DummySession(history=[])
    cmd = ExportCommand(session)

    with patch("lilith_cli.commands.render_error") as mock_error:
        await cmd.execute("json")
        mock_error.assert_called_once()
        assert "No hay mensajes para exportar" in mock_error.call_args[0][0]


@pytest.mark.asyncio
async def test_export_command_metadata_includes_plan_state() -> None:
    """ExportCommand._build_metadata should serialize the active plan."""
    session = _DummySession(history=[{"role": "user", "content": "ok"}])
    session.current_plan = AgentPlan(
        goal="G",
        steps=[PlanStep(number=1, description="A", done=True)],
    )
    cmd = ExportCommand(session)

    metadata = cmd._build_metadata()
    assert metadata["session_id"] == "test-session-123"
    assert metadata["plan"] == plan_to_dict(session.current_plan)
    assert metadata["usage"] == session.total_usage
    assert metadata["per_model_usage"] == session.per_model_usage
    assert metadata["config"]["model"] == "test-model"


@pytest.mark.asyncio
async def test_export_command_unknown_format_errors() -> None:
    """/export with an unknown format should report an error."""
    from unittest.mock import patch

    session = _DummySession(history=[{"role": "user", "content": "ok"}])
    cmd = ExportCommand(session)

    with patch("lilith_cli.commands.render_error") as mock_error:
        await cmd.execute("xml")
        mock_error.assert_called_once()
        assert "Formato desconocido" in mock_error.call_args[0][0]
