"""Tests for the /fork slash command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import (
    _deserialize_session,
    _fork_path,
    _list_forks,
    _serialize_session,
    run_fork_command,
)


class DummyConfig:
    def __init__(self):
        self.provider = "local"
        self.model = "local-model"
        self.api_key = None
        self.base_url = None
        self.system_prompt = "prompt"
        self.temperature = 0.7
        self.max_tokens = 4096
        self.tools = MagicMock()
        self.memory = MagicMock(enabled=False, db_path="")
        self.history = MagicMock(max_turns=50, save=True)
        self.providers = {}
        self.confirm_write = True
        self.agent_mode = "default"

    def model_dump(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "providers": self.providers,
            "confirm_write": self.confirm_write,
            "agent_mode": self.agent_mode,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.history = [{"role": "user", "content": "hola"}]
        self.system_prompt = "prompt"
        self._total_usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        self._per_model_usage = {}
        self._last_user_message = "hola"
        self.agent_mode = "default"
        self._agent_allow_writes = True
        self._agent_plan_first = False
        self._auto_execute = False
        self._auto_approved_patterns: list[str] = []
        self._stream_enabled = True
        self._disabled_tools: set[str] = set()
        self._pinned_messages: list[dict] = []
        self._tool_call_history: list[dict] = []
        self._command_history: list[dict] = []
        self._file_edit_history: list[dict] = []


@pytest.mark.asyncio
async def test_fork_save_and_list(tmp_path, monkeypatch):
    """Guardar una sesión bifurcada y listarla."""
    monkeypatch.setenv("HOME", str(tmp_path))
    session = DummySession()
    prints = []

    def capture(*args, **kwargs):
        prints.append(args[0] if args else "")

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_fork_command(session, "alternativa")
        await run_fork_command(session, "list")

    assert _list_forks() == ["alternativa"]
    fork_file = _fork_path("alternativa")
    assert fork_file.exists()
    data = json.loads(fork_file.read_text(encoding="utf-8"))
    assert data["history"] == session.history
    assert any("alternativa" in str(p) for p in prints)
    assert any("Sesiones bifurcadas" in str(p) for p in prints)


@pytest.mark.asyncio
async def test_fork_switch_and_delete(tmp_path, monkeypatch):
    """Cambiar a una sesión bifurcada y eliminarla."""
    monkeypatch.setenv("HOME", str(tmp_path))
    session = DummySession()
    session.history.append({"role": "assistant", "content": "respuesta original"})

    await run_fork_command(session, "prueba")

    # Modificar la sesión actual
    session.history = [{"role": "user", "content": "nuevo"}]

    def capture(*args, **kwargs):
        prints.append(args[0] if args else "")

    prints = []
    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_fork_command(session, "switch prueba")

    assert len(session.history) == 2
    assert session.history[1]["content"] == "respuesta original"

    await run_fork_command(session, "delete prueba")
    assert not _fork_path("prueba").exists()
    assert _list_forks() == ["alternativa"]
