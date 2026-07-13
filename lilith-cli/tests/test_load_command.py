"""Tests for the /load slash command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_conversations(tmp_path: Path, monkeypatch):
    """Redirect extra_commands.CONFIG_DIR to a tmp dir so /load reads fake JSON."""
    import lilith_cli.extra_commands as ec

    fake_root = tmp_path / "lilith"
    fake_root.mkdir()
    monkeypatch.setattr(ec, "CONFIG_DIR", fake_root)
    return fake_root / "conversations"


@pytest.mark.asyncio
async def test_load_replaces_session_history(fake_session, isolated_conversations):
    """/load <name> must parse the JSON file and assign its messages to history."""
    from lilith_cli.extra_commands import run_load_command

    isolated_conversations.mkdir(parents=True, exist_ok=True)
    payload = [
        {"role": "user", "content": "primero"},
        {"role": "assistant", "content": "respuesta"},
    ]
    (isolated_conversations / "demo.json").write_text(
        json.dumps({"messages": payload}), encoding="utf-8"
    )

    await run_load_command(fake_session, "demo")

    assert fake_session.history == payload


@pytest.mark.asyncio
async def test_load_without_name_reports_error(fake_session, isolated_conversations, capsys):
    """/load with no name must print a usage error and not mutate history."""
    from lilith_cli.extra_commands import run_load_command

    fake_session.history = [{"role": "user", "content": "original"}]

    await run_load_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "uso" in combined.lower()
    assert fake_session.history == [{"role": "user", "content": "original"}]


@pytest.mark.asyncio
async def test_load_missing_file_reports_error(fake_session, isolated_conversations, capsys):
    """/load <missing> must print a not-found error and not mutate history."""
    from lilith_cli.extra_commands import run_load_command

    isolated_conversations.mkdir(parents=True, exist_ok=True)
    fake_session.history = [{"role": "user", "content": "original"}]

    await run_load_command(fake_session, "does-not-exist")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "no encontr" in combined.lower() or "no existe" in combined.lower() or "no encontrada" in combined.lower()
    assert fake_session.history == [{"role": "user", "content": "original"}]


@pytest.mark.asyncio
async def test_load_invalid_json_reports_error(fake_session, isolated_conversations, capsys):
    """/load on a corrupt JSON file must print a parsing error and not crash."""
    from lilith_cli.extra_commands import run_load_command

    isolated_conversations.mkdir(parents=True, exist_ok=True)
    (isolated_conversations / "broken.json").write_text("{not valid json", encoding="utf-8")
    fake_session.history = [{"role": "user", "content": "original"}]

    await run_load_command(fake_session, "broken")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "error" in combined.lower() or "formato" in combined.lower()
    assert fake_session.history == [{"role": "user", "content": "original"}]


@pytest.mark.asyncio
async def test_load_non_dict_payload_reports_error(fake_session, isolated_conversations, capsys):
    """/load with a JSON value that is not a dict must print a format error."""
    from lilith_cli.extra_commands import run_load_command

    isolated_conversations.mkdir(parents=True, exist_ok=True)
    (isolated_conversations / "list.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    fake_session.history = [{"role": "user", "content": "original"}]

    await run_load_command(fake_session, "list")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "formato" in combined.lower() or "inv" in combined.lower()
    assert fake_session.history == [{"role": "user", "content": "original"}]