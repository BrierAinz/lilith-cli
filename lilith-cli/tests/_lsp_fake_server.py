"""Minimal LSP server used only by the test suite.

This is a *real* language server: it speaks JSON-RPC 2.0 over stdio with
the standard ``Content-Length`` framing. It is intentionally trivial — its
job is to give the :class:`LSPClient` something to talk to so the protocol
implementation can be exercised end-to-end, without depending on
pyright/pylsp being installed or any network.

Behaviour (kept deliberately simple so the tests stay readable):

* ``initialize`` → returns empty capabilities + a serverInfo dict.
* ``initialized`` → ignored (no-op).
* ``shutdown`` → returns ``None``.
* ``exit`` → prints a sentinel on stderr and exits with code 0.
* ``textDocument/didOpen`` / ``didChange`` / ``didClose`` → stored in memory.
* ``textDocument/completion`` → returns one static item per request (only
  if the document text contains the trigger word ``completion``, otherwise
  an empty list).
* ``textDocument/hover`` → returns a markdown-formatted string when the
  document contains the trigger word ``hover`` at the requested ``line``,
  otherwise the empty string.

Run: ``python -m tests._lsp_fake_server``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow ``python tests/_lsp_fake_server.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


ENCODING = "utf-8"


# ── Wire I/O ───────────────────────────────────────────────────────


def _read_message(stream) -> dict[str, Any] | None:
    """Read one JSON-RPC frame from *stream* (binary file-like)."""
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        decoded = line.decode("ascii", errors="replace")
        if decoded in ("\r\n", "\n", ""):
            if headers:
                break
            continue
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    if length <= 0:
        return None
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode(ENCODING, errors="replace"))
    except json.JSONDecodeError:
        return None


def _write_message(stream, message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode(ENCODING)
    header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
    stream.write(header + payload)
    stream.flush()


# ── Handlers ───────────────────────────────────────────────────────


def _documents() -> dict[str, dict[str, Any]]:
    return STATE.setdefault("documents", {})  # type: ignore[has-type]


def _publish_diagnostics(stream, uri: str) -> None:
    docs = _documents()
    text = docs.get(uri, {}).get("text", "")
    diags: list[dict[str, Any]] = []
    if "ERROR_SYNTAX" in text:
        diags.append(
            {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 1},
                },
                "severity": 1,
                "source": "fake-lsp",
                "message": "syntax error",
            }
        )
    if "WARN_UNUSED" in text:
        diags.append(
            {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 1},
                },
                "severity": 2,
                "source": "fake-lsp",
                "message": "unused name",
            }
        )
    _write_message(
        stream,
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": diags},
        },
    )


def _handle_request(stream, message: dict[str, Any]) -> bool:
    """Return ``False`` when the server should shut down."""
    method = message.get("method", "")
    req_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        _write_message(
            stream,
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "capabilities": {
                        "textDocumentSync": 1,
                        "completionProvider": {"triggerCharacters": ["."]},
                        "hoverProvider": True,
                    },
                    "serverInfo": {"name": "fake-lsp", "version": "test"},
                },
            },
        )
        return True
    if method == "shutdown":
        STATE["shutdown_requested"] = True
        _write_message(stream, {"jsonrpc": "2.0", "id": req_id, "result": None})
        return True
    if method == "exit":
        # ``exit`` is a JSON-RPC notification — no response expected. Just
        # write a marker on stderr and let the read loop bail on EOF.
        try:
            sys.stderr.buffer.write(b"EXIT_ACK\r\n")
            sys.stderr.buffer.flush()
        except Exception:
            pass
        return False

    # Notifications (no ``id``) below — no response expected.
    if method == "initialized":
        STATE["initialized"] = True
        return True

    # All remaining methods need textDocument params.
    if method == "textDocument/didOpen":
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        _documents()[uri] = {
            "languageId": td.get("languageId", ""),
            "version": td.get("version", 1),
            "text": td.get("text", ""),
        }
        STATE.setdefault("open_count", 0)
        STATE["open_count"] += 1
        _publish_diagnostics(stream, uri)
        return True
    if method == "textDocument/didChange":
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        changes = params.get("contentChanges") or []
        if changes and "text" in changes[-1]:
            _documents().setdefault(uri, {"text": ""})["text"] = changes[-1]["text"]
            _documents()[uri]["version"] = td.get(
                "version", _documents()[uri].get("version", 1)
            )
        STATE["change_count"] = STATE.get("change_count", 0) + 1
        _publish_diagnostics(stream, uri)
        return True
    if method == "textDocument/didClose":
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        _documents().pop(uri, None)
        STATE["close_count"] = STATE.get("close_count", 0) + 1
        # Per LSP: when a doc is closed the server must publish an empty
        # diagnostic list so clients know to clear their cached state.
        _write_message(
            stream,
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            },
        )
        return True

    if method == "textDocument/completion":
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        text = _documents().get(uri, {}).get("text", "")
        items: list[dict[str, Any]] = []
        if "completion" in text.lower():
            items.append(
                {
                    "label": "fake_completion",
                    "kind": 1,
                    "detail": "from fake LSP",
                    "insertText": "fake_completion",
                }
            )
        _write_message(
            stream, {"jsonrpc": "2.0", "id": req_id, "result": items}
        )
        return True

    if method == "textDocument/hover":
        td = params.get("textDocument", {})
        uri = td.get("uri", "")
        line = (params.get("position") or {}).get("line", 0)
        text = _documents().get(uri, {}).get("text", "")
        result: dict[str, Any] | None = None
        if "hover" in text.lower():
            result = {
                "contents": {
                    "kind": "markdown",
                    "value": f"# fake hover\nline={line}",
                }
            }
        _write_message(
            stream,
            {"jsonrpc": "2.0", "id": req_id, "result": result or {}},
        )
        return True

    # Method we don't implement: JSON-RPC method-not-found error.
    _write_message(
        stream,
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        },
    )
    return True


# ── Entry point ────────────────────────────────────────────────────

STATE: dict[str, Any] = {}


def main() -> int:
    """Read messages until EOF or ``exit``. Returns the process exit code."""
    # Make stdio binary so we don't have to think about CRLF translation
    # of the incoming/outgoing pipes on Windows.
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        try:
            message = _read_message(stdin)
        except (KeyboardInterrupt, SystemExit):
            return 0
        if message is None:
            return 0
        if not _handle_request(stdout, message):
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
