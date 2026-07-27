"""Tests for the ``/undo-peek`` slash command.

The command lives in ``lilith_cli.undo_command`` and re-uses the
:mod:`lilith_tools.undo` manager that the original ``/undo`` command uses.
These tests cover the three subcommands (``list``, ``clear``, numeric
peek) and a few edge cases (missing backups, identical backups).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

import pytest

from lilith_cli import undo_command
from lilith_cli.undo_command import (
    _build_diff,
    _format_entry,
    _print_usage,
    run_undo_peek_command,
)


class _CapturingConsole:
    """Tiny stand-in for the Rich console used in CLI rendering tests."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        text = " ".join(str(a) for a in args)
        self.messages.append(text)


class FakeSession:
    """Placeholder AgentSession — undo-peek doesn't touch the session."""


@pytest.fixture
def fake_console(monkeypatch: pytest.MonkeyPatch) -> _CapturingConsole:
    cap = _CapturingConsole()
    monkeypatch.setattr(undo_command, "console", cap)
    monkeypatch.setattr(undo_command, "render_error", lambda msg: cap.print(f"ERR {msg}"))
    return cap


@pytest.fixture
def isolated_undo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    """Redirect UndoManager storage to a temporary directory."""
    from lilith_tools.undo import UndoManager as _RealUndoManager

    manager = _RealUndoManager(root_dir=tmp_path)
    # Patch the name bound in lilith_cli.undo_command (module-level import).
    monkeypatch.setattr(undo_command, "UndoManager", lambda *a, **kw: manager)
    yield manager


# ── pure helpers ──────────────────────────────────────────────────────


class TestFormatEntry:
    def test_renders_metadata(self) -> str:
        entry = type("E", (), {})()
        entry.original_path = "/tmp/foo.py"
        entry.backup_path = "/tmp/bak/foo.py"
        entry.timestamp = 1700000000.0
        entry.tool = "file_write"
        line = _format_entry(1, 1, entry)
        assert "1/1." in line
        assert "file_write" in line
        assert "/tmp/foo.py" in line


class TestBuildDiff:
    def test_identical_files_report_no_changes(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("hello\nworld\n", encoding="utf-8")
        b.write_text("hello\nworld\n", encoding="utf-8")
        text, has_changes = _build_diff(a, b)
        assert has_changes is False
        assert text == ""

    def test_modified_files_produce_diff(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("line1\nline2\n", encoding="utf-8")
        b.write_text("line1\nline2-changed\n", encoding="utf-8")
        text, has_changes = _build_diff(a, b)
        assert has_changes is True
        assert "-line2" in text
        assert "+line2-changed" in text

    def test_missing_backup_returns_placeholder(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        a.write_text("hello\n", encoding="utf-8")
        text, has_changes = _build_diff(a, tmp_path / "missing.txt")
        assert has_changes is False
        assert "Backup eliminado" in text

    def test_missing_original_treats_as_added(self, tmp_path: Path) -> None:
        a = tmp_path / "missing.txt"
        b = tmp_path / "b.txt"
        b.write_text("created\n", encoding="utf-8")
        text, has_changes = _build_diff(a, b)
        assert has_changes is True
        assert "+created" in text


# ── print helpers ──────────────────────────────────────────────────────


class TestPrintUsage:
    def test_renders_help(self, fake_console: _CapturingConsole) -> None:
        _print_usage()
        joined = "\n".join(fake_console.messages)
        assert "/undo-peek" in joined
        assert "/undo-peek <N>" in joined
        assert "/undo-peek clear" in joined
        assert "/undo-diff" in joined
        assert "/peeks" in joined


# ── command entry point ────────────────────────────────────────────────


class TestRunUndoPeek:
    def test_empty_state_list(self, fake_console: _CapturingConsole, isolated_undo: Any) -> None:
        asyncio.run(run_undo_peek_command(FakeSession(), "list"))
        joined = "\n".join(fake_console.messages)
        assert "No hay backups pendientes" in joined

    def test_unknown_subcommand_shows_usage(
        self, fake_console: _CapturingConsole, isolated_undo: Any,
    ) -> None:
        asyncio.run(run_undo_peek_command(FakeSession(), "frobnicate"))
        joined = "\n".join(fake_console.messages)
        assert "/undo-peek" in joined  # usage block

    def test_peek_with_no_backups(
        self, fake_console: _CapturingConsole, isolated_undo: Any,
    ) -> None:
        asyncio.run(run_undo_peek_command(FakeSession(), "1"))
        joined = "\n".join(fake_console.messages)
        assert "No hay backups pendientes" in joined

    def test_peek_index_out_of_range(
        self, fake_console: _CapturingConsole, isolated_undo: Any, tmp_path: Path,
    ) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello\n", encoding="utf-8")
        isolated_undo.backup(str(f), tool="file_write")
        asyncio.run(run_undo_peek_command(FakeSession(), "5"))
        joined = "\n".join(fake_console.messages)
        assert "Indice fuera de rango" in joined

    def test_peek_diff_with_pending_backup(
        self, fake_console: _CapturingConsole, isolated_undo: Any, tmp_path: Path,
    ) -> None:
        f = tmp_path / "editable.txt"
        f.write_text("version original\n", encoding="utf-8")
        backup = isolated_undo.backup(str(f), tool="file_write")
        assert backup is not None
        # Now overwrite the file (this is the "destructive" change that
        # /undo would roll back).
        f.write_text("version modificada\n", encoding="utf-8")
        asyncio.run(run_undo_peek_command(FakeSession(), "1"))
        joined = "\n".join(fake_console.messages)
        assert "version original" in joined
        assert "version modificada" in joined
        assert "-version modificada" in joined
        assert "+version original" in joined

    def test_peek_identical_backup_reports_nothing_to_undo(
        self, fake_console: _CapturingConsole, isolated_undo: Any, tmp_path: Path,
    ) -> None:
        f = tmp_path / "stable.txt"
        f.write_text("same content\n", encoding="utf-8")
        backup = isolated_undo.backup(str(f), tool="file_write")
        assert backup is not None
        # The file didn't change — peeking should report "nothing to undo".
        asyncio.run(run_undo_peek_command(FakeSession(), "1"))
        joined = "\n".join(fake_console.messages)
        assert "no hay nada que deshacer" in joined

    def test_clear_with_no_backups(
        self, fake_console: _CapturingConsole, isolated_undo: Any,
    ) -> None:
        asyncio.run(run_undo_peek_command(FakeSession(), "clear"))
        joined = "\n".join(fake_console.messages)
        assert "No hay backups pendientes para borrar" in joined

    def test_clear_wipes_pending_backups(
        self, fake_console: _CapturingConsole, isolated_undo: Any, tmp_path: Path,
    ) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello\n", encoding="utf-8")
        isolated_undo.backup(str(f), tool="file_write")
        isolated_undo.backup(str(f), tool="file_edit")
        assert len(isolated_undo.list()) == 2
        asyncio.run(run_undo_peek_command(FakeSession(), "clear"))
        assert isolated_undo.list() == []
        joined = "\n".join(fake_console.messages)
        assert "2 backup(s) borrados" in joined

    def test_help_subcommand(
        self, fake_console: _CapturingConsole, isolated_undo: Any,
    ) -> None:
        asyncio.run(run_undo_peek_command(FakeSession(), "help"))
        joined = "\n".join(fake_console.messages)
        assert "/undo-peek" in joined
        assert "clear" in joined

    def test_list_shows_entries(
        self, fake_console: _CapturingConsole, isolated_undo: Any, tmp_path: Path,
    ) -> None:
        f = tmp_path / "f.txt"
        f.write_text("content\n", encoding="utf-8")
        isolated_undo.backup(str(f), tool="file_write")
        isolated_undo.backup(str(f), tool="file_edit")
        asyncio.run(run_undo_peek_command(FakeSession(), "list"))
        joined = "\n".join(fake_console.messages)
        assert "1/2." in joined
        assert "2/2." in joined
        assert "file_write" in joined
        assert "file_edit" in joined


# ── dispatch wiring ────────────────────────────────────────────────────


class TestReplDispatch:
    """Make sure ``repl.py`` recognises the new command names."""

    def test_command_is_importable(self) -> None:
        from lilith_cli.repl import run_undo_peek_command  # noqa: F401

    def test_command_is_in_slash_list(self) -> None:
        from lilith_cli import repl

        # The slash-command list is built lazily on module import; ensure
        # our three names were added (also includes /undo-peek itself).
        # We re-import to be safe in case the list is mutated by other tests.
        names = repl._SLASH_COMMANDS
        assert "/undo-peek" in names
        assert "/undo-diff" in names
        assert "/peeks" in names