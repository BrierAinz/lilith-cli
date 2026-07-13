"""Tests for AgentMixin diff preview, backup and undo."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from lilith_cli.ide import AgentDiffScreen, LilithIDEApp
from lilith_cli.ide.utils.helpers import (
    ProposedChange,
    _build_proposed_changes,
    _extract_fenced_files,
    _undo_last,
)


class TestExtractFencedFiles:
    """Unit tests for extracting fenced files with an explicit path."""

    def test_extract_fenced_file_with_path(self):
        text = "```python src/main.py\ndef hello():\n    return 'hi'\n```"
        items = _extract_fenced_files(text)
        assert len(items) == 1
        assert items[0]["path"] == "src/main.py"
        assert items[0]["language"] == "python"
        assert "def hello():" in items[0]["content"]

    def test_ignore_fenced_file_without_path(self):
        text = "```python\ndef hello(): pass\n```"
        assert _extract_fenced_files(text) == []

    def test_ignore_plain_text(self):
        assert _extract_fenced_files("sin bloques") == []


class TestBuildProposedChanges:
    """Unit tests for building ProposedChange objects."""

    def test_build_from_fenced_file(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        file = src / "main.py"
        file.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

        text = "```python src/main.py\ndef hello():\n    return 'hello'\n```"
        changes = _build_proposed_changes(tmp_path, text)
        assert len(changes) == 1
        assert changes[0].rel_path == "src/main.py"
        assert "return 'hello'" in changes[0].proposed
        assert "-    return 'hi'" in changes[0].diff

    def test_build_creates_new_file(self, tmp_path):
        text = "```python src/new.py\nprint('new')\n```"
        changes = _build_proposed_changes(tmp_path, text)
        assert len(changes) == 1
        assert changes[0].rel_path == "src/new.py"
        assert not changes[0].path.exists()
        assert "print('new')" in changes[0].proposed

    def test_build_from_unified_diff(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        file = src / "main.py"
        file.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

        diff = """--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'hi'
+    return 'hello'
"""
        changes = _build_proposed_changes(tmp_path, diff)
        assert len(changes) == 1
        assert changes[0].rel_path == "src/main.py"
        assert "return 'hello'" in changes[0].proposed


class TestAgentDiffScreen:
    """Smoke tests for the diff preview modal."""

    def test_screen_compose(self, tmp_path):
        change = ProposedChange(
            path=tmp_path / "a.py",
            rel_path="a.py",
            current="x = 1\n",
            proposed="x = 2\n",
            diff="",
        )
        screen = AgentDiffScreen([change], tmp_path)
        assert screen.changes == [change]
        assert screen._accepted == [False]


class TestAgentDiffIntegration:
    """Integration tests for intercepting agent proposals."""

    async def test_finalize_turn_opens_agent_diff_screen(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        src = tmp_path / "src"
        src.mkdir()
        file = src / "main.py"
        file.write_text("x = 1\n", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            text = "```python src/main.py\nx = 2\n```"
            app._finalize_turn({}, text)
            await pilot.pause()
            assert isinstance(app.screen, AgentDiffScreen)

    async def test_finalize_turn_forge_runestones_when_no_path(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            text = "```python\nx = 1\n```"
            app._finalize_turn({}, text)
            await pilot.pause()
            assert len(app.runestone_forge.list()) == 1

    async def test_on_agent_diff_action_applies_and_creates_backup(
        self, fake_session, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("lilith_cli.ide.utils.helpers.CONFIG_DIR", tmp_path)
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        src = tmp_path / "src"
        src.mkdir()
        file = src / "main.py"
        file.write_text("x = 1\n", encoding="utf-8")
        change = _build_proposed_changes(tmp_path, "```python src/main.py\nx = 2\n```")[0]
        async with app.run_test(size=(120, 40)) as pilot:
            app._on_agent_diff_action([change])
            await pilot.pause()
            assert file.read_text(encoding="utf-8") == "x = 2\n"
            assert list(tmp_path.rglob("*.bak.*"))
            assert list((tmp_path / "backups").glob("*.bak.*"))

    async def test_undo_last_restores_files(self, fake_session, tmp_path, monkeypatch):
        monkeypatch.setattr("lilith_cli.ide.utils.helpers.CONFIG_DIR", tmp_path)
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        src = tmp_path / "src"
        src.mkdir()
        file = src / "main.py"
        file.write_text("original\n", encoding="utf-8")
        change = _build_proposed_changes(tmp_path, "```python src/main.py\nmodified\n```")[0]
        async with app.run_test(size=(120, 40)) as pilot:
            app._on_agent_diff_action([change])
            await pilot.pause()
            assert file.read_text(encoding="utf-8") == "modified\n"

            restored = _undo_last()
            assert restored == ["src/main.py"]
            assert file.read_text(encoding="utf-8") == "original\n"

    async def test_undo_last_command(self, fake_session, tmp_path, monkeypatch):
        monkeypatch.setattr("lilith_cli.ide.utils.helpers.CONFIG_DIR", tmp_path)
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        src = tmp_path / "src"
        src.mkdir()
        file = src / "main.py"
        file.write_text("original\n", encoding="utf-8")
        change = _build_proposed_changes(tmp_path, "```python src/main.py\nmodified\n```")[0]
        async with app.run_test(size=(120, 40)) as pilot:
            app._on_agent_diff_action([change])
            await pilot.pause()
            assert file.read_text(encoding="utf-8") == "modified\n"

            app._handle_slash("/undo-last")
            # Give the worker a couple of event-loop cycles to finish.
            await pilot.pause()
            await pilot.pause()
            assert file.read_text(encoding="utf-8") == "original\n"
