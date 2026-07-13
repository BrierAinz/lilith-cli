"""Tests for the /macro slash command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.commands import CommandRegistry, MacroCommand


class _DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""


class _DummySession:
    def __init__(self, history: list | None = None) -> None:
        self.config = _DummyConfig()
        self.memory = None
        self.history = list(history) if history is not None else []
        self.provider = MagicMock()
        self.system_prompt = ""
        self._command_history: list[dict[str, str]] = []


@pytest.fixture
def tmp_macros_path(monkeypatch, tmp_path: Path) -> Path:
    """Override the macros path to a temporary directory."""
    macros_path = tmp_path / "macros.json"
    monkeypatch.setattr(
        "lilith_cli.commands._MACROS_PATH",
        macros_path,
    )
    return macros_path


@pytest.fixture
def clean_recording(monkeypatch) -> None:
    """Clear the in-memory macro recording state before each test."""
    monkeypatch.setattr(
        "lilith_cli.commands._macro_recording",
        {},
    )


@pytest.mark.asyncio
async def test_macro_record_stop_and_play(
    tmp_macros_path: Path, clean_recording: None
) -> None:
    """MacroCommand should record, stop, and play back slash commands."""
    import lilith_cli.commands as commands_mod

    session = _DummySession()
    cmd = MacroCommand(session)
    key = id(session)

    await cmd.execute("record demo")
    # Simulate the REPL: while recording, slash commands are appended raw.
    commands_mod._macro_recording[key].append("/help")
    commands_mod._macro_recording[key].append("/commands")
    await cmd.execute("stop")

    assert tmp_macros_path.exists()
    data = json.loads(tmp_macros_path.read_text(encoding="utf-8"))
    assert data.get("demo") == ["/help", "/commands"]

    await cmd.execute("play demo")
    assert any(entry["name"] == "macro-step" for entry in session._command_history)


@pytest.mark.asyncio
async def test_macro_delete_and_registry(fake_session) -> None:
    """MacroCommand should delete macros and be discoverable by registry."""
    from lilith_cli.commands import _load_macros, _save_macros

    tmp_macros_path = Path.home() / "tmp_macros_for_test.json"
    tmp_macros_path.write_text(
        json.dumps({"legacy": ["/help"]}),
        encoding="utf-8",
    )
    import lilith_cli.commands as commands_mod

    original_path = commands_mod._MACROS_PATH
    commands_mod._MACROS_PATH = tmp_macros_path
    try:
        cmd = MacroCommand(fake_session)
        await cmd.execute("delete legacy")
        data = json.loads(tmp_macros_path.read_text(encoding="utf-8"))
        assert "legacy" not in data

        registry = CommandRegistry(fake_session)
        registry.discover()
        assert registry.get("macro") is not None
        assert registry.get("macros") is not None
    finally:
        commands_mod._MACROS_PATH = original_path
        tmp_macros_path.unlink(missing_ok=True)

def _render_panels_to_text(prints):
    """Render captured Rich renderables to plain text."""
    from io import StringIO
    from rich.console import Console

    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=200, record=True)
    for entry in prints:
        for obj in entry:
            if obj is None or obj == "":
                continue
            try:
                c.print(obj)
            except Exception:
                buf.write(repr(obj))
    return c.export_text(clear=False)


@pytest.mark.asyncio
async def test_run_macro_record_shows_rec_indicator(
    tmp_macros_path, clean_recording, capsys
):
    """/macro record <name> renders a Rich Panel with REC indicator."""
    import lilith_cli.commands as commands_mod

    session = _DummySession()
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch(
        "lilith_cli.extra_commands.console.print", side_effect=capture
    ):
        from lilith_cli.extra_commands import run_macro_command

        await run_macro_command(session, "record demo")

    rendered = _render_panels_to_text(prints)
    assert "REC" in rendered
    assert "demo" in rendered
    assert "Iniciar grabacion" in rendered
    assert "Grabando macro" in rendered
    assert id(session) in commands_mod._macro_recording


@pytest.mark.asyncio
async def test_run_macro_stop_shows_stop_indicator(
    tmp_macros_path, clean_recording, capsys
):
    """/macro stop renders a Rich Panel with STOP indicator."""
    import lilith_cli.commands as commands_mod

    session = _DummySession()
    cmd_obj = MacroCommand(session)
    await cmd_obj.execute("record demo")
    commands_mod._macro_recording[id(session)].append("/help")

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch(
        "lilith_cli.extra_commands.console.print", side_effect=capture
    ):
        from lilith_cli.extra_commands import run_macro_command

        await run_macro_command(session, "stop")

    rendered = _render_panels_to_text(prints)
    assert "STOP" in rendered
    assert "Detener grabacion" in rendered
    assert "Macro guardada" in rendered
    assert id(session) not in commands_mod._macro_recording


@pytest.mark.asyncio
async def test_run_macro_play_no_status_indicator(
    tmp_macros_path, clean_recording, capsys
):
    """/macro play does NOT add a status indicator (only record/stop do)."""
    from lilith_cli.commands import _save_macros

    session = _DummySession()
    _save_macros({"demo": ["/help"]})

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch(
        "lilith_cli.extra_commands.console.print", side_effect=capture
    ):
        from lilith_cli.extra_commands import run_macro_command

        await run_macro_command(session, "play demo")

    rendered = _render_panels_to_text(prints)
    assert "REC" not in rendered
    assert "STOP" not in rendered
    assert "Iniciar grabacion" not in rendered
    # Underlying MacroCommand play still runs
    assert (
        "Reproduciendo macro" in rendered
        or "macro-step" in rendered
        or "finalizada" in rendered
    )