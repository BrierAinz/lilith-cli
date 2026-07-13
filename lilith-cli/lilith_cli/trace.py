"""Live activity trace for Yggdrasil CLI v3.1.

Shows what the agent is doing *while it works* — an animated one-line status
whose label shifts through verbs ("Pensando…", "Razonando…", "Forjando…")
as the model streams reasoning, switches to "Ejecutando <tool>…" while a tool
executes, and drops a persistent line for each completed tool call.

Inspired by Neurosurfer's AgentTrace (github.com/NaumanHSA/neurosurfer).

The status line is animated with a carriage return (\\r) — the same trick
progress bars use. This updates a single line in place and behaves identically
in a terminal and in a notebook (VS Code / Lab / classic), with no gaps.

When stdout is neither a TTY nor a notebook (a pipe, CI, the test suite) it
degrades to plain newline-terminated lines — no spinner / carriage-return
noise in captured output.

This is a side-channel: it observes the event stream but never consumes it.
Both :meth:`AgentSession.process_message` and
:meth:`AgentSession.process_message_stream` can feed events through here.
"""

from __future__ import annotations

import itertools
import sys
import time
from typing import Any

# ── Constants ───────────────────────────────────────────────────────

_THINK_VERBS = [
    "Pensando",
    "Razonando",
    "Forjando",
    "Tejiendo",
    "Sopesando",
    "Cavilar",
    "Percolando",
    "Sintetizando",
    "Computando",
    "Rumiando",
    "Tramando",
    "Brewing",
]
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_MAX_ARGS_LEN = 80
_MAX_RESULT_LEN = 120
_VERB_INTERVAL = 1.1  # seconds between verb changes while thinking

# ANSI styles (honoured by terminals and by Jupyter/VS Code output)
_DIM, _CYAN, _YELLOW, _RED, _RESET = "\033[2m", "\033[36m", "\033[33m", "\033[31m", "\033[0m"


# ── Helpers ─────────────────────────────────────────────────────────


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _in_notebook() -> bool:
    try:
        from IPython import get_ipython  # noqa: PLC0415

        shell = get_ipython()
        return shell is not None and shell.__class__.__name__ == "ZMQInteractiveShell"
    except Exception:  # noqa: BLE001
        return False


# ── AgentTrace class ────────────────────────────────────────────────


class AgentTrace:
    """Animated (or plain) one-line activity trace, driven one event at a time.

    Usage:
        trace = AgentTrace()
        async for event in session.process_message_stream(text):
            trace.handle(event)
        trace.close()

    Events are plain dicts with at least a "type" key:
        - {"type": "thinking", "content": "..."}
        - {"type": "text", "content": "..."}
        - {"type": "tool_call", "name": "...", "arguments": {...}}
        - {"type": "tool_result", "name": "...", "content": "...", "is_error": bool}
        - {"type": "done", "content": "..."}
        - {"type": "error", "message": "..."}
    """

    def __init__(self) -> None:
        self._out = sys.stdout
        self._frames = itertools.cycle(_SPINNER_FRAMES)
        self._verbs = itertools.cycle(_THINK_VERBS)
        self._verb = next(self._verbs)
        self._verb_at = 0.0
        self._mode: str | None = None  # "think" | "tool" | None
        self._status_len = 0  # visible width of the live status line
        self._answer_open = False  # caller is mid-stream printing the answer
        try:
            self._interactive = bool(
                getattr(self._out, "isatty", lambda: False)()
            ) or _in_notebook()
        except Exception:  # noqa: BLE001
            self._interactive = False

    # ── public API ────────────────────────────────────────────────────

    def handle(self, event: dict[str, Any]) -> None:
        """Process a single event from the agent stream."""
        try:
            (self._handle_live if self._interactive else self._handle_plain)(event)
        except Exception:  # noqa: BLE001 — a trace must never break the run
            self._status_len = 0

    def close(self) -> None:
        """Finalise any active status and clean up."""
        if not self._interactive:
            return
        try:
            self._clear_status()
            if self._answer_open:
                self._out.write("\n")
                self._out.flush()
                self._answer_open = False
        except Exception:  # noqa: BLE001
            pass

    # ── animated (interactive) path ─────────────────────────────────

    def _handle_live(self, event: dict[str, Any]) -> None:
        event_type = event.get("type", "")

        if event_type == "thinking":
            # While the answer is streaming, never draw the spinner — a stray
            # reasoning token mid-answer must not overwrite the text the caller
            # is printing. The spinner resumes after the next tool call.
            if not self._answer_open:
                self._tick_thinking()

        elif event_type == "tool_call":
            name = event.get("name", "tool")
            args = event.get("arguments", {})
            self._commit(
                f"{_YELLOW}→ {name}({_short(repr(args), _MAX_ARGS_LEN)}){_RESET}"
            )
            self._mode = None

        elif event_type == "tool_result":
            name = event.get("name", "tool")
            result = event.get("content", "")
            is_error = event.get("is_error", False)
            head = _short(result, _MAX_RESULT_LEN)
            if is_error:
                self._commit(f"{_RED}  ✗ {name}: {head}{_RESET}")
            else:
                self._commit(f"{_DIM}  ↳ {head}{_RESET}")
            self._mode = None

        elif event_type == "text":
            # Answer is streaming — clear the status and let the caller render it.
            self._clear_status()
            self._answer_open = True
            self._mode = None

        elif event_type == "done":
            self._clear_status()
            self._mode = None

        elif event_type == "error":
            msg = event.get("message", "Unknown error")
            self._commit(f"{_RED}✗ {msg}{_RESET}")
            self._mode = None

    def _tick_thinking(self) -> None:
        now = time.monotonic()
        if self._mode != "think":
            self._mode = "think"
            self._verb_at = now
        elif now - self._verb_at >= _VERB_INTERVAL:
            self._verb = next(self._verbs)
            self._verb_at = now
        self._write_status(f"{self._verb}…", _CYAN)

    # ── carriage-return status plumbing ───────────────────────────────

    _ERASE = "\r\033[2K"

    def _fresh_line(self) -> None:
        """Drop to a new line if the caller was mid-printing the answer."""
        if self._answer_open:
            self._out.write("\n")
            self._answer_open = False

    def _write_status(self, label: str, color: str) -> None:
        """Render/overwrite the single animated status line in place."""
        frame = next(self._frames)
        visible = f"{frame} {label}"
        pad = max(0, self._status_len - len(visible))
        self._out.write(f"{self._ERASE}{color}{visible}{_RESET}{' ' * pad}")
        self._out.flush()
        self._status_len = len(visible)

    def _clear_status(self) -> None:
        """Finalise the live status line with a newline."""
        if self._status_len:
            self._out.write("\n")
            self._out.flush()
            self._status_len = 0

    def _commit(self, line: str) -> None:
        """Finalise any status line, then print a persistent line."""
        self._clear_status()
        self._fresh_line()
        self._out.write(line + "\n")
        self._out.flush()

    # ── plain (non-interactive) path ──────────────────────────────────

    def _handle_plain(self, event: dict[str, Any]) -> None:
        event_type = event.get("type", "")

        if event_type == "thinking":
            if self._mode != "think":
                self._mode = "think"
                print("· thinking", flush=True)

        elif event_type == "tool_call":
            self._mode = None
            name = event.get("name", "tool")
            args = event.get("arguments", {})
            print(f"→ {name}({_short(repr(args), _MAX_ARGS_LEN)})", flush=True)

        elif event_type == "tool_result":
            self._mode = None
            name = event.get("name", "tool")
            result = event.get("content", "")
            is_error = event.get("is_error", False)
            head = _short(result, _MAX_RESULT_LEN)
            if is_error:
                print(f"  ✗ {name}: {head}", flush=True)
            else:
                print(f"  ↳ {head}", flush=True)

        elif event_type == "text":
            self._mode = None

        elif event_type == "error":
            self._mode = None
            msg = event.get("message", "Unknown error")
            print(f"✗ {msg}", flush=True)

        elif event_type == "done":
            self._mode = None
