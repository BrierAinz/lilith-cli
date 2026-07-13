"""Tests for the /replay slash command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_replays(tmp_path: Path, monkeypatch):
    """Redirect the replay directory to a tmp_path so tests do not touch real config."""
    from lilith_cli import extra_commands as ec

    fake_dir = tmp_path / "replays"
    fake_dir.mkdir()
    monkeypatch.setattr(ec, "_REPLAY_DIR", fake_dir)
    return fake_dir


@pytest.mark.asyncio
async def test_replay_list_empty(fake_session, isolated_replays):
    """/replay with no args on an empty dir prints the empty-state message."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "No hay replays guardados" in combined


@pytest.mark.asyncio
async def test_replay_list_renders_saved_names(fake_session, isolated_replays):
    """/replay list must enumerate existing replay names."""
    (isolated_replays / "demo.json").write_text(
        json.dumps([{"role": "user", "content": "hola"}]),
        encoding="utf-8",
    )
    (isolated_replays / "other.json").write_text(
        json.dumps([{"role": "user", "content": "chau"}]),
        encoding="utf-8",
    )

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "list")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Replays guardados" in combined
    assert "demo" in combined
    assert "other" in combined


@pytest.mark.asyncio
async def test_replay_save_writes_file(fake_session, isolated_replays):
    """/replay save <name> must persist session.history to <name>.json."""
    fake_session.history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "chau"},
    ]

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "save demo")

    target = isolated_replays / "demo.json"
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == fake_session.history


@pytest.mark.asyncio
async def test_replay_save_without_name_reports_error(fake_session, isolated_replays):
    """/replay save (no name) must print a usage error and not write a file."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "save")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Uso:" in combined
    assert not any(isolated_replays.glob("*.json"))


@pytest.mark.asyncio
async def test_replay_save_without_history_reports_error(fake_session, isolated_replays):
    """/replay save must refuse to save when session.history is empty."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "save demo")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "historial" in combined.lower() or "guardar" in combined.lower()
    assert not any(isolated_replays.glob("*.json"))


@pytest.mark.asyncio
async def test_replay_load_populates_session_history(fake_session, isolated_replays):
    """/replay load <name> must replace session.history with the stored messages."""
    saved = [
        {"role": "user", "content": "primero"},
        {"role": "assistant", "content": "respuesta"},
    ]
    (isolated_replays / "demo.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "load demo")

    assert fake_session.history == saved


@pytest.mark.asyncio
async def test_replay_load_by_id(fake_session, isolated_replays):
    """/replay <name> (no subcommand) must also load the named replay."""
    saved = [{"role": "user", "content": "x"}]
    (isolated_replays / "demo.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "demo")

    assert fake_session.history == saved


@pytest.mark.asyncio
async def test_replay_load_missing_reports_error(fake_session, isolated_replays):
    """/replay load <unknown> must print a not-found error and not mutate history."""
    fake_session.history = [{"role": "user", "content": "original"}]

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "load does-not-exist")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "no encontrado" in combined.lower() or "no encontrada" in combined.lower()
    assert fake_session.history == [{"role": "user", "content": "original"}]


@pytest.mark.asyncio
async def test_replay_load_invalid_format_reports_error(fake_session, isolated_replays):
    """/replay load on a corrupt JSON file must print an error and not crash."""
    (isolated_replays / "bad.json").write_text("{not valid json", encoding="utf-8")
    fake_session.history = [{"role": "user", "content": "original"}]

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "load bad")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Error" in combined or "formato" in combined.lower() or "inválido" in combined.lower() or "invalido" in combined.lower()


@pytest.mark.asyncio
async def test_replay_load_non_list_payload_reports_error(fake_session, isolated_replays):
    """/replay load with a JSON file that is not a list of dicts must refuse."""
    (isolated_replays / "weird.json").write_text(
        json.dumps({"not": "a list"}),
        encoding="utf-8",
    )

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_replay_command

        await run_replay_command(fake_session, "load weird")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "inv" in combined.lower() or "formato" in combined.lower()
