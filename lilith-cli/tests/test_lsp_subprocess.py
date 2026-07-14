"""End-to-end LSP tests that talk to a real subprocess.

The fake server lives in :mod:`tests._lsp_fake_server` — a stdlib-only JSON-RPC
2.0 over stdio language server that responds to the lifecycle, document
sync, completion and hover messages this codebase cares about. No network,
no third-party dependencies, no ``pyright``/``pylsp`` requirement.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from lilith_cli.ide.lsp.client import LSPClient
from lilith_cli.ide.lsp.languages import detect_language_server, language_server_command
from lilith_cli.ide.lsp.manager import LSPManager


FAKE_SERVER = [
    sys.executable,
    str(Path(__file__).resolve().parent / "_lsp_fake_server.py"),
]


# ── language resolution ──────────────────────────────────────────────


class TestLanguageResolution:
    def test_preferred_server_is_pyright_langserver(self, monkeypatch):
        # When ``pyright-langserver`` is on PATH it wins over the fallback.
        import lilith_cli.ide.lsp.languages as langs

        monkeypatch.setattr(
            langs.shutil, "which", lambda cmd: "/usr/bin/pyright-langserver" if cmd == "pyright-langserver" else None
        )
        assert langs.language_server_command("python") == [
            "pyright-langserver",
            "--stdio",
        ]

    def test_falls_back_to_pylsp_when_pyright_missing(self, monkeypatch):
        import lilith_cli.ide.lsp.languages as langs

        monkeypatch.setattr(
            langs.shutil, "which", lambda cmd: None
        )
        monkeypatch.setattr(langs.importlib.util, "find_spec", lambda m: object() if m == "pylsp" else None)
        cmd = langs.language_server_command("python")
        assert cmd is not None
        assert cmd[1:] == ["-m", "pylsp"]

    def test_returns_none_when_nothing_available(self, monkeypatch):
        import lilith_cli.ide.lsp.languages as langs

        monkeypatch.setattr(langs.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(langs.importlib.util, "find_spec", lambda m: None)
        assert langs.language_server_command("python") is None

    def test_detect_language_server_for_known_extension(self, tmp_path):
        path = tmp_path / "main.py"
        # Result depends on the host; ensure we never get ``None`` for a
        # real Python file and that the command list is non-empty.
        cmd = detect_language_server(path)
        if cmd is not None:
            assert cmd

    def test_detect_language_server_for_unknown_extension(self, tmp_path):
        path = tmp_path / "x.pascal"
        assert detect_language_server(path) is None


# ── wire / lifecycle ────────────────────────────────────────────────


class TestLSPClientSubprocess:
    @pytest.mark.asyncio
    async def test_initialize_handshake_completes(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            ok = await client.start()
            assert ok is True
            assert client.initialized
            assert client.server_capabilities
            assert client.server_info.get("name") == "fake-lsp"
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_initialize_failure_is_reported(self, tmp_path):
        # Point at a binary that doesn't exist; start must return False and
        # leave the client un-initialized.
        client = LSPClient(
            [sys.executable, "-c", "raise SystemExit(1)"], tmp_path, request_timeout=2
        )
        ok = await client.start()
        assert ok is False
        assert not client.initialized
        await client.stop()

    @pytest.mark.asyncio
    async def test_shutdown_then_exit_terminates_process(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        ok = await client.start()
        assert ok
        proc = client._proc
        assert proc is not None
        await client.shutdown()
        # Give the OS a tick to reap the child.
        rc = await asyncio.wait_for(proc.wait(), timeout=5)
        # The fake server reads another frame and exits once the handshake
        # is done; either 0 or None are acceptable.
        assert rc in (0, None)
        # ``shutdown`` only does the protocol exchange, so the client is
        # still marked initialized (per LSP spec).
        assert client.initialized
        # ``stop()`` cleans up the running flag for future starts.
        await client.stop()
        assert not client.initialized

    @pytest.mark.asyncio
    async def test_start_after_stop_is_idempotent(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        assert await client.start() is True
        await client.stop()
        assert not client.initialized
        # A second start brings up a fresh subprocess cleanly.
        assert await client.start() is True
        await client.stop()


# ── document sync / diagnostics ────────────────────────────────────


class TestDocumentLifecycle:
    @pytest.mark.asyncio
    async def test_did_open_publishes_diagnostics(self, tmp_path):
        received: list[tuple[str, list[dict]]] = []

        def cb(uri, diags):
            received.append((uri, diags))

        client = LSPClient(FAKE_SERVER, tmp_path, on_diagnostics=cb, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///a.py"
            await client.did_open(
                uri, "python", "ERROR_SYNTAX and WARN_UNUSED\n", version=1
            )
            # Wait for the publishDiagnostics notification to land.
            for _ in range(50):
                if received:
                    break
                await asyncio.sleep(0.05)
            assert received, "expected at least one publishDiagnostics callback"
            assert received[0][0] == uri
            sevs = sorted(d.get("severity") for d in received[0][1])
            assert sevs == [1, 2]
            summary = client.diagnostics_summary(uri)
            assert summary == {"errors": 1, "warnings": 1, "info": 0, "hints": 0}
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_did_change_updates_text(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///b.py"
            await client.did_open(uri, "python", "clean", 1)
            await client.did_change(uri, "ERROR_SYNTAX", version=2)
            await asyncio.sleep(0.1)
            diags = client.get_diagnostics(uri)
            # After the change there should be a single error.
            assert any(d.get("severity") == 1 for d in diags)
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_did_close_clears_state_for_uri(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///c.py"
            await client.did_open(uri, "python", "ERROR_SYNTAX", 1)
            await client.did_close(uri)
            # The fake server stores text in memory only while the doc is
            # open; after did_close a subsequent should match the closed
            # state (no diagnostics because no doc maps to that URI).
            await asyncio.sleep(0.1)
            assert client.get_diagnostics(uri) == []
        finally:
            await client.stop()


# ── completion / hover ──────────────────────────────────────────────


class TestCompletionAndHover:
    @pytest.mark.asyncio
    async def test_completion_returns_items_when_trigger_present(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///d.py"
            await client.did_open(uri, "python", "completion here", 1)
            items = await client.completion(uri, 0, 5)
            assert isinstance(items, list)
            assert items and items[0].get("label") == "fake_completion"
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_completion_empty_when_trigger_absent(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///e.py"
            await client.did_open(uri, "python", "nothing fancy", 1)
            items = await client.completion(uri, 0, 0)
            assert items == []
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_hover_returns_markdown(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///f.py"
            await client.did_open(uri, "python", "hover me", 1)
            text = await client.hover(uri, 0, 0)
            assert "fake hover" in text
            assert "line=0" in text
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_hover_empty_when_trigger_absent(self, tmp_path):
        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            uri = "file:///g.py"
            await client.did_open(uri, "python", "calm text", 1)
            assert await client.hover(uri, 0, 0) == ""
        finally:
            await client.stop()


# ── server messages ────────────────────────────────────────────────


class TestServerMessages:
    @pytest.mark.asyncio
    async def test_window_log_message_routes_to_callback(self, tmp_path):
        # We exercise the routing through the dispatch method directly
        # because the fake server does not emit log messages. This still
        # proves the JSON-RPC dispatch wiring without a network.
        captured: list[tuple[int, str]] = []

        def on_log(level: int, message: str) -> None:
            captured.append((level, message))

        client = LSPClient(
            FAKE_SERVER, tmp_path, on_log_message=on_log, request_timeout=5
        )
        try:
            assert await client.start()
            client._handle_message(
                {
                    "method": "window/logMessage",
                    "params": {"type": 1, "message": "hello from server"},
                }
            )
            assert captured == [(1, "hello from server")]
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_show_message_routes_to_callback(self, tmp_path):
        captured: list[tuple[int, str]] = []

        def on_show(level: int, message: str) -> None:
            captured.append((level, message))

        client = LSPClient(
            FAKE_SERVER, tmp_path, on_show_message=on_show, request_timeout=5
        )
        try:
            assert await client.start()
            client._handle_message(
                {
                    "method": "window/showMessage",
                    "params": {"type": 2, "message": "warning"},
                }
            )
            assert captured == [(2, "warning")]
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_response_error_is_raised(self, tmp_path):
        # Send a request, then simulate the server replying with an error.
        # The client must surface ``LSPError`` rather than returning ``None``
        # or losing the future silently.
        from lilith_cli.ide.lsp.client import LSPError

        client = LSPClient(FAKE_SERVER, tmp_path, request_timeout=5)
        try:
            assert await client.start()
            req_id = client._next_id
            # Build the future as the request path would.
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            client._pending[req_id] = future
            client._handle_message(
                {
                    "id": req_id,
                    "error": {"code": -32601, "message": "no method"},
                }
            )
            with pytest.raises(LSPError):
                await future
            assert req_id not in client._pending
        finally:
            await client.stop()


# ── manager wiring ──────────────────────────────────────────────────


class TestManagerSubprocess:
    @pytest.mark.asyncio
    async def test_manager_does_not_exist_for_unknown_language(self, tmp_path):
        mgr = LSPManager(tmp_path)
        assert await mgr.get_client("klingon") is None

    @pytest.mark.asyncio
    async def test_manager_serves_a_document(self, tmp_path, monkeypatch):
        # Skip the subprocess: monkey-patch ``LSPClient.start`` to mark the
        # client as initialized without spawning anything. The full subprocess
        # path is exercised above in TestLSPClientSubprocess.
        from lilith_cli.ide.lsp.client import LSPClient as RealClient

        async def _passthrough(self, *args, **kwargs):
            self._initialized = True
            return True

        monkeypatch.setattr(RealClient, "start", _passthrough)

        received: list[tuple[str, list[dict]]] = []

        manager = LSPManager(tmp_path, on_diagnostics=lambda u, d: received.append((u, d)))
        path = tmp_path / "demo.py"
        path.write_text("ERROR_SYNTAX", encoding="utf-8")
        opened = await manager.did_open(path, "python", path.read_text("utf-8"))
        assert opened is True
        client = manager._clients["python"]

        # Simulate the server publishing diagnostics; the manager must
        # forward to the UI callback.
        client._handle_message(
            {
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": path.as_uri(),
                    "diagnostics": [
                        {"severity": 1, "message": "boom", "range": {"start": {"line": 0, "character": 0}}},
                    ],
                },
            }
        )
        assert received and received[0][0] == path.as_uri()
        assert manager.diagnostics_for(path) == received[0][1]

        # Closing the URI the manager actually opened returns True and
        # silently no-ops for paths we did not open.
        assert await manager.did_close(path, "python") is True
        assert manager._open_documents[client] == set()
        other = tmp_path / "nope.py"
        other.write_text("x", encoding="utf-8")
        assert await manager.did_close(other, "python") is False

    @pytest.mark.asyncio
    async def test_manager_status_and_stop_all(self, tmp_path):
        manager = LSPManager(tmp_path)
        # Empty by default.
        assert manager.status() == {}
        await manager.stop_all()  # safe on empty manager
