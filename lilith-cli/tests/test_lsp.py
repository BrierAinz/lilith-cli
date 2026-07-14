"""Tests for the Lilith IDE LSP infrastructure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.widgets import Static

from lilith_cli.ide.app import LilithIDEApp
from lilith_cli.ide.lsp.client import LSPClient
from lilith_cli.ide.lsp.languages import detect_language_server, language_server_command
from lilith_cli.ide.lsp.manager import LSPManager


class TestLanguageServerCommands:
    """Unit tests for language-server command resolution."""

    def test_python_command_present(self):
        cmd = language_server_command("python")
        assert cmd is not None
        # Either pyright-langserver (``pyright-langserver --stdio``) or the
        # pylsp fallback (``python -m pylsp``) must resolve.
        joined = " ".join(cmd)
        assert "pyright-langserver" in joined or "pylsp" in joined

    def test_unknown_language_returns_none(self):
        assert language_server_command("fortran") is None

    def test_detect_language_server_for_python(self, tmp_path):
        path = tmp_path / "main.py"
        cmd = detect_language_server(path)
        assert cmd is not None
        joined = " ".join(cmd)
        assert "pyright-langserver" in joined or "pylsp" in joined

    def test_detect_language_server_unknown_extension(self, tmp_path):
        path = tmp_path / "data.xyz"
        assert detect_language_server(path) is None


class TestLSPClientConstruction:
    """Unit tests for LSP client setup."""

    def test_client_stores_command_and_root(self, tmp_path):
        client = LSPClient(["python", "-m", "pylsp"], tmp_path)
        assert client.command == ["python", "-m", "pylsp"]
        assert client.root == tmp_path.resolve()

    def test_client_not_started_without_call(self, tmp_path):
        client = LSPClient(["true"], tmp_path)
        assert client._proc is None

    @pytest.mark.asyncio
    async def test_client_start_with_invalid_command(self, tmp_path):
        client = LSPClient(["this-binary-does-not-exist-12345"], tmp_path)
        result = await client.start()
        assert result is False
        await client.stop()


class TestLSPManager:
    """Unit tests for the LSP manager."""

    def test_manager_starts_empty(self, tmp_path):
        mgr = LSPManager(tmp_path)
        assert mgr.status() == {}

    @pytest.mark.asyncio
    async def test_get_client_returns_none_for_unknown_language(self, tmp_path):
        mgr = LSPManager(tmp_path)
        client = await mgr.get_client("klingon")
        assert client is None

    @pytest.mark.asyncio
    async def test_stop_all_is_safe_when_empty(self, tmp_path):
        mgr = LSPManager(tmp_path)
        await mgr.stop_all()  # should not raise


class TestLSPClientDiagnostics:
    """Unit tests for LSP diagnostic storage."""

    def test_get_diagnostics_empty(self, tmp_path):
        client = LSPClient(["true"], tmp_path)
        assert client.get_diagnostics("file:///x.py") == []

    def test_handle_publish_diagnostics(self, tmp_path):
        client = LSPClient(["true"], tmp_path)
        message = {
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///x.py",
                "diagnostics": [
                    {"range": {"start": {"line": 0, "character": 0}}, "message": "error"}
                ],
            },
        }
        client._handle_message(message)
        assert len(client.get_diagnostics("file:///x.py")) == 1



class TestLSPManagerDiagnosticsForwarding:
    """Tests that LSPManager routes server diagnostics to the UI."""

    def test_manager_forwards_diagnostics_to_callback(self, tmp_path):
        received = []

        def callback(uri, diagnostics):
            received.append((uri, diagnostics))

        mgr = LSPManager(tmp_path)
        mgr.on_diagnostics = callback
        mgr._on_client_diagnostics(
            "file:///x.py",
            [{"severity": 1, "message": "syntax error"}],
        )
        assert len(received) == 1
        assert received[0][0] == "file:///x.py"
        assert len(received[0][1]) == 1


class TestEditorMixinLSPIntegration:
    """Integration tests for LSP callbacks wired into EditorMixin."""

    def _mock_lsp_manager(self, app):
        """Replace the app's LSPManager with a stub to avoid real servers."""
        app.lsp_manager = MagicMock()
        app.lsp_manager.did_open = AsyncMock()
        app.lsp_manager.did_change = AsyncMock()
        app.lsp_manager.did_save = AsyncMock()
        app.lsp_manager.completion = AsyncMock(return_value=[])
        app.lsp_manager.hover = AsyncMock(return_value="")
        app.lsp_manager.definition = AsyncMock(return_value=[])
        app.lsp_manager.diagnostics_for = MagicMock(return_value=[])

    @pytest.mark.asyncio
    async def test_diagnostics_callback_updates_info_bar(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        file = tmp_path / "main.py"
        file.write_text("x = 1\n", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            self._mock_lsp_manager(app)
            app._open_file(file)
            await pilot.pause()
            mgr = LSPManager(tmp_path)
            mgr.on_diagnostics = app._on_lsp_diagnostics
            mgr._on_client_diagnostics(
                file.as_uri(),
                [
                    {"severity": 1, "message": "syntax error"},
                    {"severity": 2, "message": "unused import"},
                ],
            )
            await pilot.pause()
            info = app.query_one("#editor-info", Static)
            text = str(info.render())
            assert "1 errores" in text
            assert "1 warnings" in text

    @pytest.mark.asyncio
    async def test_completion_inserts_text_replacing_prefix(self, fake_session, tmp_path):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        file = tmp_path / "main.py"
        file.write_text("pri\n", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            self._mock_lsp_manager(app)
            app._open_file(file)
            await pilot.pause()
            editor = app._current_editor()
            editor.cursor_location = (0, 3)
            app._completion_request_position = (0, 3)
            app._on_completion_selected("print(")
            await pilot.pause()
            assert editor.text == "print(\n"
            assert editor.cursor_location == (0, 6)

    @pytest.mark.asyncio
    async def test_definition_opens_target_file_and_centers_cursor(
        self, fake_session, tmp_path
    ):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        source = tmp_path / "main.py"
        source.write_text("x = 1\n", encoding="utf-8")
        target = tmp_path / "lib.py"
        target.write_text("def foo():\n    pass\n", encoding="utf-8")
        async with app.run_test(size=(120, 40)) as pilot:
            self._mock_lsp_manager(app)
            app._open_file(source)
            await pilot.pause()
            app.lsp_manager.definition = AsyncMock(
                return_value=[
                    {
                        "uri": target.as_uri(),
                        "range": {"start": {"line": 0, "character": 4}},
                    }
                ]
            )
            await app._lsp_definition_worker(source, "python", 0, 0)
            await pilot.pause()
            await pilot.pause()
            assert app.current_file == target
            editor = app._current_editor()
            assert editor.cursor_location == (0, 4)
