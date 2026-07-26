"""Tests for saved-session lifecycle operations exposed by /history."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from lilith_cli.extra_commands import run_history_command


def _write_conversation(directory: Path, name: str, preview: str) -> Path:
    path = directory / f"conv_{name}.json"
    path.write_text(
        json.dumps(
            {
                "timestamp": "20260726_120000",
                "model": "test-model",
                "provider": "test-provider",
                "messages": [{"role": "user", "content": preview}],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_history_sessions_json_lists_saved_conversations(tmp_path, capsys):
    _write_conversation(tmp_path, "20260726_120000", "revisar parser")

    with patch("lilith_cli.repl._CONVERSATIONS_DIR", tmp_path):
        asyncio.run(run_history_command(object(), "sessions --json"))

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "saved_sessions"
    assert payload["count"] == 1
    assert payload["sessions"][0]["preview"] == "revisar parser"
    assert "file" not in payload["sessions"][0]


def test_history_rename_then_delete_requires_confirmation(tmp_path, capsys):
    original = _write_conversation(tmp_path, "20260726_120000", "migrar api")

    with patch("lilith_cli.repl._CONVERSATIONS_DIR", tmp_path):
        asyncio.run(run_history_command(object(), 'rename 1 "migración api"'))
        renamed = tmp_path / "conv_20260726_120000__migración-api.json"
        assert renamed.exists()
        assert not original.exists()

        asyncio.run(run_history_command(object(), "delete 1"))
        assert renamed.exists()
        asyncio.run(run_history_command(object(), "delete 1 --yes"))

    assert not renamed.exists()
    output = capsys.readouterr().out
    assert "Confirmá" in output
    assert "Sesión eliminada" in output