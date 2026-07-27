"""Tests for the ``/note`` slash command.

The command lives in ``lilith_cli.notes_command`` and persists free-form
text notes to ``~/.yggdrasil/notes.json``. These tests cover the four
subcommands (``add``, ``list``, ``show``, ``edit``, ``rm``, ``clear``),
the length guard, and the alias surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from lilith_cli import notes_command
from lilith_cli.notes_command import (
    _MAX_NOTE_CHARS,
    _add_note,
    _clear_all,
    _edit_note,
    _find_note,
    _load_notes,
    _next_id,
    _rm_note,
    _save_notes,
    run_note_command,
)


class _CapturingConsole:
    """Tiny stand-in for the Rich console used in CLI rendering tests."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        text = " ".join(str(a) for a in args)
        self.messages.append(text)


class FakeSession:
    """Placeholder AgentSession — /note doesn't touch the session."""

    # Annotate as Any so pyright doesn't complain when we pass it to a
    # function typed as AgentSession; run_note_command never reads it.
    def __init__(self) -> None:
        self._placeholder: Any = None


@pytest.fixture
def fake_console(monkeypatch: pytest.MonkeyPatch) -> _CapturingConsole:
    cap = _CapturingConsole()
    monkeypatch.setattr(notes_command, "console", cap)
    monkeypatch.setattr(
        notes_command, "render_error", lambda msg: cap.print(f"ERR {msg}")
    )
    return cap


@pytest.fixture
def isolated_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """Redirect the notes store to a temporary directory."""
    notes_path = tmp_path / "notes.json"
    monkeypatch.setattr(notes_command, "_NOTES_PATH", notes_path)
    yield notes_path


# ── pure helpers ──────────────────────────────────────────────────────


def test_load_notes_missing_returns_empty(isolated_notes: Path) -> None:
    """No file yet → empty list, no exception."""
    assert _load_notes() == []


def test_load_notes_corrupt_returns_empty(
    isolated_notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed JSON store must not break the REPL."""
    isolated_notes.write_text("{not valid json", encoding="utf-8")
    assert _load_notes() == []


def test_load_notes_filters_non_dict_entries(isolated_notes: Path) -> None:
    """Only dicts survive the filter; non-dict entries are dropped."""
    isolated_notes.write_text(
        '[{"id": 1, "text": "ok"}, {"oops": true}, "stray"]',
        encoding="utf-8",
    )
    notes = _load_notes()
    assert len(notes) == 1
    assert notes[0]["text"] == "ok"


def test_next_id_empty_returns_one(isolated_notes: Path) -> None:
    assert _next_id([]) == 1


def test_next_id_increments_past_max(isolated_notes: Path) -> None:
    notes = [{"id": 3}, {"id": 7, "text": "x"}, {"id": 2}]
    assert _next_id(notes) == 8


def test_find_note_returns_match_and_none(isolated_notes: Path) -> None:
    notes = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
    assert _find_note(notes, 2) == {"id": 2, "text": "b"}
    assert _find_note(notes, 99) is None


# ── mutating helpers ──────────────────────────────────────────────────


def test_add_note_persists_and_increments_id(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("primer texto")
    _add_note("segundo texto")

    data = _load_notes()
    assert [n["id"] for n in data] == [1, 2]
    assert [n["text"] for n in data] == ["primer texto", "segundo texto"]
    # Timestamps should be ISO-formatted strings
    assert all("T" in n["created"] for n in data)


def test_add_note_empty_text_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("   ")
    assert _load_notes() == []
    assert any("vacía" in m for m in fake_console.messages)


def test_add_note_over_max_length_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    huge = "x" * (_MAX_NOTE_CHARS + 10)
    _add_note(huge)
    assert _load_notes() == []
    assert any("demasiado larga" in m for m in fake_console.messages)


def test_edit_note_replaces_text_and_keeps_id(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("original")
    _edit_note(1, "reemplazo")

    notes = _load_notes()
    assert notes[0]["id"] == 1
    assert notes[0]["text"] == "reemplazo"
    assert "updated" in notes[0]


def test_edit_note_unknown_id_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("algo")
    _edit_note(99, "nuevo")

    notes = _load_notes()
    assert notes[0]["text"] == "algo"  # unchanged
    assert any("99" in m and "no encontrada" in m for m in fake_console.messages)


def test_edit_note_empty_text_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("algo")
    _edit_note(1, "  ")
    assert _load_notes()[0]["text"] == "algo"
    assert any("vacío" in m for m in fake_console.messages)


def test_rm_note_deletes_by_id(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("uno")
    _add_note("dos")
    _rm_note(1)

    notes = _load_notes()
    assert len(notes) == 1
    assert notes[0]["id"] == 2


def test_rm_note_unknown_id_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("uno")
    _rm_note(42)
    assert len(_load_notes()) == 1
    assert any("42" in m for m in fake_console.messages)


def test_clear_all_wipes_store(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _add_note("uno")
    _add_note("dos")
    _clear_all()

    assert _load_notes() == []


def test_clear_all_with_no_notes_reports_empty(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _clear_all()
    assert any("No hay notas" in m for m in fake_console.messages)


# ── async entry point ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_note_add(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    await run_note_command(FakeSession(), "primera idea")
    notes = _load_notes()
    assert notes[0]["text"] == "primera idea"


@pytest.mark.asyncio
async def test_run_note_help(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    await run_note_command(FakeSession(), "")
    await run_note_command(FakeSession(), "help")
    await run_note_command(FakeSession(), "?")

    usage_msgs = [m for m in fake_console.messages if "/note" in m]
    assert len(usage_msgs) == 3


@pytest.mark.asyncio
async def test_run_note_list(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _save_notes(
        [
            {"id": 1, "text": "alpha", "created": "2026-07-26T10:00:00"},
            {"id": 2, "text": "beta", "created": "2026-07-26T11:30:00"},
        ]
    )

    await run_note_command(FakeSession(), "list")
    out = "\n".join(fake_console.messages)
    assert "alpha" in out and "beta" in out
    # Plain list should NOT include IDs column
    assert "  1." not in out


@pytest.mark.asyncio
async def test_run_note_list_with_ids(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _save_notes(
        [{"id": 1, "text": "alpha", "created": "2026-07-26T10:00:00"}]
    )
    await run_note_command(FakeSession(), "list --ids")
    out = "\n".join(fake_console.messages)
    assert "alpha" in out
    assert "1" in out  # the ID column


@pytest.mark.asyncio
async def test_run_note_list_empty(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    await run_note_command(FakeSession(), "list")
    out = "\n".join(fake_console.messages)
    assert "No hay notas" in out


@pytest.mark.asyncio
async def test_run_note_show(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _save_notes(
        [{"id": 1, "text": "cuerpo completo", "created": "2026-07-26T12:00:00"}]
    )
    await run_note_command(FakeSession(), "show 1")
    out = "\n".join(fake_console.messages)
    assert "cuerpo completo" in out


@pytest.mark.asyncio
async def test_run_note_show_invalid_id_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    await run_note_command(FakeSession(), "show abc")
    assert any("Uso: /note show" in m for m in fake_console.messages)


@pytest.mark.asyncio
async def test_run_note_show_unknown_id_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    await run_note_command(FakeSession(), "show 99")
    assert any("99" in m for m in fake_console.messages)


@pytest.mark.asyncio
async def test_run_note_edit(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _save_notes(
        [{"id": 1, "text": "original", "created": "2026-07-26T10:00:00"}]
    )
    await run_note_command(FakeSession(), "edit 1 actualizado")
    assert _load_notes()[0]["text"] == "actualizado"


@pytest.mark.asyncio
async def test_run_note_edit_missing_args_errors(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    await run_note_command(FakeSession(), "edit")
    await run_note_command(FakeSession(), "edit 1")
    errs = [m for m in fake_console.messages if "Uso: /note edit" in m]
    assert len(errs) == 2


@pytest.mark.asyncio
async def test_run_note_rm(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _save_notes(
        [{"id": 1, "text": "borrable", "created": "2026-07-26T10:00:00"}]
    )
    await run_note_command(FakeSession(), "rm 1")
    assert _load_notes() == []


@pytest.mark.asyncio
async def test_run_note_clear(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    _save_notes(
        [
            {"id": 1, "text": "x", "created": "2026-07-26T10:00:00"},
            {"id": 2, "text": "y", "created": "2026-07-26T10:01:00"},
        ]
    )
    await run_note_command(FakeSession(), "clear")
    assert _load_notes() == []


@pytest.mark.asyncio
async def test_run_note_uses_text_as_default(
    isolated_notes: Path, fake_console: _CapturingConsole
) -> None:
    """Any argument that isn't a known subcommand becomes a new note body."""
    await run_note_command(
        FakeSession(), "esto no es list ni show ni rm ni edit ni clear"
    )
    notes = _load_notes()
    assert len(notes) == 1
    assert "esto no es list" in notes[0]["text"]