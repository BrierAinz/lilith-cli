"""Tests for the /fork slash command."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_fork_edit_rewrites_copy_without_mutating_active_session(tmp_path, monkeypatch):
    """--edit abre un JSON temporal y guarda los mensajes editados solo en el fork."""
    from lilith_cli import extra_commands as ec

    monkeypatch.setattr(ec, "_FORKS_DIR", tmp_path)
    monkeypatch.setattr(ec, "_get_editor", lambda: "code")
    session = DummySession()
    original_history = list(session.history)

    def fake_run(command, **kwargs):
        assert command[-2] == "--wait"
        draft_path = Path(command[-1])
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["messages"] = [{"role": "user", "content": "prompt corregido"}]
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    await run_fork_command(session, "alternativa --edit")

    saved = json.loads((tmp_path / "alternativa.json").read_text(encoding="utf-8"))
    assert saved["history"][0]["content"] == "prompt corregido"
    assert session.history == original_history
    assert not (tmp_path / "alternativa.edit.tmp").exists()


@pytest.mark.asyncio
async def test_fork_edit_rejects_invalid_json_and_cleans_draft(tmp_path, monkeypatch, capsys):
    """Un borrador inválido no crea el fork ni deja el archivo temporal."""
    from lilith_cli import extra_commands as ec

    monkeypatch.setattr(ec, "_FORKS_DIR", tmp_path)
    monkeypatch.setattr(ec, "_get_editor", lambda: "vim")

    def fake_run(command, **kwargs):
        Path(command[-1]).write_text("JSON inválido", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    await run_fork_command(DummySession(), "fallido --edit")

    assert not (tmp_path / "fallido.json").exists()
    assert not (tmp_path / "fallido.edit.tmp").exists()
    assert "No se pudo editar la conversación" in capsys.readouterr().out
