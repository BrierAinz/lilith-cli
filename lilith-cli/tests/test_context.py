"""Tests for the Lilith IDE context system."""

from __future__ import annotations

import pytest
from pathlib import Path

from lilith_cli.ide.context import ContextItem, ContextManager


class TestContextParsing:
    """Unit tests for @-mention parsing."""

    def test_parse_single_mention(self):
        mgr = ContextManager(Path("/tmp"))
        mentions = mgr.parse_mentions("revisa @file:src/main.py")
        assert mentions == [("file", "src/main.py")]

    def test_parse_multiple_mentions(self):
        mgr = ContextManager(Path("/tmp"))
        mentions = mgr.parse_mentions("@file:a.py y @folder:src y @selection")
        assert mentions == [("file", "a.py"), ("folder", "src"), ("selection", "")]

    def test_parse_deduplicates(self):
        mgr = ContextManager(Path("/tmp"))
        mentions = mgr.parse_mentions("@file:x @file:x @selection")
        assert mentions == [("file", "x"), ("selection", "")]

    def test_strip_mentions(self):
        mgr = ContextManager(Path("/tmp"))
        cleaned = mgr.strip_mentions("revisa @file:src/main.py por favor")
        assert cleaned == "revisa  por favor"


class TestContextResolution:
    """Unit tests for context resolution."""

    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Proyecto\nHola.", encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    async def test_resolve_file(self, project):
        mgr = ContextManager(project)
        item = await mgr.resolve("file", "src/main.py")
        assert isinstance(item, ContextItem)
        assert item.kind == "file"
        assert "src/main.py" in item.name
        assert "print('hello')" in item.content

    @pytest.mark.asyncio
    async def test_resolve_file_missing(self, project):
        mgr = ContextManager(project)
        item = await mgr.resolve("file", "noexiste.txt")
        assert item.kind == "file"
        assert "no encontrado" in item.content.lower()

    @pytest.mark.asyncio
    async def test_resolve_selection(self, project):
        mgr = ContextManager(project)
        item = await mgr.resolve("selection", "", get_selection=lambda: "selected text")
        assert item.kind == "selection"
        assert item.content == "selected text"

    @pytest.mark.asyncio
    async def test_resolve_folder(self, project):
        mgr = ContextManager(project)
        item = await mgr.resolve("folder", "src")
        assert item.kind == "folder"
        assert "src/main.py" in item.content

    @pytest.mark.asyncio
    async def test_resolve_project(self, project):
        mgr = ContextManager(project)
        item = await mgr.resolve("project", "")
        assert item.kind == "project"
        assert "README.md" in item.content
        assert "# Proyecto" in item.content

    @pytest.mark.asyncio
    async def test_resolve_terminal_output(self, project):
        mgr = ContextManager(project)
        mgr.record_terminal_output(["line 1", "line 2"])
        item = await mgr.resolve("terminal-output", "")
        assert item.kind == "terminal-output"
        assert "line 1" in item.content
        assert "line 2" in item.content

    @pytest.mark.asyncio
    async def test_resolve_all(self, project):
        mgr = ContextManager(project)
        items = await mgr.resolve_all("revisa @file:src/main.py", current_file=project / "src" / "main.py")
        assert len(items) == 1
        assert items[0].kind == "file"


class TestContextItem:
    """Unit tests for ContextItem serialization."""

    def test_context_item_str(self):
        item = ContextItem(kind="file", name="x.py", content="code")
        text = str(item)
        assert "<file:x.py>" in text
        assert "code" in text
        assert "</file:x.py>" in text
