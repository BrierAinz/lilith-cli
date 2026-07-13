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
