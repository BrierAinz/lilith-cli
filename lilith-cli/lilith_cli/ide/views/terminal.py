"""TerminalMixin — multi-session terminal panel with PTY support.

Owns the terminal surface area: the command input, command history
navigation (up/down arrows in the terminal input), shell command
execution, the per-session terminal log widgets, and the panel
fullscreen toggle.

Execution modes (see ``widgets/pty_terminal.py`` for the backends):
    * PTY mode (Windows + pywinpty): each session runs a persistent shell
      attached to a real ConPTY, so interactive programs (python REPL,
      git with a pager) work. Input lines are written straight to the
      shell's stdin.
    * Subprocess fallback (non-Windows or pywinpty unavailable): each
      command runs as a one-shot asyncio subprocess — the historical
      behaviour. Detection happens at runtime per command.

Multi-terminal:
    Sessions are managed by a ``TerminalManager`` (created lazily on
    first use). Session 1 reuses the ``#terminal-log`` RichLog composed by
    app.py; later sessions mount a ``SessionRichLog`` into
    ``#terminal-panel`` and visibility is toggled when switching. The
    ``#terminal-title`` Static doubles as the tab bar.

Key bindings:
    app.py hardcodes its BINDINGS and Textual does not merge BINDINGS from
    non-DOMNode mixins, so the terminal shortcuts are registered
    dynamically from ``on_ready`` (a hook app.py does not define) using
    ``App.bind``. The canonical list lives in ``keymaps.TERMINAL_BINDINGS``.

State that lives here (initialised in LilithIDEApp.__init__):
    _terminal_history:        list[str]   -- last 100 commands (MRU, newest at end)
    _terminal_history_index:  int         -- -1 = at the live prompt; >=0 walks back
    _terminal_fullscreen:     bool        -- True when the terminal panel fills the workspace
    _terminal_normal_height:  int         -- remembered pre-fullscreen height

State created lazily by this mixin (app.py must not be touched):
    _terminal_manager:        TerminalManager -- sessions + active cursor
    _terminal_force_fallback: bool            -- set True to disable PTY (tests)
    _pty_resize_timer:        Timer           -- periodic PTY size sync

The App owns the dispatch handlers that route events INTO this mixin:
    on_input_submitted  -- branch to `_run_terminal_command` for `#terminal-input`
    on_key              -- up/down arrow history navigation in the terminal input

Cross-domain calls (resolved via the composed LilithIDEApp instance):
    self._chat_system                              → App cross-domain (logger when to="chat")
    self.run_worker / self.bind / self.set_timer   → Textual App base methods
    self.context_manager.record_terminal_output    → App cross-domain (ContextManager)
    self.notify                                    → App override

Shared worker (`_shell_worker`) with AgentMixin:
    AgentMixin._handle_slash dispatches `/run` and the git subcommands
    (stash/checkout/branch/commit) through `self._shell_worker(...)`. The MRO
    places TerminalMixin before AgentMixin so the call resolves here. Both call
    sites use `# type: ignore[attr-defined]` because the methods are defined on
    different mixin classes.

Process cleanup:
    `on_unmount` (App receives Unmount at shutdown; app.py does not define
    it) kills every live PTY backend so no conhost/shell zombies survive
    the IDE.
"""

from __future__ import annotations

import asyncio
import time

from rich.text import Text
from textual.widgets import RichLog, Static

from ..keymaps import TERMINAL_BINDINGS
from ..widgets.pty_terminal import (
    SessionRichLog,
    TerminalManager,
    TerminalSession,
    WinptyBackend,
    default_shell,
    winpty_available,
)

_TAIL_FLUSH_DELAY = 0.15  # seconds without new output before showing a prompt tail
_RESIZE_POLL_INTERVAL = 1.0  # seconds between PTY size syncs


class TerminalMixin:
    """Multi-session terminal panel: PTY shells, fallback runner, fullscreen."""

    # ── Lifecycle hooks (not defined by app.py; resolved via MRO) ────

    def on_ready(self) -> None:
        """Register terminal shortcuts dynamically (app.py BINDINGS is frozen)."""
        for binding in TERMINAL_BINDINGS:
            try:
                self.bind(  # type: ignore[attr-defined]
                    binding.key, binding.action, description=binding.description
                )
            except Exception:
                pass

    def on_unmount(self) -> None:
        """Kill every PTY shell on IDE shutdown (no conhost zombies)."""
        self._shutdown_terminals()

    def on_resize(self, event) -> None:  # noqa: ANN001 - textual event
        """Keep the active PTY's size in sync with the panel."""
        self.call_after_refresh(self._sync_pty_size)  # type: ignore[attr-defined]

    def _shutdown_terminals(self) -> None:
        manager = getattr(self, "_terminal_manager", None)
        if manager is None:
            return
        for session in manager.sessions:
            if session.backend is not None:
                session.backend.kill()
                session.backend = None

    # ── Session plumbing ────────────────────────────────────────────

    def _term_manager(self) -> TerminalManager:
        """Lazily create the manager + session 1 (bound to `#terminal-log`)."""
        manager = getattr(self, "_terminal_manager", None)
        if manager is None:
            manager = TerminalManager()
            manager.create(cwd=self.root)  # type: ignore[attr-defined]
            self._terminal_manager = manager
        return manager

    def _use_pty(self) -> bool:
        """PTY mode is opt-out: runtime-detected, disabled by force-fallback."""
        if getattr(self, "_terminal_force_fallback", False):
            return False
        return winpty_available()

    def _session_log_widget(self, session: TerminalSession) -> RichLog:
        try:
            return self.query_one(f"#{session.widget_id}", RichLog)  # type: ignore[attr-defined]
        except Exception:
            return self.query_one("#terminal-log", RichLog)  # type: ignore[attr-defined]

    # ── Actions ─────────────────────────────────────────────────────

    def action_focus_terminal(self) -> None:
        self.query_one("#terminal-input").focus()  # type: ignore[attr-defined]

    def action_new_terminal(self) -> None:
        """Create a new terminal session (tab) and switch to it."""
        manager = self._term_manager()
        session = manager.create(cwd=self.root)  # type: ignore[attr-defined]
        log = SessionRichLog(id=session.widget_id, highlight=True, markup=True)
        panel = self.query_one("#terminal-panel")  # type: ignore[attr-defined]
        panel.mount(log, before="#terminal-input")
        self.call_after_refresh(self._post_new_terminal, session)  # type: ignore[attr-defined]

    def _post_new_terminal(self, session: TerminalSession) -> None:
        manager = self._term_manager()
        if session not in manager.sessions:
            return
        self._activate_session(session)
        if self._use_pty():
            self._start_pty_session(session)
        self.query_one("#terminal-input").focus()  # type: ignore[attr-defined]

    def action_close_terminal(self) -> None:
        """Close the active terminal session (never the last one)."""
        manager = self._term_manager()
        if len(manager.sessions) <= 1:
            self.notify(  # type: ignore[attr-defined]
                "No se puede cerrar la última terminal", severity="warning"
            )
            return
        session = manager.close_active()
        if session is None:
            return
        if session.backend is not None:
            session.backend.kill()
            session.backend = None
        try:
            self.query_one(f"#{session.widget_id}").remove()  # type: ignore[attr-defined]
        except Exception:
            pass
        active = manager.active
        if active is not None:
            self._activate_session(active)

    def action_next_terminal(self) -> None:
        self._cycle_terminal(1)

    def action_prev_terminal(self) -> None:
        self._cycle_terminal(-1)

    def _cycle_terminal(self, delta: int) -> None:
        manager = self._term_manager()
        if len(manager.sessions) < 2:
            return
        session = manager.cycle(delta)
        if session is not None:
            self._activate_session(session)
            self.query_one("#terminal-input").focus()  # type: ignore[attr-defined]

    def action_toggle_terminal_fullscreen(self) -> None:
        """Expand/collapse the terminal panel to fill the workspace."""
        terminal = self.query_one("#terminal-panel")  # type: ignore[attr-defined]
        if not self._terminal_fullscreen:  # type: ignore[attr-defined]
            self._terminal_normal_height = terminal.styles.height  # type: ignore[attr-defined]
            terminal.styles.height = "1fr"
            self._terminal_fullscreen = True  # type: ignore[attr-defined]
            self.notify("Terminal fullscreen", severity="information")  # type: ignore[attr-defined]
        else:
            terminal.styles.height = self._terminal_normal_height  # type: ignore[attr-defined]
            self._terminal_fullscreen = False  # type: ignore[attr-defined]
            self.notify("Terminal restaurado", severity="information")  # type: ignore[attr-defined]
        self.call_after_refresh(self._sync_pty_size)  # type: ignore[attr-defined]

    # ── Session activation / tab bar ────────────────────────────────

    def _activate_session(self, session: TerminalSession) -> None:
        manager = self._term_manager()
        manager.activate(session)
        for candidate in manager.sessions:
            try:
                widget = self.query_one(f"#{candidate.widget_id}")  # type: ignore[attr-defined]
                widget.styles.display = "block" if candidate is session else "none"
            except Exception:
                pass
        self._refresh_terminal_title()
        self.call_after_refresh(self._sync_pty_size)  # type: ignore[attr-defined]

    def _refresh_terminal_title(self) -> None:
        manager = self._term_manager()
        title = "⚡ Terminal — Midgard"
        if len(manager.sessions) > 1:
            tabs = []
            for index, session in enumerate(manager.sessions, start=1):
                marker = "●" if session is manager.active else "○"
                dead = "✗" if session.exited else ""
                tabs.append(f"{marker}{index}{dead}")
            title += "   " + "  ".join(tabs)
        try:
            self.query_one("#terminal-title", Static).update(title)  # type: ignore[attr-defined]
        except Exception:
            pass

    # ── Command runner ──────────────────────────────────────────────

    def _run_terminal_command(self) -> None:
        input_widget = self.query_one("#terminal-input")  # type: ignore[attr-defined]
        command = input_widget.value.strip()
        input_widget.value = ""
        manager = self._term_manager()
        session = manager.active
        if session is None:  # pragma: no cover - manager always seeds one
            return
        pty_live = session.is_running()

        if not command:
            if pty_live:
                # Empty Enter goes to the PTY (useful for pagers/prompts).
                session.backend.write("\r\n")  # type: ignore[union-attr]
            elif session.exited and self._use_pty():
                # Enter on a dead PTY session relaunches the shell.
                self._start_pty_session(session)
            return

        self._terminal_history_index = -1  # type: ignore[attr-defined]
        if (
            not self._terminal_history  # type: ignore[attr-defined]
            or self._terminal_history[-1] != command  # type: ignore[attr-defined]
        ):
            self._terminal_history.append(command)  # type: ignore[attr-defined]
            if len(self._terminal_history) > 100:  # type: ignore[attr-defined]
                self._terminal_history = self._terminal_history[-100:]  # type: ignore[attr-defined]

        if command.lower() in ("clear", "cls"):
            # ANSI clears are stripped from PTY output, so clear locally.
            self._session_log_widget(session).clear()
            return

        if self._use_pty():
            if not pty_live:
                pty_live = self._start_pty_session(session)
            if pty_live and session.backend is not None:
                session.backend.write(command + "\r\n")
                return

        # Subprocess fallback: one-shot command (historical behaviour).
        self._terminal_log(f"> {command}")
        self._active_worker = self.run_worker(  # type: ignore[attr-defined]
            self._shell_worker(command, to="terminal"), exclusive=True
        )

    # ── PTY session lifecycle ───────────────────────────────────────

    def _start_pty_session(self, session: TerminalSession) -> bool:
        """Spawn the shell PTY for `session`; True on success."""
        shell = default_shell()
        log = self._session_log_widget(session)
        rows = max(2, log.size.height or 0) or 24
        cols = max(20, log.size.width or 0) or 80
        if session.backend is not None:
            session.backend.kill()  # release the dead PTY handle before relaunch
        session.reset_for_relaunch()
        try:
            session.backend = WinptyBackend(
                shell,
                cwd=session.cwd or self.root,  # type: ignore[attr-defined]
                rows=rows,
                cols=cols,
            )
        except Exception as exc:
            session.backend = None
            self._terminal_force_fallback = True
            self._terminal_log(
                f"[red]No se pudo iniciar PTY ({exc}); usando modo subprocess.[/]"
            )
            return False
        log.write(f"[dim]PTY iniciado: {' '.join(shell)}[/]")
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._pty_reader(session),
            thread=True,
            exclusive=False,
            exit_on_error=False,
            group=f"pty-{session.uid}",
        )
        self._ensure_resize_timer()
        self._refresh_terminal_title()
        return True

    def _pty_reader(self, session: TerminalSession) -> None:
        """Thread worker: pump PTY output into the UI until the shell dies."""
        backend = session.backend
        if backend is None:  # pragma: no cover - defensive
            return
        while True:
            try:
                chunk = backend.read(4096)
            except (EOFError, OSError):
                break
            except Exception:
                break
            if not chunk:
                if not backend.is_alive():
                    break
                time.sleep(0.02)
                continue
            try:
                self.call_from_thread(self._on_pty_output, session, chunk)  # type: ignore[attr-defined]
            except Exception:
                return  # app is shutting down
        exit_code = None
        for _ in range(100):
            if not backend.is_alive():
                exit_code = backend.exit_code
                break
            time.sleep(0.05)
        try:
            self.call_from_thread(self._on_pty_exit, session, exit_code)  # type: ignore[attr-defined]
        except Exception:
            pass  # app is shutting down

    def _on_pty_output(self, session: TerminalSession, chunk: str) -> None:
        """UI thread: append cleaned PTY output lines to the session log."""
        lines = session.assembler.feed(chunk)
        log = self._session_log_widget(session)
        for line in lines:
            # Plain Text: shell output must never be parsed as Rich markup.
            log.write(Text(line))
        if lines:
            self.context_manager.record_terminal_output(lines)  # type: ignore[attr-defined]
        session.gen += 1
        generation = session.gen
        if session.assembler.peek_tail():
            # Debounced flush so prompts (no trailing newline) become visible.
            self.set_timer(  # type: ignore[attr-defined]
                _TAIL_FLUSH_DELAY,
                lambda: self._flush_pty_tail(session, generation),
            )

    def _flush_pty_tail(self, session: TerminalSession, generation: int) -> None:
        if session.gen != generation:
            return  # newer output arrived; its own timer will handle the tail
        tail = session.assembler.take_flush()
        if tail:
            self._session_log_widget(session).write(Text(tail))

    def _on_pty_exit(self, session: TerminalSession, exit_code: int | None) -> None:
        """UI thread: mark the session dead, show exit code, offer relaunch."""
        session.exited = True
        session.exit_code = exit_code
        remainder = session.assembler.drain()
        log = self._session_log_widget(session)
        if remainder:
            log.write(Text(remainder))
        log.write(
            f"[dim]— proceso terminado (exit code: {exit_code}) — "
            f"Enter para relanzar —[/]"
        )
        self.context_manager.record_terminal_output(  # type: ignore[attr-defined]
            [f"Exit code: {exit_code}"]
        )
        self._refresh_terminal_title()

    # ── PTY size sync ───────────────────────────────────────────────

    def _ensure_resize_timer(self) -> None:
        """Poll the panel size so PTY resize works for every layout change

        (window resize, fullscreen toggle, zen mode) without touching app.py.
        """
        if getattr(self, "_pty_resize_timer", None) is None:
            try:
                self._pty_resize_timer = self.set_interval(  # type: ignore[attr-defined]
                    _RESIZE_POLL_INTERVAL, self._sync_pty_size
                )
            except Exception:
                self._pty_resize_timer = None

    def _sync_pty_size(self) -> None:
        manager = getattr(self, "_terminal_manager", None)
        if manager is None:
            return
        session = manager.active
        if session is None or not session.is_running():
            return
        try:
            log = self._session_log_widget(session)
            rows, cols = log.size.height, log.size.width
        except Exception:
            return
        if rows > 0 and cols > 0:
            session.backend.resize(rows, cols)  # type: ignore[union-attr]

    # ── Subprocess fallback worker ──────────────────────────────────

    async def _shell_worker(self, command: str, *, to: str = "chat") -> None:
        """Run a shell command and route its output to the terminal or chat log.

        Shared with AgentMixin: the `/run` slash command and the git
        subcommands (`/git stash`, `/git checkout`, `/git branch`,
        `/git commit`) all dispatch through this worker. They call
        `self._shell_worker(cmd)` which routes output to the chat log
        (`to="chat"` by default).
        """
        logger = self._terminal_log if to == "terminal" else self._chat_system  # type: ignore[attr-defined]
        logger(f"[dim]Ejecutando: {command}[/]")
        captured: list[str] = []
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            stdout, stderr = await proc.communicate()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            err_text = stderr.decode("utf-8", errors="replace").strip()
            if out_text:
                logger(f"[dim]{out_text}[/]")
                captured.extend(out_text.splitlines())
            if err_text:
                logger(f"[red]{err_text}[/]")
                captured.extend(err_text.splitlines())
            logger(f"[dim]Exit code: {proc.returncode}[/]")
            captured.append(f"Exit code: {proc.returncode}")
        except Exception as exc:
            logger(f"[red]Error ejecutando comando:[/] {exc}")
            captured.append(f"Error: {exc}")
        self.context_manager.record_terminal_output(captured)  # type: ignore[attr-defined]

    # ── Log widget helper ───────────────────────────────────────────

    def _terminal_log(self, text: str) -> None:
        """Write markup text to the *active* session's log (or the base log)."""
        manager = getattr(self, "_terminal_manager", None)
        if manager is not None and manager.active is not None:
            log = self._session_log_widget(manager.active)
        else:
            log = self.query_one("#terminal-log", RichLog)  # type: ignore[attr-defined]
        log.write(text)
