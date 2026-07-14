"""Asynchronous LSP client for the Lilith IDE.

Speaks JSON-RPC 2.0 over stdio with the standard ``Content-Length`` framing
(``\\r\\n`` separators). The client is designed for:

* non-blocking stdout/stderr reads via :class:`asyncio.StreamReader`,
* serialized writes through a single :class:`asyncio.Lock` so concurrent
  notifications cannot interleave inside a single frame,
* graceful degradation when the language server process is missing or
  exits unexpectedly (the IDE keeps running).

The wire shape per JSON-RPC 2.0::

    Content-Length: <N>\r\n
    \r\n
    {"jsonrpc": "2.0", ...}

The same client is reused across multiple files: document lifecycle
(``didOpen``/``didChange``/``didClose``) is the responsibility of the
caller through the public methods below.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

LOG = logging.getLogger("lilith.lsp")

# Severity levels from the LSP spec, mirrored here for convenience in callers
# that want a quick human-readable label. Values mirror ``DiagnosticSeverity``.
_SEVERITY_LABELS: dict[int, str] = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}


class LSPError(Exception):
    """Raised when the language server returns a JSON-RPC error response."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.code = error.get("code", 0)
        self.message = error.get("message", "LSP error")
        super().__init__(f"LSP error {self.code}: {self.message}")


class LSPClient:
    """Async LSP client that talks to one language server over stdin/stdout.

    Parameters
    ----------
    command:
        Full argv passed to ``asyncio.create_subprocess_exec``.
    root:
        Project root used as ``rootUri`` and ``cwd`` for the subprocess.
    on_diagnostics:
        Optional callback ``(uri, diagnostics)`` invoked as the server
        publishes diagnostics. Wrapped with a try/except so a buggy UI
        cannot bring the client down.
    on_log_message:
        Optional callback ``(level, message)`` for ``window/logMessage``.
    on_show_message:
        Optional callback ``(level, message)`` for ``window/showMessage``.
    request_timeout:
        Timeout in seconds for any individual request. Notification sends
        and the read loop have no timeout.
    shutdown_timeout:
        Timeout in seconds for ``shutdown`` / ``exit`` exchange.
    """

    def __init__(
        self,
        command: list[str],
        root: Path,
        *,
        on_diagnostics: Any = None,
        on_log_message: Any = None,
        on_show_message: Any = None,
        request_timeout: float = 10.0,
        shutdown_timeout: float = 2.0,
    ) -> None:
        self.command = list(command)
        self.root = root.resolve()
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[Any] | None = None
        self._write_lock = asyncio.Lock()
        self._initialized = False
        self._shutdown_sent = False
        self._server_capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._on_diagnostics = on_diagnostics
        self._on_log_message = on_log_message
        self._on_show_message = on_show_message
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout

    # ── Lifecycle ──────────────────────────────────────────────────

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def server_capabilities(self) -> dict[str, Any]:
        return dict(self._server_capabilities)

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    async def start(self) -> bool:
        """Spawn the language server and complete the ``initialize`` handshake.

        Returns ``True`` on success. On any failure (binary missing, server
        exited, handshake timed out) the subprocess is cleaned up and the
        method returns ``False`` — callers should not assume a started
        client is a healthy one.
        """
        try:
            # ``CREATE_NO_WINDOW`` keeps a stray language server from
            # popping up a console window on Windows.
            kwargs: dict[str, Any] = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": str(self.root),
            }
            if os.name == "nt":
                CREATE_NO_WINDOW = 0x08000000  # type: ignore[attr-defined]
                kwargs["creationflags"] = CREATE_NO_WINDOW
            self._proc = await asyncio.create_subprocess_exec(
                *self.command, **kwargs
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            LOG.debug("LSP start: failed to spawn %s: %s", self.command, exc)
            self._proc = None
            return False

        self._reader_task = asyncio.create_task(
            self._read_loop(), name="lsp-read-loop"
        )
        # Drain stderr in the background to avoid pipe buffer exhaustion.
        self._stderr_task: asyncio.Task[Any] | None = asyncio.create_task(
            self._stderr_drain(), name="lsp-stderr-drain"
        )

        if not await self._initialize():
            await self.stop()
            return False
        return True

    async def stop(self) -> None:
        """Best-effort shutdown of the language server subprocess."""
        if self._proc is None:
            self._cancel_tasks()
            return
        proc = self._proc
        try:
            if self._initialized and not self._shutdown_sent:
                try:
                    await self._request(
                        "shutdown", None, timeout=self.shutdown_timeout
                    )
                    self._shutdown_sent = True
                except Exception:
                    self._shutdown_sent = True
                try:
                    self._send_notification("exit", None)
                except Exception:
                    pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
        except ProcessLookupError:
            pass
        finally:
            self._cancel_tasks()
            self._proc = None
            self._initialized = False
            self._fail_pending(LSPError({"code": -1, "message": "client stopped"}))

    def _cancel_tasks(self) -> None:
        tasks = [self._reader_task, getattr(self, "_stderr_task", None)]
        self._reader_task = None
        self._stderr_task = None
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        # Don't ``await`` here — ``stop()`` is called from both event loops
        # and worker tasks; consumers can fire-and-forget.

    def _fail_pending(self, exc: BaseException) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _req_id, future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _stderr_drain(self) -> None:
        """Read and log stderr so the pipe buffer never fills up."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = await proc.stderr.readline()
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace").rstrip()
                if text:
                    LOG.debug("LSP[stderr] %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _initialize(self) -> bool:
        """Send ``initialize`` then the ``initialized`` notification."""
        params = {
            "processId": os.getpid(),
            "rootPath": str(self.root),
            "rootUri": self.root.as_uri(),
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "didSave": True,
                        "willSave": False,
                        "dynamicRegistration": False,
                    },
                    "completion": {
                        "completionItem": {"snippetSupport": False},
                        "contextSupport": False,
                    },
                    "hover": {
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "publishDiagnostics": {"relatedInformation": True},
                },
                "workspace": {"workspaceFolders": False},
            },
            "trace": "off",
            "workspaceFolders": [
                {"uri": self.root.as_uri(), "name": self.root.name}
            ],
        }
        try:
            result = await self._request("initialize", params)
        except LSPError as exc:
            LOG.debug("LSP initialize failed: %s", exc)
            return False
        if result is None:
            return False
        if not isinstance(result, dict):
            self._initialized = True
            return True
        self._server_capabilities = dict(result.get("capabilities", {}))
        self._server_info = dict(result.get("serverInfo", {}))
        self._send_notification("initialized", {})
        self._initialized = True
        return True

    # ── Wire I/O ───────────────────────────────────────────────────

    def _send_notification(self, method: str, params: Any) -> None:
        """Encode and write a notification; returns synchronously."""
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        try:
            proc.stdin.write(header + payload)
        except Exception as exc:
            LOG.debug("LSP write failed: %s", exc)

    async def _request(
        self, method: str, params: Any, timeout: float | None = None
    ) -> Any:
        """Send a JSON-RPC request and await its response.

        Returns the ``result`` payload (any JSON value), or ``None`` on
        timeout/process death. JSON-RPC errors raise :class:`LSPError`.
        """
        proc = self._proc
        if proc is None or proc.stdin is None:
            return None
        req_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            message["params"] = params
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future

        async with self._write_lock:
            try:
                proc.stdin.write(header + payload)
                await proc.stdin.drain()
            except Exception as exc:
                self._pending.pop(req_id, None)
                if not future.done():
                    future.set_exception(LSPError(
                        {"code": -1, "message": f"write failed: {exc}"}
                    ))
                return await self._await_future(future, 0.0)

        return await self._await_future(future, timeout)

    async def _await_future(
        self,
        future: asyncio.Future[dict[str, Any]],
        timeout: float | None,
    ) -> Any:
        wait = self.request_timeout if timeout is None else timeout
        try:
            message = await asyncio.wait_for(future, timeout=wait)
        except asyncio.TimeoutError:
            return None
        except LSPError:
            raise
        return message

    async def _read_loop(self) -> None:
        """Decode JSON-RPC frames from the server's stdout."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        # Reference to the local StreamReader; if the subprocess dies the
        # reader returns empty bytes, ending the loop below.
        reader = proc.stdout
        while True:
            try:
                headers = await self._read_headers(reader)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Read failed — assume pipe is dead and bail.
                return
            if headers is None:
                return
            length = headers.get("content-length")
            if not length:
                continue
            try:
                content_length = int(length)
            except ValueError:
                continue
            if content_length <= 0:
                continue
            try:
                data = await reader.readexactly(content_length)
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            try:
                message = json.loads(data.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            self._handle_message(message)

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str] | None:
        """Read ``Header: value\\r\\n`` lines until a blank line. ``None`` on EOF."""
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                return None
            decoded = line.decode("ascii", errors="replace")
            if decoded in ("\r\n", "\n", ""):
                if headers:
                    return headers
                continue
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    # ── Message dispatch ────────────────────────────────────────────

    def _handle_message(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        # Response to one of our requests.
        if "id" in message and message["id"] is not None:
            req_id = message["id"]
            future = self._pending.pop(req_id, None)
            if future is None or future.done():
                return
            if "error" in message and message["error"]:
                future.set_exception(LSPError(message["error"]))
            else:
                future.set_result(message.get("result"))
            return

        # Server-initiated notification.
        method = message.get("method", "")
        params = message.get("params", {})
        if method == "textDocument/publishDiagnostics":
            self._handle_publish_diagnostics(params)
        elif method == "window/logMessage":
            self._handle_window_message("log", params)
        elif method == "window/showMessage":
            self._handle_window_message("show", params)

    def _handle_publish_diagnostics(self, params: Any) -> None:
        if not isinstance(params, dict):
            return
        uri = str(params.get("uri") or "")
        diags = list(params.get("diagnostics") or [])
        if not uri:
            return
        self._diagnostics[uri] = diags
        callback = self._on_diagnostics
        if callable(callback):
            try:
                callback(uri, diags)
            except Exception:
                LOG.exception("LSP on_diagnostics callback raised")

    def _handle_window_message(self, kind: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        level = int(params.get("type") or 0)
        try:
            message_obj = params.get("message", "")
            message_text = (
                message_obj
                if isinstance(message_obj, str)
                else json.dumps(message_obj, ensure_ascii=False)
            )
        except Exception:
            message_text = str(message_obj)
        callback = self._on_log_message if kind == "log" else self._on_show_message
        if callable(callback):
            try:
                callback(level, message_text)
            except Exception:
                LOG.exception("LSP %s callback raised", kind)
        else:
            label = _SEVERITY_LABELS.get(level, "log")
            LOG.info("LSP server [%s] %s", label, message_text)

    # ── Diagnostics accessors ──────────────────────────────────────

    def get_diagnostics(self, uri: str) -> list[dict[str, Any]]:
        return list(self._diagnostics.get(uri, []))

    def diagnostics_summary(self, uri: str) -> dict[str, int]:
        """Return ``{errors, warnings, info, hints}`` counts for *uri*."""
        summary = {"errors": 0, "warnings": 0, "info": 0, "hints": 0}
        for diag in self._diagnostics.get(uri, []):
            severity = int(diag.get("severity") or 1)
            if severity == 1:
                summary["errors"] += 1
            elif severity == 2:
                summary["warnings"] += 1
            elif severity == 3:
                summary["info"] += 1
            elif severity == 4:
                summary["hints"] += 1
        return summary

    # ── Public LSP operations ──────────────────────────────────────

    async def did_open(
        self, uri: str, language_id: str, text: str, version: int = 1
    ) -> None:
        """Notify the server a document was opened."""
        self._send_notification(
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
        """Push a full-document change (``textDocument/didChange``).

        We send a single full-replacement ``contentChanges`` entry, which is
        sufficient for the language servers we target and avoids having to
        map between the editor's TextArea coordinates and incremental diffs.
        """
        self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    async def did_close(self, uri: str) -> None:
        """Notify the server a document was closed."""
        self._send_notification(
            "textDocument/didClose", {"textDocument": {"uri": uri}}
        )

    async def did_save(self, uri: str, text: str | None = None) -> None:
        """Notify the server a document was saved.

        Some servers expect ``text`` to mirror disk content; we forward it
        when the caller has it on hand.
        """
        params: dict[str, Any] = {"textDocument": {"uri": uri}}
        if text is not None:
            params["text"] = text
        self._send_notification("textDocument/didSave", params)

    async def completion(
        self, uri: str, line: int, character: int
    ) -> list[dict[str, Any]]:
        """Return ``textDocument/completion`` items at *line*/*character*."""
        result = await self._request(
            "textDocument/completion",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            return list(result["items"])
        return []

    async def hover(self, uri: str, line: int, character: int) -> str:
        """Return hover contents at *line*/*character* as plain text."""
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
            return str(contents.get("value", ""))
        if isinstance(contents, list):
            return "\n".join(str(c) for c in contents)
        return str(contents)

    async def definition(
        self, uri: str, line: int, character: int
    ) -> list[dict[str, Any]]:
        """Return ``textDocument/definition`` locations."""
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

    async def shutdown(self) -> None:
        """Send ``shutdown`` + ``exit`` and tear down the subprocess."""
        if self._initialized and not self._shutdown_sent:
            try:
                await self._request(
                    "shutdown", None, timeout=self.shutdown_timeout
                )
            except Exception:
                pass
            self._shutdown_sent = True
        self._send_notification("exit", None)
