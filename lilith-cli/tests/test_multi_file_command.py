"""Tests for the /multi-file slash command (atomic multi-file edits)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import _parse_multi_file_spec, run_multi_file_command


class DummySession:
    def __init__(self):
        self.history = []


def test_parse_multi_file_spec_handles_two_edits() -> None:
    """El parser separa rutas y strings correctamente."""
    spec = "[a.txt] hello -> hola ; [b.txt] foo -> baz"
    edits = _parse_multi_file_spec(spec)
    assert edits == [
        {"path": "a.txt", "old_string": "hello", "new_string": "hola"},
        {"path": "b.txt", "old_string": "foo", "new_string": "baz"},
    ]


def test_parse_multi_file_spec_handles_single_edit() -> None:
    spec = "[main.py] print(x) -> print(repr(x))"
    edits = _parse_multi_file_spec(spec)
    assert edits == [{"path": "main.py", "old_string": "print(x)", "new_string": "print(repr(x))"}]


def test_parse_multi_file_spec_rejects_invalid() -> None:
    assert _parse_multi_file_spec("no brackets here") == []
    assert _parse_multi_file_spec("") == []


@pytest.mark.asyncio
async def test_multi_file_command_edits_two_files(tmp_path, monkeypatch) -> None:
    """Edita dos archivos de forma atómica."""
    monkeypatch.chdir(tmp_path)
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello world", encoding="utf-8")
    f2.write_text("foo bar", encoding="utf-8")

    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_multi_file_command(
            session, "[a.txt] hello -> hola ; [b.txt] foo -> baz"
        )

    assert f1.read_text(encoding="utf-8") == "hola world"
    assert f2.read_text(encoding="utf-8") == "baz bar"


@pytest.mark.asyncio
async def test_multi_file_command_rolls_back_on_failure(tmp_path, monkeypatch) -> None:
    """Si una edición falla, todas las anteriores se rollbackean."""
    monkeypatch.chdir(tmp_path)
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello world", encoding="utf-8")
    f2.write_text("foo bar", encoding="utf-8")

    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    # "missing" doesn't exist in f2 so batch_edit should fail and rollback f1.
    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_multi_file_command(
            session, "[a.txt] hello -> hola ; [b.txt] missing -> baz"
        )

    # f1 should still contain the ORIGINAL content (rolled back)
    assert "hello world" in f1.read_text(encoding="utf-8")
