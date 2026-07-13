"""Tests for the /snippet slash command.

Covers add, list, delete, search, and persistence across reloads.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lilith_cli.extra_commands import (
    _load_snippets,
    _save_snippets,
    run_snippet_command,
)


class _DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""


class _DummySession:
    """Minimal session stand-in (snippet command does not touch history)."""

    def __init__(self) -> None:
        self.config = _DummyConfig()
        self.memory = None
        self.history = []
        self.provider = MagicMock()
        self.system_prompt = ""


@pytest.fixture
def tmp_snippets_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect snippet storage to a per-test temporary file."""
    snippets_path = tmp_path / "snippets.json"
    monkeypatch.setattr(
        "lilith_cli.extra_commands._SNIPPETS_PATH",
        snippets_path,
    )
    return snippets_path


@pytest.mark.asyncio
async def test_snippet_add_and_get(tmp_snippets_path: Path) -> None:
    """Adding a snippet then retrieving it returns the stored content."""
    session = _DummySession()
    body = "def hola():\n    return 'mundo'"
    await run_snippet_command(session, f"add hola-fn python {body}")

    snippets = _load_snippets()
    assert "hola-fn" in snippets
    assert snippets["hola-fn"]["lang"] == "python"
    assert snippets["hola-fn"]["content"] == body
    assert snippets["hola-fn"]["created"]  # ISO timestamp populated


@pytest.mark.asyncio
async def test_snippet_list_empty_then_filled(tmp_snippets_path: Path) -> None:
    """Empty state shows dim message; after add the snippet is listed."""
    session = _DummySession()

    # No snippets yet — listing should not raise and storage should stay empty.
    await run_snippet_command(session, "")
    await run_snippet_command(session, "list")
    await run_snippet_command(session, "ls")
    assert _load_snippets() == {}

    # Add one and list again — storage should reflect it.
    await run_snippet_command(session, "add saludo text 'hola mundo'")
    snippets = _load_snippets()
    assert "saludo" in snippets
    assert snippets["saludo"]["content"] == "hola mundo"  # auto-stripped quotes


@pytest.mark.asyncio
async def test_snippet_delete(tmp_snippets_path: Path) -> None:
    """Deleting a snippet removes it from storage."""
    session = _DummySession()
    await run_snippet_command(session, "add temporal text 'borrame'")
    assert "temporal" in _load_snippets()

    await run_snippet_command(session, "delete temporal")
    assert "temporal" not in _load_snippets()

    # rm alias also works.
    await run_snippet_command(session, "add otro text 'a'")
    await run_snippet_command(session, "rm otro")
    assert "otro" not in _load_snippets()


@pytest.mark.asyncio
async def test_snippet_search(tmp_snippets_path: Path) -> None:
    """Search matches by name, content, and tags (case-insensitive substring)."""
    session = _DummySession()
    await run_snippet_command(session, "add utils-python python 'def add(a,b): return a+b'")
    await run_snippet_command(session, "add utils-rust rust 'fn add(a:i32,b:i32)->i32{a+b}'")
    await run_snippet_command(session, "add greet text 'print(\"hola\")'")

    # Tag a snippet manually for the search-by-tags check.
    data = _load_snippets()
    data["utils-python"]["tags"] = ["math", "arithmetic"]
    _save_snippets(data)

    # Match by name fragment
    await run_snippet_command(session, "search python")
    # Match by content
    await run_snippet_command(session, "search return a+b")
    # Match by tag
    await run_snippet_command(session, "search arithmetic")

    # Direct storage assertion: search query 'rust' should land only utils-rust.
    data = _load_snippets()
    q = "rust"
    matches = [
        name for name, entry in data.items()
        if q in name.lower()
        or q in str(entry.get("content", "")).lower()
        or any(q in str(t).lower() for t in entry.get("tags") or [])
    ]
    assert matches == ["utils-rust"]


@pytest.mark.asyncio
async def test_snippet_persists_across_loads(tmp_snippets_path: Path) -> None:
    """A snippet added in one call survives a fresh ``_load_snippets`` call."""
    session = _DummySession()
    await run_snippet_command(session, "add persistente bash 'echo hi'")

    # Fresh read — should see the file on disk.
    fresh = _load_snippets()
    assert "persistente" in fresh
    assert fresh["persistente"]["lang"] == "bash"
    assert fresh["persistente"]["content"] == "echo hi"