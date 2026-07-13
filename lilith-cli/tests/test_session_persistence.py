"""Tests for Lilith IDE session persistence."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from textual.widgets import TextArea

from lilith_cli.ide import IDEConfig, LilithIDEApp


class TestIDEConfigSessionFields:
    """Unit tests for the new session fields in IDEConfig."""

    def test_default_session_fields(self):
        cfg = IDEConfig()
        assert cfg.active_file == ""
        assert cfg.cursor_positions == {}
        assert cfg.sidebar_width is None
        assert cfg.terminal_fullscreen is False
        assert cfg.zen_mode is False
        assert cfg.terminal_height == 8

    def test_roundtrip_session_fields(self, tmp_path):
        cfg_path = tmp_path / "ide.yaml"
        cfg = IDEConfig(
            active_file="src/main.py",
            cursor_positions={"src/main.py": (2, 5), "README.md": (0, 0)},
            terminal_height=12,
            sidebar_width=30,
            terminal_fullscreen=True,
            zen_mode=True,
        )
        cfg.save(cfg_path)
        loaded = IDEConfig.load(cfg_path)
        assert loaded.active_file == "src/main.py"
        assert loaded.cursor_positions == {"src/main.py": (2, 5), "README.md": (0, 0)}
        assert loaded.terminal_height == 12
        assert loaded.sidebar_width == 30
        assert loaded.terminal_fullscreen is True
        assert loaded.zen_mode is True


class TestSessionSaveState:
    """Tests for collecting and saving the live IDE session state."""

    async def test_save_session_state_collects_open_files_and_cursor(
        self, fake_session, tmp_path
    ):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("line1\nline2\nline3\n", encoding="utf-8")
        b.write_text("foo\nbar\nbaz\n", encoding="utf-8")

        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(a)
            await pilot.pause()
            app._open_file(b)
            await pilot.pause()

            editor = app._current_editor()
            assert editor is not None
            editor.cursor_location = (1, 3)
            await pilot.pause()

            app._save_session_state()

            assert set(app.ide_config.open_files) == {"a.py", "b.py"}
            assert app.ide_config.active_file == "b.py"
            assert app.ide_config.cursor_positions.get("b.py") == (1, 3)
            assert "a.py" in app.ide_config.cursor_positions

    async def test_save_session_persists_to_disk(
        self, fake_session, tmp_path, monkeypatch
    ):
        import lilith_cli.ide.config as _ide_config

        config_dir = tmp_path / "yggdrasil-config"
        monkeypatch.setattr(_ide_config, "CONFIG_DIR", config_dir)

        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        app.ide_config = IDEConfig.load()
        file = tmp_path / "session.py"
        file.write_text("x = 1\ny = 2\n", encoding="utf-8")

        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(file)
            await pilot.pause()
            editor = app._current_editor()
            editor.cursor_location = (0, 4)
            await pilot.pause()

            app._save_session()

        saved = IDEConfig.load()
        assert saved.open_files == ["session.py"]
        assert saved.active_file == "session.py"
        assert saved.cursor_positions == {"session.py": (0, 4)}


class TestSessionRestore:
    """Tests for restoring a previous IDE session."""

    async def test_restore_session_reopens_files_and_cursor(self, fake_session, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("line1\nline2\nline3\n", encoding="utf-8")
        b.write_text("foo\nbar\nbaz\n", encoding="utf-8")

        cfg = IDEConfig(
            open_files=["a.py", "b.py"],
            active_file="b.py",
            cursor_positions={"b.py": (2, 1)},
            terminal_height=10,
        )
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        app.ide_config = cfg

        async with app.run_test(size=(120, 40)) as pilot:
            # on_mount calls _restore_session; allow refreshes to settle.
            await pilot.pause()
            await pilot.pause()

            assert set(app._tab_paths.values()) == {a, b}
            assert app.current_file == b
            editor = app._current_editor()
            assert editor is not None
            assert editor.cursor_location == (2, 1)
            assert app._terminal_normal_height == 10
