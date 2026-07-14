"""Tests for the local /feedback command."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from rich.table import Table

from lilith_cli import extra_commands
from lilith_cli.extra_commands import run_feedback_command


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
async def test_feedback_add_persists_entry(tmp_path, monkeypatch):
    """/feedback add guarda una entrada local con timestamp y mensaje."""
    monkeypatch.setattr(extra_commands, "CONFIG_DIR", tmp_path)
    prints = []

    with patch("lilith_cli.extra_commands.console.print", side_effect=prints.append):
        await run_feedback_command(DummySession(), "add La respuesta fue útil")

    entries = json.loads((tmp_path / "feedback.json").read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert set(entries[0]) == {"ts", "message"}
    assert entries[0]["message"] == "La respuesta fue útil"
    assert entries[0]["ts"]
    assert any("Feedback guardado" in str(item) for item in prints)


@pytest.mark.asyncio
async def test_feedback_lists_only_five_most_recent(tmp_path, monkeypatch):
    """/feedback muestra las cinco entradas más recientes en una tabla Rich."""
    entries = [
        {"ts": f"2026-07-{day:02d}T12:00:00+00:00", "message": f"mensaje {day}"}
        for day in range(1, 7)
    ]
    (tmp_path / "feedback.json").write_text(
        json.dumps(entries, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(extra_commands, "CONFIG_DIR", tmp_path)
    prints = []

    with patch("lilith_cli.extra_commands.console.print", side_effect=prints.append):
        await run_feedback_command(DummySession(), "")

    table = next(item for item in prints if isinstance(item, Table))
    assert table.row_count == 5
    assert table.columns[1]._cells == [f"mensaje {day}" for day in range(2, 7)]