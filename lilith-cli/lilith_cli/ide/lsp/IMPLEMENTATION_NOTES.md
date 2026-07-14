# Lilith IDE — Real LSP Client

This directory ships the **real** Language Server Protocol client the IDE
talks to. It speaks JSON-RPC 2.0 over stdio against external language
servers (currently ``pyright-langserver`` preferred, ``pylsp`` fallback).

Before this implementation, `client.py` was structurally present but
lightweight; the goal of the rewrite was to land a production-grade
client that:

* uses correct `Content-Length` framing with serialized writes so frames
  cannot interleave;
* completes the full LSP lifecycle (`initialize` / `initialized` /
  `shutdown` / `exit`) without leaking tasks or pending requests;
* wires the existing editor diagnostics callback to the actual
  `textDocument/publishDiagnostics` notification;
* resolves language servers with a clear preferred → fallback policy;
* degrades gracefully when no server is installed (no crash, all
  editor features still work);
* is exercised end-to-end by a stdlib-only fake LSP server in a real
  subprocess, with no network and no third-party dependencies.

The package now exposes a small public surface
(`LSPClient`, `LSPManager`, `LSPError`, `language_server_command`,
`detect_language_server`, `preferred_server_for`).

---

## Architecture

```
 ┌─────────────────────────────────────────────────────────────┐
 │  EditorMixin (views/editor.py)                              │
 │      │                                                      │
 │      │  did_open / did_change / did_save / did_close        │
 │      │  completion / hover / definition / diagnostics        │
 │      ▼                                                      │
 │  LSPManager (manager.py)                                    │
 │      │  • one LSPClient per language, lazy startup          │
 │      │  • forwards diagnostics to EditorMixin's callback    │
 │      │  • returns empty defaults when no server is found    │
 │      ▼                                                      │
 │  LSPClient (client.py)                                      │
 │      │  • asyncio + Content-Length framing                  │
 │      │  • JSON-RPC 2.0 over stdin/stdout                    │
 │      │  • stderr drained in a side task to avoid pipe stall │
 │      ▼                                                      │
 │  subprocess: pyright-langserver --stdio  (or python -m pylsp)│
 └─────────────────────────────────────────────────────────────┘
```

### Lifecycle

`LSPClient.start()`:

1. Spawn the subprocess (`stdin=PIPE`, `stdout=PIPE`, `stderr=PIPE`,
   `cwd=root`, plus `CREATE_NO_WINDOW` on Windows so a console window
   does not pop up).
2. Spawn the read loop task that decodes `Content-Length`-framed JSON
   messages from stdout.
3. Spawn a stderr drain task so the server's error stream never fills
   its kernel buffer and blocks.
4. Send `initialize` with full client capabilities; on a successful
   response, store `server_capabilities` and `server_info`, then send
   the `initialized` notification.
5. Return `True` only after the handshake completes.

`LSPClient.stop()`:

1. If the handshake completed, run `shutdown` (waiting up to
   `shutdown_timeout` seconds) and send `exit`.
2. `terminate()` the process, escalate to `kill()` after a short grace
   period.
3. Cancel the read loop and stderr tasks.
4. Fail any pending requests with `LSPError` so awaiting code does not
   hang.
5. Clear `initialized`, capability, and process state for future reuse.

### Wire layer

* Frames are written under an `asyncio.Lock` so a notification and a
  concurrent request cannot interleave inside a single frame.
* Reads use `StreamReader.readline` for headers and `readexactly(N)` for
  the body, which avoids accidental framing drift.
* The header reader skips blank lines, tolerates CR/LF differences, and
  treats unexpected EOF on the pipe as an end-of-stream signal.
* All payload encoding is UTF-8 with `errors="replace"` for robustness
  against misbehaving servers.

### Document lifecycle

`LSPClient.did_open / did_change / did_close / did_save` send the
matching `textDocument/*` notifications. The manager keeps a
`_open_documents` set per client so:

* `did_close` is only sent for URIs the manager actually opened (silent
  no-op otherwise).
* `did_change` is best-effort and never raises to the caller.

### Diagnostics pipeline

`textDocument/publishDiagnostics` notifications are:

1. Stored on the client (`get_diagnostics(uri)`,
   `diagnostics_summary(uri)`).
2. Forwarded to `LSPClient.on_diagnostics`.
3. Re-forwarded by `LSPManager._on_client_diagnostics` to whatever the
   `LSPManager(on_diagnostics=...)` was wired with. In the live IDE this
   is `EditorMixin._on_lsp_diagnostics`, which already updates the
   editor info bar.

### Server messages

`window/logMessage` and `window/showMessage` are routed to
`on_log_message` / `on_show_message`. Missing callbacks fall back to
the `lilith.lsp` logger so the IDE never loses server output.

### Language detection (`languages.py`)

For each language two command lists are kept:

| Language    | Preferred                 | Fallback                |
|-------------|---------------------------|-------------------------|
| python      | `pyright-langserver --stdio` | `python -m pylsp`    |
| rust        | `rust-analyzer`           | -                       |
| typescript  | `typescript-language-server --stdio` | -            |
| javascript  | `typescript-language-server --stdio` | -            |
| go          | `gopls`                   | -                       |
| json        | `vscode-json-language-server --stdio` | -           |
| yaml        | `yaml-language-server --stdio` | -                 |

Resolution per language:

1. If the preferred binary resolves on `PATH` (`shutil.which`), use it.
2. Otherwise, for `python -m <module>`-style fallbacks, check whether
   `<module>` can be imported in the current interpreter (works inside
   venvs where console scripts are not on `PATH`).
3. Otherwise return `None` — the IDE keeps running and surfaces nothing.

---

## Testing

The tests live in two files:

* `tests/test_lsp.py` — pre-existing unit tests for command resolution,
  client construction, manager behaviour, and editor diagnostics
  wiring. Kept untouched except for two assertions that needed updating
  after the `python -m pylsp` fallback returned an absolute path.
* `tests/test_lsp_subprocess.py` — **new** — exercises the real wire
  protocol against a fake LSP server that runs as a subprocess. 22
  tests cover:
  * Preferred vs. fallback resolution (`monkeypatch` on `shutil.which`).
  * Initialize / initialize-failure / shutdown-then-exit / idempotent
    restart.
  * `didOpen` → `publishDiagnostics` arrives with the right severities.
  * `didChange` updates the document and re-publishes.
  * `didClose` clears cached diagnostics (server publishes empty list).
  * `completion` returns items when triggered, empty otherwise.
  * `hover` returns markdown when triggered, empty otherwise.
  * `window/logMessage` and `window/showMessage` route to the callbacks.
  * JSON-RPC error responses surface as `LSPError` rather than being
    dropped.
  * Manager forwards diagnostics to its UI callback and tracks open
    documents for clean `didClose`.

The fake server is in `tests/_lsp_fake_server.py` and reads/writes
`stdin`/`stdout` in binary mode (no CRLF translation) so it works
identically on Windows and POSIX. It implements only the methods this
codebase uses; unknown methods return JSON-RPC method-not-found so the
client error path stays honest.

### Running the suite

```bash
# From D:\Proyectos\Yggdrasil\Asgard\lilith-stack
uv run --directory lilith-cli python -m pytest
```

Result: `1087 passed, 11 skipped` — 22 new tests, no regressions.

---

## Files touched

| Path                                                          | Change                                  |
|---------------------------------------------------------------|-----------------------------------------|
| `lilith_cli/ide/lsp/__init__.py`                              | Real package surface + module docstring |
| `lilith_cli/ide/lsp/client.py`                                | Real LSP client (rewritten)             |
| `lilith_cli/ide/lsp/manager.py`                               | `did_close`, graceful degrade, public hooks |
| `lilith_cli/ide/lsp/languages.py`                             | pyright-langserver preferred + pylsp fallback |
| `tests/test_lsp.py`                                            | Two assertions updated                  |
| `tests/test_lsp_subprocess.py`                                 | New — 22 tests with a fake server subprocess |
| `tests/_lsp_fake_server.py`                                    | New — stdlib-only LSP server fixture     |

Files explicitly **not** touched (in-progress elsewhere per task):
`repl.py`, `agent.py`, `providers.py`, `tool_progress.py`, `render.py`.
