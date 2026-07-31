"""Tests for ``run_feedback_command`` — the ``/feedback`` slash dispatch.

The dedicated ``test_feedback_command.py`` covers the
:class:`FeedbackCommand` BaseCommand registration. This file covers the
slash-cmd branch (``cmd_name == "feedback"`` in repl.py), which goes
through ``extra_commands.run_feedback_command`` and parses a different
subcommand surface (``add``/``clear``/``help``).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import lilith_cli.extra_commands as extra_commands
from lilith_cli.extra_commands import run_feedback_command


class DummyConfig:
    def __init__(self) -> None:
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


class _Session:
    def __init__(self) -> None:
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


def _patch_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Redirect CONFIG_DIR lookups to a temporary directory."""
    monkeypatch.setattr(extra_commands, "CONFIG_DIR", tmp_path)


def _feedback_file(tmp_path) -> str:
    return str(tmp_path / "feedback.json")


@pytest.mark.asyncio
async def test_help_subcommand_prints_usage(monkeypatch, tmp_path, capsys):
    """/feedback help prints the usage hint, never touches the JSON file."""
    _patch_config_dir(monkeypatch, tmp_path)
    session = _Session()

    await run_feedback_command(session, "help")

    captured = capsys.readouterr()
    assert "Uso de /feedback" in captured.out
    assert not (tmp_path / "feedback.json").exists()


@pytest.mark.asyncio
async def test_no_args_with_empty_store_prints_idle(monkeypatch, tmp_path, capsys):
    """/feedback on an empty store prints the 'no hay feedback' idle hint."""
    _patch_config_dir(monkeypatch, tmp_path)
    session = _Session()

    await run_feedback_command(session, "")

    captured = capsys.readouterr()
    assert "No hay feedback" in captured.out
    assert not (tmp_path / "feedback.json").exists()


@pytest.mark.asyncio
async def test_add_appends_entry_with_iso_timestamp(monkeypatch, tmp_path):
    """/feedback add <msg> persists the message with a UTC ISO-8601 timestamp."""
    _patch_config_dir(monkeypatch, tmp_path)
    session = _Session()

    with patch("lilith_cli.extra_commands.console.print") as _print:
        await run_feedback_command(session, "add el agente clavó el bug")

    path = tmp_path / "feedback.json"
    assert path.exists(), "expected feedback.json to be created on add"
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    assert len(entries) == 1
    entry = entries[0]
    assert "ts" in entry and entry["ts"].endswith("+00:00")
    assert entry["message"] == "el agente clavó el bug"
    _print.assert_called_once()
    assert "Feedback guardado" in str(_print.call_args)


@pytest.mark.asyncio
async def test_add_without_message_renders_error(monkeypatch, tmp_path, capsys):
    """/feedback add (sin mensaje) reporta el uso esperado y NO escribe nada."""
    _patch_config_dir(monkeypatch, tmp_path)
    session = _Session()

    await run_feedback_command(session, "add")

    out = capsys.readouterr().out
    assert "Uso: /feedback add" in out
    assert not (tmp_path / "feedback.json").exists()


@pytest.mark.asyncio
async def test_clear_confirmed_wipes_store(monkeypatch, tmp_path):
    """/feedback clear con confirmación borra el store y muestra el conteo."""
    _patch_config_dir(monkeypatch, tmp_path)
    seed = [
        {"ts": "2026-01-01T00:00:00+00:00", "message": "uno"},
        {"ts": "2026-01-02T00:00:00+00:00", "message": "dos"},
    ]
    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text(json.dumps(seed), encoding="utf-8")
    session = _Session()

    with patch("rich.prompt.Confirm.ask", return_value=True), \
         patch("lilith_cli.extra_commands.console.print") as _print:
        await run_feedback_command(session, "clear")

    assert feedback_file.read_text(encoding="utf-8").strip() == "[]"
    joined = " ".join(str(c.args[0]) for c in _print.call_args_list)
    assert "2 entradas" in joined


@pytest.mark.asyncio
async def test_clear_declined_keeps_store(monkeypatch, tmp_path):
    """/feedback clear con confirmación negativa deja el store intacto."""
    _patch_config_dir(monkeypatch, tmp_path)
    seed = [{"ts": "2026-01-01T00:00:00+00:00", "message": "uno"}]
    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text(json.dumps(seed), encoding="utf-8")
    original = feedback_file.read_text(encoding="utf-8")
    session = _Session()

    with patch("rich.prompt.Confirm.ask", return_value=False), \
         patch("lilith_cli.extra_commands.console.print") as _print:
        await run_feedback_command(session, "clear")

    assert feedback_file.read_text(encoding="utf-8") == original
    joined = " ".join(str(c.args[0]) for c in _print.call_args_list)
    assert "cancelada" in joined


@pytest.mark.asyncio
async def test_unknown_subcommand_renders_error(monkeypatch, tmp_path, capsys):
    """/feedback con subcomando desconocido muestra un error de uso."""
    _patch_config_dir(monkeypatch, tmp_path)
    session = _Session()

    await run_feedback_command(session, "invent")

    out = capsys.readouterr().out
    assert "Uso: /feedback" in out
    assert not (tmp_path / "feedback.json").exists()


@pytest.mark.asyncio
async def test_malformed_store_treated_as_empty(monkeypatch, tmp_path, capsys):
    """/feedback sobre un store con JSON inválido no crashea y reporta el error."""
    _patch_config_dir(monkeypatch, tmp_path)
    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text("{esto no es json", encoding="utf-8")
    session = _Session()

    await run_feedback_command(session, "")

    out = capsys.readouterr().out
    # Either decoded as empty (idle hint) or printed a decode error;
    # either path is acceptable — neither should raise NameError/TypeError.
    assert "No hay feedback" in out or "no se pudo leer" in out.lower()
