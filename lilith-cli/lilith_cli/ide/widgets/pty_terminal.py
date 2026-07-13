"""PTY-backed terminal sessions for the Lilith IDE terminal panel.

Architecture
------------
The terminal panel supports two execution modes behind one session model:

* **PTY mode (Windows)** — :class:`WinptyBackend` wraps ``pywinpty``
  (``import winpty``) and spawns a *persistent* shell attached to a real
  ConPTY. Interactive programs (``python`` REPL, ``git`` with a pager)
  work because they see a real console. Availability is detected at
  runtime with :func:`winpty_available`; nothing imports ``winpty`` at
  module import time, so Linux/macOS keep working untouched.
* **Subprocess fallback** — when no PTY backend is available (non-Windows,
  or pywinpty missing/broken) a session keeps ``backend is None`` and
  ``TerminalMixin`` runs each command as a one-shot ``asyncio`` subprocess
  (the historical behaviour).

Pure-logic pieces (:class:`TerminalManager`, :class:`TerminalSession`,
:class:`OutputLineAssembler`, :func:`strip_ansi`, :func:`default_shell`)
are UI-free and unit tested in ``tests/test_terminal.py``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from textual.widgets import RichLog

# ── Runtime detection ────────────────────────────────────────────────

_WINPTY_AVAILABLE: bool | None = None


def winpty_available() -> bool:
    """Return True when pywinpty can actually be imported (Windows only).

    Detected at runtime and cached; never imported at module load so the
    IDE keeps working on Linux/macOS where pywinpty does not exist.
    """
    global _WINPTY_AVAILABLE
    if _WINPTY_AVAILABLE is None:
        if sys.platform != "win32":
            _WINPTY_AVAILABLE = False
        else:
            try:
                import winpty  # noqa: F401

                _WINPTY_AVAILABLE = True
            except Exception:
                _WINPTY_AVAILABLE = False
    return _WINPTY_AVAILABLE


def default_shell() -> list[str]:
    """Return the argv of the default interactive shell for this OS.

    On Windows (cmd.exe) the codepage is forced to UTF-8 (``chcp 65001``)
    so that non-ASCII output round-trips correctly through the PTY.
    """
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        if Path(comspec).name.lower() == "cmd.exe":
            return [comspec, "/K", "chcp", "65001"]
        return [comspec]
    return [os.environ.get("SHELL") or "/bin/bash"]


# ── ANSI / control-character cleanup ─────────────────────────────────

# Order matters: full OSC/DCS/CSI sequences first, lone escapes last.
_ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC … BEL/ST (tolerate unterminated)
    r"|\x1bP[^\x1b]*(?:\x1b\\)?"  # DCS
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b[@-Z\\^_\[\]]"  # remaining two-char escapes
)
# Control chars except \t (0x09), \n (0x0a), \r (0x0d) and \x08 (handled below).
_CTRL_RE = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1f\x7f]")
_BACKSPACE_PAIR_RE = re.compile(r"[^\x08]\x08")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and non-printable control characters.

    Backspaces are *applied* (they erase the previous character) before
    any leftovers are dropped, so interactive editing echoes render sanely.
    """
    text = _ANSI_RE.sub("", text)
    while "\x08" in text:
        reduced = _BACKSPACE_PAIR_RE.sub("", text)
        if reduced == text:
            text = text.replace("\x08", "")
            break
        text = reduced
    return _CTRL_RE.sub("", text)


def _apply_carriage_returns(line: str) -> str:
    """Resolve bare ``\\r`` overwrites inside a single logical line.

    Progress bars emit ``10%\\r20%\\r30%`` — we keep the last non-empty
    segment, which is what the user would see on a real console.
    """
    if "\r" not in line:
        return line
    for part in reversed(line.split("\r")):
        if part:
            return part
    return ""


def clean_line(raw: str) -> str:
    """Turn one raw PTY line into printable text (no ANSI, CR resolved)."""
    return _apply_carriage_returns(strip_ansi(raw))


class OutputLineAssembler:
    """Accumulates raw PTY chunks and yields clean, complete lines.

    Escape sequences and ``\\r\\n`` pairs can be split across read chunks,
    so the raw tail (text after the last newline) is kept unprocessed
    until more data arrives. The tail can still be *flushed* (e.g. to show
    a shell prompt that never ends in a newline); flushing is idempotent —
    the same tail is never emitted twice.
    """

    def __init__(self) -> None:
        self._tail: str = ""
        self._flushed_tail: str = ""

    def feed(self, chunk: str) -> list[str]:
        """Add a raw chunk; return the newly completed, cleaned lines."""
        data = (self._tail + chunk).replace("\r\n", "\n")
        parts = data.split("\n")
        self._tail = parts[-1]
        lines = [clean_line(part) for part in parts[:-1]]
        if lines:
            self._flushed_tail = ""
        return lines

    def peek_tail(self) -> str:
        """Return the cleaned pending tail without consuming it."""
        return clean_line(self._tail)

    def take_flush(self) -> str | None:
        """Return the cleaned tail if it has not been flushed yet."""
        cleaned = clean_line(self._tail)
        if cleaned and cleaned != self._flushed_tail:
            self._flushed_tail = cleaned
            return cleaned
        return None

    def drain(self) -> str:
        """Consume and return whatever unflushed tail remains (at EOF)."""
        cleaned = clean_line(self._tail)
        self._tail = ""
        if cleaned and cleaned != self._flushed_tail:
            self._flushed_tail = ""
            return cleaned
        self._flushed_tail = ""
        return ""


# ── PTY backend (Windows / pywinpty) ─────────────────────────────────


class WinptyBackend:
    """A persistent shell attached to a real Windows PTY (pywinpty).

    ``winpty`` is imported lazily inside ``__init__`` so this class can be
    defined (and the module imported) on any OS.
    """

    MIN_ROWS = 2
    MIN_COLS = 20

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        import winpty

        rows = max(self.MIN_ROWS, int(rows))
        cols = max(self.MIN_COLS, int(cols))
        env_map = dict(os.environ)
        # UTF-8 in/out for child Python processes and unbuffered pipes.
        env_map.setdefault("PYTHONIOENCODING", "utf-8")
        env_map.setdefault("PYTHONUNBUFFERED", "1")
        if env:
            env_map.update(env)
        self._proc = winpty.PtyProcess.spawn(
            list(argv),
            cwd=str(cwd) if cwd else None,
            env=env_map,
            dimensions=(rows, cols),
        )
        self._size = (rows, cols)

    def read(self, size: int = 4096) -> str:
        """Blocking read; returns decoded text, raises EOFError at EOF."""
        return self._proc.read(size)

    def write(self, data: str) -> None:
        self._proc.write(data)

    def resize(self, rows: int, cols: int) -> None:
        rows = max(self.MIN_ROWS, int(rows))
        cols = max(self.MIN_COLS, int(cols))
        if (rows, cols) == self._size:
            return
        try:
            self._proc.setwinsize(rows, cols)
            self._size = (rows, cols)
        except Exception:
            pass

    def is_alive(self) -> bool:
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    @property
    def exit_code(self) -> int | None:
        try:
            return self._proc.exitstatus
        except Exception:
            return None

    def kill(self) -> None:
        """Terminate the shell and close the PTY (no conhost zombies)."""
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass
        try:
            self._proc.close(force=True)
        except Exception:
            pass


# ── Session / manager (pure logic, unit-testable) ────────────────────


class TerminalSession:
    """One terminal instance: a log surface plus (optionally) a PTY shell.

    ``backend is None``  → subprocess fallback mode (one-shot commands).
    ``backend`` set      → persistent PTY shell; ``exited`` flips True when
    the shell process ends (exit code kept in ``exit_code``; the session
    can be relaunched by starting a new backend).
    """

    def __init__(
        self,
        uid: int,
        title: str,
        widget_id: str,
        cwd: str | Path | None = None,
    ) -> None:
        self.uid = uid
        self.title = title
        self.widget_id = widget_id
        self.cwd = cwd
        self.backend: WinptyBackend | None = None
        self.exit_code: int | None = None
        self.exited: bool = False
        self.assembler = OutputLineAssembler()
        self.gen: int = 0  # output generation counter (tail-flush debounce)

    @property
    def mode(self) -> str:
        return "pty" if self.backend is not None else "subprocess"

    def is_running(self) -> bool:
        return self.backend is not None and self.backend.is_alive()

    def reset_for_relaunch(self) -> None:
        """Prepare state before attaching a fresh backend."""
        self.backend = None
        self.exit_code = None
        self.exited = False
        self.assembler = OutputLineAssembler()


class TerminalManager:
    """Owns the list of terminal sessions and the active-session cursor.

    Pure logic — no Textual imports beyond type reuse — so tab management
    (create/close/cycle) is unit-testable without a running app.

    The first session is bound to the ``#terminal-log`` widget composed by
    ``app.py``; later sessions get unique widget ids and their RichLogs are
    mounted dynamically by ``TerminalMixin``.
    """

    BASE_WIDGET_ID = "terminal-log"

    def __init__(self) -> None:
        self.sessions: list[TerminalSession] = []
        self.active_index: int = 0
        self._uid: int = 0

    @property
    def active(self) -> TerminalSession | None:
        if not self.sessions:
            return None
        self.active_index = min(self.active_index, len(self.sessions) - 1)
        return self.sessions[self.active_index]

    def create(
        self, *, title: str | None = None, cwd: str | Path | None = None
    ) -> TerminalSession:
        self._uid += 1
        widget_id = (
            self.BASE_WIDGET_ID if self._uid == 1 else f"{self.BASE_WIDGET_ID}-{self._uid}"
        )
        session = TerminalSession(
            self._uid, title or str(self._uid), widget_id, cwd=cwd
        )
        self.sessions.append(session)
        self.active_index = len(self.sessions) - 1
        return session

    def close_active(self) -> TerminalSession | None:
        """Remove the active session; refuse to close the last one."""
        if len(self.sessions) <= 1:
            return None
        session = self.sessions.pop(self.active_index)
        self.active_index = min(self.active_index, len(self.sessions) - 1)
        return session

    def cycle(self, delta: int = 1) -> TerminalSession | None:
        if not self.sessions:
            return None
        self.active_index = (self.active_index + delta) % len(self.sessions)
        return self.sessions[self.active_index]

    def index_of(self, session: TerminalSession) -> int:
        return self.sessions.index(session)

    def activate(self, session: TerminalSession) -> None:
        self.active_index = self.sessions.index(session)


class SessionRichLog(RichLog):
    """RichLog for dynamically-mounted terminal sessions.

    Mirrors the ``#terminal-log`` CSS from theme.py so extra terminals look
    identical to the first one without touching the theme file.
    """

    DEFAULT_CSS = """
    SessionRichLog {
        height: 1fr;
        border: none;
        padding: 0;
    }
    """
