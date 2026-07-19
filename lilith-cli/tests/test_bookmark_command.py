"""Tests for the /bookmark slash command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lilith_cli.commands import BookmarkCommand, CommandRegistry


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


@pytest.fixture
def tmp_bookmarks_path(monkeypatch, tmp_path: Path) -> Path:
    """Override the bookmarks path to a temporary directory."""
    bookmarks_path = tmp_path / "bookmarks.json"
    monkeypatch.setattr(
        "lilith_cli.commands._BOOKMARKS_PATH",
        bookmarks_path,
    )
    return bookmarks_path


@pytest.mark.asyncio
async def test_bookmark_command_add(tmp_bookmarks_path: Path) -> None:
    """BookmarkCommand should add a bookmark at the current history position."""
    session = _DummySession(history=[
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "¡Hola!"},
    ])
    cmd = BookmarkCommand(session)
    assert cmd.name == "bookmark"
    await cmd.execute("primero")

    assert tmp_bookmarks_path.exists()
    data = json.loads(tmp_bookmarks_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["name"] == "primero"
    assert data[0]["index"] == 1
    assert data[0]["id"] == 1


@pytest.mark.asyncio
async def test_bookmark_command_list_and_go(tmp_bookmarks_path: Path) -> None:
    """BookmarkCommand should list bookmarks and show the referenced message."""
    session = _DummySession(history=[
        {"role": "user", "content": "Pregunta"},
        {"role": "assistant", "content": "Respuesta"},
    ])
    cmd = BookmarkCommand(session)

    await cmd.execute("mi marcador")
    await cmd.execute("list")
    await cmd.execute("go 1")

    data = json.loads(tmp_bookmarks_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["name"] == "mi marcador"


@pytest.mark.asyncio
async def test_bookmark_command_delete(tmp_bookmarks_path: Path) -> None:
    """BookmarkCommand should delete a bookmark by ID."""
    session = _DummySession(history=[{"role": "user", "content": "Hola"}])
    cmd = BookmarkCommand(session)

    await cmd.execute("borrable")
    await cmd.execute("delete 1")

    assert json.loads(tmp_bookmarks_path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_bookmark_command_registry(fake_session) -> None:
    """BookmarkCommand should be discoverable by the CommandRegistry."""
    registry = CommandRegistry(fake_session)
    registry.discover()
    assert registry.get("bookmark") is not None
    assert registry.get("bm") is not None
    assert registry.get("mark") is not None


@pytest.mark.asyncio
async def test_bookmark_rename_persists(tmp_bookmarks_path: Path) -> None:
    """/bookmark rename <id> <new name> updates the bookmark's name on disk."""
    session = _DummySession(history=[{"role": "user", "content": "x"}])
    cmd = BookmarkCommand(session)
    await cmd.execute("original")
    await cmd.execute("rename 1 mejor nombre")

    data = json.loads(tmp_bookmarks_path.read_text(encoding="utf-8"))
    assert data[0]["name"] == "mejor nombre"
    assert data[0]["id"] == 1


@pytest.mark.asyncio
async def test_bookmark_rename_invalid_id_errors(tmp_bookmarks_path: Path, capsys) -> None:
    """/bookmark rename abc foo renders a clean error (no traceback)."""
    session = _DummySession(history=[])
    cmd = BookmarkCommand(session)
    await cmd.execute("rename abc foo")

    out = capsys.readouterr().out
    assert "inv" in out.lower()  # "inválido"


@pytest.mark.asyncio
async def test_bookmark_rename_missing_id_errors(tmp_bookmarks_path: Path, capsys) -> None:
    """/bookmark rename 999 foo when no bookmark with id 999 exists errors."""
    session = _DummySession(history=[{"role": "user", "content": "x"}])
    cmd = BookmarkCommand(session)
    await cmd.execute("primero")
    await cmd.execute("rename 999 otro")

    out = capsys.readouterr().out
    assert "999" in out


@pytest.mark.asyncio
async def test_bookmark_search_finds_by_name(tmp_bookmarks_path: Path, capsys) -> None:
    """/bookmark search <text> renders matches whose name contains the query."""
    session = _DummySession(history=[
        {"role": "user", "content": "msg uno"},
        {"role": "assistant", "content": "ok"},
    ])
    cmd = BookmarkCommand(session)
    await cmd.execute("API design")
    await cmd.execute("Bug fix")
    await cmd.execute("search API")

    out = capsys.readouterr().out
    # The search results table is the section after the "Marcadores que
    # matchean" title; it must list API design but NOT Bug fix.
    search_section = out.split("Marcadores que matchean", 1)[-1]
    assert "API design" in search_section
    assert "Bug fix" not in search_section


@pytest.mark.asyncio
async def test_bookmark_search_finds_by_content(tmp_bookmarks_path: Path, capsys) -> None:
    """/bookmark search <text> also matches against the bookmarked message body."""
    # The bookmark records index = len(history)-1 at creation time. With
    # history [user("kubernetes"), assistant("ok")], the bookmark
    # points at the assistant reply (index 1). /bookmark search should
    # still match if the query hits that reply.
    session = _DummySession(history=[
        {"role": "user", "content": "kubernetes intro"},
        {"role": "assistant", "content": "ok, let's talk about pods"},
    ])
    cmd = BookmarkCommand(session)
    await cmd.execute("primero")  # index = 1 (assistant reply)
    await cmd.execute("search pods")

    out = capsys.readouterr().out
    assert "primero" in out
    assert "contenido" in out  # match-type column


@pytest.mark.asyncio
async def test_bookmark_search_no_match(tmp_bookmarks_path: Path, capsys) -> None:
    """/bookmark search with no hits says so explicitly."""
    session = _DummySession(history=[])
    cmd = BookmarkCommand(session)
    await cmd.execute("foo")
    await cmd.execute("search nothingmatches")

    out = capsys.readouterr().out
    assert "nothingmatches" in out
    assert "Sin marcadores" in out
