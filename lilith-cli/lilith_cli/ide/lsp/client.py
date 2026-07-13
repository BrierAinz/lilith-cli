"""Minimal async LSP client for the Lilith IDE.

This client speaks JSON-RPC over stdio and exposes a small surface for
initialize, completion, hover and diagnostics. UI integration (popups,
squiggles, go-to-definition) is left to future widgets.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class LSPClient:
    """Async LSP client that talks to a language server over stdin/stdout."""

    def __init__(
        self,
        command: list[str],
        root: Path,
        *,
        on_diagnostics: Any = None,
    ) -> None:
        self.command = command
        self.root = root.resolve()
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[Any] | None = None
        self._initialized = False
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._on_diagnostics = on_diagnostics

    async def start(self) -> bool:
        """Start the language server subprocess."""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
        except Exception:
            return False
        self._reader_task = asyncio.create_task(self._read_loop())
        return await self._initialize()

    async def stop(self) -> None:
        """Shutdown the language server and clean up."""
        if self._proc is None:
            return
        try:
            if self._initialized:
                await self._request("shutdown")
                await self._notify("exit")
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except Exception:
            if self._proc.returncode is None:
                self._proc.kill()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._proc = None
        self._reader_task = None
        self._initialized = False

    async def _initialize(self) -> bool:
        """Send initialize and initialized notifications."""
        result = await self._request(
            "initialize",
            {
                "processId": None,
                "rootPath": str(self.root),
                "rootUri": self.root.as_uri(),
                "capabilities": {},
                "workspaceFolders": None,
            },
        )
        if result is None:
            return False
        await self._notify("initialized", {})
        self._initialized = True
        return True

    async def _request(self, method: str, params: Any = None) -> Any:
        """Send a JSON-RPC request and await the response."""
        if self._proc is None or self._proc.stdin is None:
            return None
        req_id = self._next_id
        self._next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params if params is not None else {},
        }
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        self._send_raw(message)
        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return None

    async def _notify(self, method: str, params: Any = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if self._proc is None or self._proc.stdin is None:
            return
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
        }
        self._send_raw(message)

    def _send_raw(self, message: dict[str, Any]) -> None:
        """Encode and send a JSON-RPC message with Content-Length header."""
        if self._proc is None or self._proc.stdin is None:
            return
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + payload)
        # StreamWriter.drain is a coroutine; fire-and-forget is acceptable here
        # because the reader loop handles back-pressure via the protocol.
        try:
            asyncio.ensure_future(self._proc.stdin.drain())
        except Exception:
            pass

    async def _read_loop(self) -> None:
        """Read JSON-RPC responses/notifications from the server stdout."""
        if self._proc is None or self._proc.stdout is None:
            return
        while True:
            try:
                headers: dict[str, str] = {}
                while True:
                    line = await self._proc.stdout.readline()
                    if not line:
                        return
                    line = line.decode("ascii", errors="replace").strip()
                    if not line:
                        break
                    if ":" in line:
                        key, value = line.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                data = await self._proc.stdout.read(length)
                if not data:
                    return
                message = json.loads(data.decode("utf-8", errors="replace"))
                self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    def _handle_message(self, message: dict[str, Any]) -> None:
        """Dispatch a parsed JSON-RPC message."""
        if "id" in message and message["id"] is not None:
            req_id = message["id"]
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                if "error" in message:
                    future.set_exception(LSPError(message["error"]))
                else:
                    future.set_result(message.get("result"))
            return

        # Handle notifications.
        method = message.get("method", "")
        params = message.get("params", {})
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diagnostics = params.get("diagnostics", [])
            self._diagnostics[uri] = list(diagnostics)
            if callable(self._on_diagnostics):
                try:
                    self._on_diagnostics(uri, self._diagnostics[uri])
                except Exception:
                    pass

    def get_diagnostics(self, uri: str) -> list[dict[str, Any]]:
        """Return the latest diagnostics for *uri*."""
        return list(self._diagnostics.get(uri, []))

    # ── Public LSP operations ─────────────────────────────────────────

    async def did_open(self, uri: str, language_id: str, text: str, version: int = 1) -> None:
        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )

    async def did_change(self, uri: str, text: str, version: int) -> None:
        await self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    async def completion(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Request completion items at the given position."""
        result = await self._request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "items" in result:
            return list(result["items"])
        return []

    async def hover(self, uri: str, line: int, character: int) -> str:
        """Request hover information at the given position."""
        result = await self._request(
            "textDocument/hover",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
        if not isinstance(result, dict):
            return ""
        contents = result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", "")
        if isinstance(contents, list):
            return "\n".join(str(c) for c in contents)
        return str(contents)

    async def definition(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Request definition locations at the given position."""
        result = await self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []


class LSPError(Exception):
    """Raised when the language server returns a JSON-RPC error."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.code = error.get("code", 0)
        self.message = error.get("message", "LSP error")
        super().__init__(f"LSP error {self.code}: {self.message}")
