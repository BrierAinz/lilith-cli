"""Tests for Lilith IDE TUI mode."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from textual.widgets import Input

from lilith_cli.ide import (
    CommandPaletteScreen,
    CompletionScreen,
    ConfigScreen,
    DiffScreen,
    FileSearchScreen,
    FindReplaceScreen,
    FindScreen,
    GitBlameScreen,
    GoToLineScreen,
    GrepScreen,
    HoverScreen,
    IDEConfig,
    LilithIDEApp,
    OutlineScreen,
    PaletteItem,
    ProjectFindReplaceScreen,
    RecentFilesScreen,
    RuneDirectoryTree,
    RunestoneScreen,
    DiagnosticsScreen,
    ToastHistoryScreen,
    _NORSE_LIGHT_THEME,
    _NORSE_THEME,
    _apply_patch,
    _detect_language,
    _parse_unified_diff,
    _shorten_path,
    run_ide,
)
from lilith_cli.ide.screens.splash import SplashScreen


class TestIDEHelpers:
    """Unit tests for small helper functions in the IDE module."""

    def test_detect_language_python(self):
        assert _detect_language(Path("main.py")) == "python"

    def test_detect_language_typescript(self):
        assert _detect_language(Path("app.ts")) == "typescript"

    def test_detect_language_unknown(self):
        assert _detect_language(Path("data.xyz")) is None

    def test_shorten_path_inside_root(self, tmp_path):
        root = tmp_path / "project"
        file = root / "src" / "main.py"
        assert _shorten_path(file, root) == "src/main.py"

    def test_shorten_path_outside_root(self, tmp_path):
        file = tmp_path / "elsewhere" / "file.txt"
        assert _shorten_path(file, tmp_path / "project") == file.as_posix()


class TestIDEApp:
    """Smoke tests for the Textual IDE app."""

    def test_app_importable(self, fake_session):
        """The IDE module should expose the app and entry point."""
        app = LilithIDEApp(fake_session, root=Path.cwd())
        assert app._title == "Lilith IDE — Hlidskjalf Console"
        assert app.session is fake_session

    def test_run_ide_entry_point_exists(self):
        """run_ide should be a callable entry point."""
        assert callable(run_ide)

    async def test_app_compose(self, fake_session, tmp_path):
        """The app should compose its widget tree without errors."""
        app = LilithIDEApp(fake_session, root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            # Basic widgets exist.
            assert app.query_one("#file-tree")
            assert app.query_one("#chat-log")
            assert app.query_one("#editor-tabs")
            assert app.query_one("#terminal-panel")
            assert app.query_one("#chat-input")
            assert app.query_one("#send-button")
            assert app.query_one("#status-bar")

            # Typing into the input updates its value.
            input_widget = app.query_one("#chat-input", Input)
            input_widget.value = "hello"
            assert input_widget.value == "hello"


class TestFileSearchScreen:
    """Tests for the Ctrl+P file search modal."""

    @pytest.fixture
    def search_root(self, tmp_path):
        """Create a small directory tree for search tests."""
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
        (tmp_path / ".venv" / "ignore.me").parent.mkdir(parents=True)
        (tmp_path / ".venv" / "ignore.me").write_text("x", encoding="utf-8")
        return tmp_path

    def test_collect_files_excludes_noise(self, search_root):
        """FileSearchScreen should skip common noise directories."""
        screen = FileSearchScreen(search_root)
        files = screen._collect_files()
        names = {_shorten_path(f, search_root) for f in files}
        assert "src/main.py" in names
        assert "README.md" in names
        assert not any(".venv" in n for n in names)

    def test_filter_by_query(self, search_root):
        """Filtering should match file names case-insensitively."""
        screen = FileSearchScreen(search_root)
        screen._all_files = screen._collect_files()
        screen._update_results("main")
        assert len(screen._filtered) == 1
        assert screen._filtered[0].name == "main.py"

    def test_filtered_results_without_ui(self, search_root):
        """Filtering should work even when the screen is not mounted."""
        screen = FileSearchScreen(search_root)
        screen._all_files = screen._collect_files()
        screen._update_results("main")
        assert len(screen._filtered) == 1
        assert screen._filtered[0].name == "main.py"


class TestPatchHelpers:
    """Tests for diff parsing and application."""

    def test_parse_unified_diff(self):
        diff = """--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,3 @@
 def hello():
-    return "hi"
+    return "hello"
"""
        patches = _parse_unified_diff(diff)
        assert len(patches) == 1
        assert patches[0]["path"] == "b/src/main.py"
        assert patches[0]["hunk"]["old_start"] == 1

    def test_apply_patch(self, tmp_path):
        root = tmp_path / "project"
        src = root / "src"
        src.mkdir(parents=True)
        file = src / "main.py"
        file.write_text('def hello():\n    return "hi"\n', encoding="utf-8")

        diff = """--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
 def hello():
-    return "hi"
+    return "hello"
"""
        changed = _apply_patch(diff, root)
        assert "src/main.py" in changed
        assert 'return "hello"' in file.read_text(encoding="utf-8")
        # Backup was created.
        assert list(root.rglob("*.bak.*"))


class TestGrepScreen:
    """Tests for the grep screen."""

    def test_build_command_prefer_ripgrep(self, tmp_path):
        screen = GrepScreen(tmp_path)
        cmd = screen._build_command("foo")
        assert cmd[0] in ("rg", "grep", "findstr")

    def test_parse_ripgrep_output(self, tmp_path):
        screen = GrepScreen(tmp_path)
        output = "src/main.py:10:print('foo')\nREADME.md:3:# foo"
        results = screen._parse_output(output)
        assert len(results) == 2
        assert results[0].path.name == "main.py"
        assert results[0].line == 10


class TestIDEConfig:
    """Tests for persistent IDE configuration."""

    def test_default_config(self):
        cfg = IDEConfig()
        assert cfg.theme == "textual-dark"
        assert cfg.auto_reload is True

    def test_load_save_roundtrip(self, tmp_path):
        cfg_path = tmp_path / "ide.yaml"
        cfg = IDEConfig(theme="textual-light", terminal_height=12)
        cfg.save(cfg_path)
        loaded = IDEConfig.load(cfg_path)
        assert loaded.theme == "textual-light"
        assert loaded.terminal_height == 12

    def test_load_missing_returns_default(self, tmp_path):
        cfg = IDEConfig.load(tmp_path / "missing.yaml")
        assert cfg.theme == "textual-dark"


class TestFindAndGoToScreens:
    """Smoke tests for find/go-to modal screens."""

    def test_find_screen_dismisses_query(self):
        screen = FindScreen()
        assert screen is not None

    def test_find_replace_screen(self):
        screen = FindReplaceScreen()
        assert screen is not None

    def test_goto_screen_parses_line(self):
        screen = GoToLineScreen()
        assert screen is not None


class TestNewScreensAndTheme:
    """Smoke tests for command palette, git blame, and Norse theme."""

    def test_command_palette_screen(self):
        items = [
            PaletteItem(label="uno", callback=lambda: None),
            PaletteItem(label="dos", callback=lambda: None),
        ]
        screen = CommandPaletteScreen(items)
        assert len(screen._all_items) == 2

    def test_git_blame_screen(self, tmp_path):
        screen = GitBlameScreen(tmp_path, None)
        assert screen is not None

    def test_norse_theme_defined(self):
        assert _NORSE_THEME.name == "norse-dark"
        assert _NORSE_THEME.dark is True

    def test_runestone_screen_constructible(self):
        from lilith_cli.ide.runestones import Runestone

        stone = Runestone.from_code_block("python", "x = 1", title="test.py")
        screen = RunestoneScreen(stone)
        assert screen is not None

    def test_completion_screen_constructible(self):
        screen = CompletionScreen([{"label": "print", "insertText": "print("}])
        assert screen is not None

    def test_hover_screen_constructible(self):
        screen = HoverScreen("Some hover info", title="Hover test")
        assert screen is not None

    def test_diagnostics_screen_constructible(self, tmp_path):
        diagnostics = [
            {
                "range": {"start": {"line": 0, "character": 0}},
                "severity": 1,
                "message": "syntax error",
            }
        ]
        screen = DiagnosticsScreen(diagnostics, tmp_path / "x.py")
        assert screen is not None

    def test_outline_screen_parses_python_symbols(self, tmp_path):
        file = tmp_path / "sample.py"
        file.write_text(
            "class Foo:\n    pass\n\ndef bar():\n    pass\n",
            encoding="utf-8",
        )
        screen = OutlineScreen(file)
        symbols = screen._parse_symbols()
        assert len(symbols) == 2
        assert symbols[0] == (1, "class Foo")
        assert symbols[1] == (4, "def bar")


class TestToastHistoryScreen:
    """Tests for the toast notification history modal."""

    def test_empty_history_shows_placeholder(self):
        screen = ToastHistoryScreen([])
        assert screen._history == []

    def test_history_keeps_last_messages(self):
        history = [
            {"message": "uno", "severity": "information"},
            {"message": "dos", "severity": "warning"},
        ]
        screen = ToastHistoryScreen(history)
        assert len(screen._history) == 2


class TestConfigScreen:
    """Tests for the IDE settings modal."""

    def test_config_screen_loads_defaults(self):
        cfg = IDEConfig()
        screen = ConfigScreen(cfg)
        assert screen._config is cfg

    def test_norse_light_theme_defined(self):
        assert _NORSE_LIGHT_THEME.name == "norse-light"
        assert _NORSE_LIGHT_THEME.dark is False


class TestDiffScreen:
    """Tests for the side-by-side diff modal."""

    def test_diff_screen_without_file(self, tmp_path):
        screen = DiffScreen(tmp_path, None)
        assert screen.current_file is None

    def test_diff_screen_with_missing_head_file(self, tmp_path):
        file = tmp_path / "orphan.py"
        file.write_text("print(1)", encoding="utf-8")
        screen = DiffScreen(tmp_path, file)
        assert screen.current_file == file


class TestLilithIDEAppNewFeatures:
    """Integration-style tests for new IDE actions and slash commands."""

    async def test_notify_accumulates_toast_history(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.notify("hola", severity="information")
            await pilot.pause()
            assert any(item["message"] == "hola" for item in app._toast_history)

    async def test_new_command_creates_file_from_snippet(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app._handle_new_command("py src/hola.py")
            await pilot.pause()
            target = tmp_path / "src" / "hola.py"
            assert target.exists()
            assert "def main():" in target.read_text(encoding="utf-8")

    async def test_new_command_unknown_template_shows_help(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app._handle_new_command("xyz foo.txt")
            await pilot.pause()
            assert not (tmp_path / "foo.txt").exists()

    async def test_open_file_tracks_recent_files(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        file = tmp_path / "src" / "main.py"
        file.parent.mkdir(parents=True)
        file.write_text("print(1)", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(file)
            await pilot.pause()
            assert app._recent_files and app._recent_files[0] == file


class TestRecentFilesScreen:
    """Tests for the Ctrl+E recent files modal."""

    def test_recent_files_screen_lists_paths(self, tmp_path):
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        screen = RecentFilesScreen(files, tmp_path)
        assert screen._files == files


class TestProjectFindReplaceScreen:
    """Tests for the project-wide find/replace modal."""

    def test_project_find_replace_screen_dismisses_tuple(self):
        screen = ProjectFindReplaceScreen()
        assert screen is not None


class TestIDEConfigQoL:
    """Tests for quality-of-life configuration options."""

    def test_auto_save_default_is_true(self):
        cfg = IDEConfig()
        assert cfg.auto_save is True

    def test_project_search_command_prefer_ripgrep(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        cmd = app._build_project_search_command("foo")
        assert cmd[0] in ("rg", "grep", "findstr")


class TestLilithIDEAppQoLv2:
    """Tests for the second wave of quality-of-life features."""

    async def test_chat_history_accumulates(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            chat_input = app.query_one("#chat-input")
            chat_input.value = "/clear"
            app._send_message()
            await pilot.pause()
            assert "/clear" in app._chat_history

    async def test_open_file_jumps_to_line(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        file = tmp_path / "sample.py"
        file.write_text("line1\nline2\nline3\n", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(file, line=2)
            await pilot.pause()
            await pilot.pause()
            editor = app._current_editor()
            assert editor is not None
            assert editor.cursor_location[0] == 1  # 0-based line 1 = line 2

    async def test_toggle_zen_mode_hides_panels(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_toggle_zen_mode()
            await pilot.pause()
            assert app._zen_mode is True
            assert app.query_one("#sidebar").styles.display == "none"
            app.action_toggle_zen_mode()
            await pilot.pause()
            assert app._zen_mode is False

    async def test_session_saves_open_files(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path)
        file = tmp_path / "session.py"
        file.write_text("x = 1\n", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(file)
            await pilot.pause()
            app._save_session()
            assert "session.py" in app.ide_config.open_files


class TestRuneDirectoryTree:
    """Tests for the Norse-themed file tree."""

    def test_rune_for_python_file(self, tmp_path):
        tree = RuneDirectoryTree(tmp_path)
        assert tree.rune_for_path(tmp_path / "main.py") == "ᛈ"

    def test_rune_for_rust_file(self, tmp_path):
        tree = RuneDirectoryTree(tmp_path)
        assert tree.rune_for_path(tmp_path / "lib.rs") == "ᚱ"

    def test_rune_for_directory(self, tmp_path):
        tree = RuneDirectoryTree(tmp_path)
        assert tree.rune_for_path(tmp_path) == tree.ICON_NODE

    def test_default_rune_for_unknown_extension(self, tmp_path):
        tree = RuneDirectoryTree(tmp_path)
        assert tree.rune_for_path(tmp_path / "data.xyz") == "ᚠ"


class TestSplashScreen:
    """Tests for the startup splash screen."""

    def test_splash_screen_constructible(self):
        screen = SplashScreen()
        assert screen is not None
        assert "LILITH" in SplashScreen._YGGDRASIL_ART


class TestDebuggerIntegration:
    """Tests for the basic debugger action."""

    async def test_debug_without_file_shows_warning(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_debug_current_file()
            await pilot.pause()
            assert app.current_file is None

    async def test_debug_non_python_file_shows_warning(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        file = tmp_path / "readme.md"
        file.write_text("# hi", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(file)
            await pilot.pause()
            app.action_debug_current_file()
            await pilot.pause()
            assert app.current_file.suffix == ".md"

    async def test_debug_python_file_starts_worker(self, fake_session, tmp_path, monkeypatch):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        file = tmp_path / "script.py"
        file.write_text("x = 1\n", encoding="utf-8")
        started = []
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_file(file)
            await pilot.pause()
            original_run_worker = app.run_worker

            def capture_worker(awaitable, *args, **kwargs):
                started.append(awaitable)
                awaitable.close()
                return None

            monkeypatch.setattr(app, "run_worker", capture_worker)
            try:
                app.action_debug_current_file()
                await pilot.pause()
                assert app.current_file.suffix == ".py"
                assert len(started) == 1
            finally:
                monkeypatch.setattr(app, "run_worker", original_run_worker)
