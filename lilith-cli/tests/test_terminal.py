"""Tests for the multi-session IDE terminal (PTY + subprocess fallback).

Only PTY-free logic is exercised here: ANSI/output parsing, the line
assembler, session/tab management, keybinding registration, the
subprocess-fallback dispatch and process cleanup (with fake backends).

Not covered (manual-only, needs a real interactive console):
    * Real pywinpty shell I/O (python REPL, git pager) inside the TUI.
    * PTY resize behaviour of real child processes.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from textual.widgets import Input, RichLog

from lilith_cli.ide.app import LilithIDEApp
from lilith_cli.ide.keymaps import IDE_BINDINGS, TERMINAL_BINDINGS
from lilith_cli.ide.widgets.pty_terminal import (
    OutputLineAssembler,
    TerminalManager,
    clean_line,
    default_shell,
    strip_ansi,
    winpty_available,
)


# ── ANSI / output parsing ────────────────────────────────────────────


class TestStripAnsi:
    def test_plain_text_untouched(self):
        assert strip_ansi("hola mundo") == "hola mundo"

    def test_removes_csi_sequences(self):
        assert strip_ansi("\x1b[31mrojo\x1b[0m") == "rojo"

    def test_removes_private_csi_sequences(self):
        # Real conpty preamble observed on Windows.
        raw = "\x1b[1t\x1b[c\x1b[?1004h\x1b[?9001h\x1b[?7l\x1b[?7hhola"
        assert strip_ansi(raw) == "hola"

    def test_removes_osc_title_sequence(self):
        assert strip_ansi("\x1b]0;titulo\x07texto") == "texto"

    def test_removes_osc_with_st_terminator(self):
        assert strip_ansi("\x1b]0;titulo\x1b\\texto") == "texto"

    def test_applies_backspaces(self):
        assert strip_ansi("abcd\x08\x08XY") == "abXY"

    def test_leading_backspaces_dropped(self):
        assert strip_ansi("\x08\x08hola") == "hola"

    def test_strips_bell_and_control_chars(self):
        assert strip_ansi("din\x07g\x00!") == "ding!"

    def test_preserves_tabs(self):
        assert strip_ansi("a\tb") == "a\tb"


class TestCleanLine:
    def test_carriage_return_keeps_last_segment(self):
        assert clean_line("10%\r20%\r30%") == "30%"

    def test_trailing_carriage_return_keeps_last_nonempty(self):
        assert clean_line("100%\r") == "100%"

    def test_no_carriage_return_passthrough(self):
        assert clean_line("normal") == "normal"

    def test_only_carriage_returns_is_empty(self):
        assert clean_line("\r\r") == ""


class TestOutputLineAssembler:
    def test_complete_lines_emitted(self):
        asm = OutputLineAssembler()
        assert asm.feed("uno\r\ndos\r\n") == ["uno", "dos"]

    def test_partial_line_held_as_tail(self):
        asm = OutputLineAssembler()
        assert asm.feed("incompl") == []
        assert asm.peek_tail() == "incompl"
        assert asm.feed("eta\r\n") == ["incompleta"]
        assert asm.peek_tail() == ""

    def test_crlf_split_across_chunks(self):
        asm = OutputLineAssembler()
        assert asm.feed("foo\r") == []
        assert asm.feed("\nbar\r\n") == ["foo", "bar"]

    def test_ansi_stripped_from_lines(self):
        asm = OutputLineAssembler()
        assert asm.feed("\x1b[32mok\x1b[0m\n") == ["ok"]

    def test_take_flush_is_idempotent(self):
        asm = OutputLineAssembler()
        asm.feed("C:\\proyecto>")
        assert asm.take_flush() == "C:\\proyecto>"
        assert asm.take_flush() is None  # same tail not flushed twice

    def test_new_output_resets_flush_dedupe(self):
        asm = OutputLineAssembler()
        asm.feed("C:\\proyecto>")
        assert asm.take_flush() == "C:\\proyecto>"
        asm.feed("dir\r\n")  # completes the prompt line
        asm.feed("C:\\proyecto>")
        assert asm.take_flush() == "C:\\proyecto>"

    def test_drain_returns_remaining_tail(self):
        asm = OutputLineAssembler()
        asm.feed("final sin newline")
        assert asm.drain() == "final sin newline"
        assert asm.peek_tail() == ""

    def test_drain_skips_already_flushed_tail(self):
        asm = OutputLineAssembler()
        asm.feed(">>>")
        assert asm.take_flush() == ">>>"
        assert asm.drain() == ""


# ── Platform detection / shell selection ─────────────────────────────


class TestPlatformDetection:
    def test_winpty_availability_matches_platform(self):
        if sys.platform == "win32":
            # pywinpty is a declared dependency on Windows.
            assert winpty_available() is True
        else:
            assert winpty_available() is False

    def test_default_shell_is_nonempty_argv(self):
        shell = default_shell()
        assert isinstance(shell, list) and shell
        if sys.platform == "win32":
            assert "cmd" in shell[0].lower() or shell[0]
        else:
            assert shell[0].startswith("/") or shell[0]

    def test_default_shell_forces_utf8_codepage_for_cmd(self):
        if sys.platform != "win32":
            pytest.skip("Windows-only")
        shell = default_shell()
        if Path(shell[0]).name.lower() == "cmd.exe":
            assert "65001" in shell


# ── Tab / session management (pure logic) ────────────────────────────


class TestTerminalManager:
    def test_first_session_binds_base_widget(self):
        mgr = TerminalManager()
        first = mgr.create()
        assert first.widget_id == "terminal-log"
        assert mgr.active is first

    def test_later_sessions_get_unique_widget_ids(self):
        mgr = TerminalManager()
        mgr.create()
        second = mgr.create()
        third = mgr.create()
        assert second.widget_id != third.widget_id
        assert second.widget_id.startswith("terminal-log-")
        assert mgr.active is third

    def test_widget_ids_not_reused_after_close(self):
        mgr = TerminalManager()
        mgr.create()
        second = mgr.create()
        mgr.close_active()
        replacement = mgr.create()
        assert replacement.widget_id != second.widget_id

    def test_cycle_wraps_forward_and_backward(self):
        mgr = TerminalManager()
        a, b, c = mgr.create(), mgr.create(), mgr.create()
        assert mgr.active is c
        assert mgr.cycle(1) is a  # wraps
        assert mgr.cycle(-1) is c
        assert mgr.cycle(-1) is b

    def test_close_refuses_last_session(self):
        mgr = TerminalManager()
        mgr.create()
        assert mgr.close_active() is None
        assert len(mgr.sessions) == 1

    def test_close_active_pops_and_clamps_index(self):
        mgr = TerminalManager()
        a, b, c = mgr.create(), mgr.create(), mgr.create()
        closed = mgr.close_active()  # closes c (last, active)
        assert closed is c
        assert mgr.active is b
        mgr.activate(a)
        assert mgr.close_active() is a
        assert mgr.active is b

    def test_new_session_defaults(self):
        mgr = TerminalManager()
        session = mgr.create(cwd="/tmp")
        assert session.backend is None
        assert session.mode == "subprocess"
        assert session.exited is False
        assert session.exit_code is None
        assert session.is_running() is False

    def test_reset_for_relaunch_clears_state(self):
        mgr = TerminalManager()
        session = mgr.create()
        session.exited = True
        session.exit_code = 9
        session.assembler.feed("basura")
        session.reset_for_relaunch()
        assert session.exited is False
        assert session.exit_code is None
        assert session.assembler.peek_tail() == ""


# ── Keymap declarations ──────────────────────────────────────────────


class TestTerminalKeymaps:
    def test_terminal_bindings_declared(self):
        actions = {binding.action for binding in TERMINAL_BINDINGS}
        assert actions == {
            "new_terminal",
            "close_terminal",
            "next_terminal",
            "prev_terminal",
        }

    def test_terminal_bindings_folded_into_ide_bindings(self):
        ide_actions = {binding.action for binding in IDE_BINDINGS}
        assert {"new_terminal", "close_terminal"} <= ide_actions

    def test_no_key_collisions_in_ide_bindings(self):
        keys = [binding.key for binding in IDE_BINDINGS]
        assert len(keys) == len(set(keys))


# ── Fakes for app-level tests ────────────────────────────────────────


class FakeBackend:
    """Stands in for WinptyBackend without spawning any process."""

    def __init__(self) -> None:
        self.killed = False
        self.written: list[str] = []
        self.exit_code = None

    def write(self, data: str) -> None:
        self.written.append(data)

    def resize(self, rows: int, cols: int) -> None:
        pass

    def is_alive(self) -> bool:
        return not self.killed

    def kill(self) -> None:
        self.killed = True


def _make_app(fake_session, tmp_path) -> LilithIDEApp:
    app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
    # Never spawn real PTY shells from tests.
    app._terminal_force_fallback = True
    return app


def _second_terminal_settled(app, manager) -> bool:
    """True cuando ``action_new_terminal`` terminó DEL TODO.

    ``manager.create()`` es síncrono, pero ``_post_new_terminal`` llega vía
    ``call_after_refresh`` y re-activa la sesión nueva — interactuar antes de
    que corra deja que ese callback tardío pise el estado del test (p.ej.
    revirtiendo un cycle posterior). Su efecto observable es el estado
    visual final: log nuevo visible y log base oculto.
    """
    return bool(
        len(manager.sessions) == 2
        and app.query(f"#{manager.sessions[1].widget_id}")
        and str(app.query_one(f"#{manager.sessions[1].widget_id}", RichLog).styles.display) == "block"
        and str(app.query_one("#terminal-log", RichLog).styles.display) == "none"
    )


async def _wait_until(pilot, condition, timeout: float = 10.0) -> None:
    """Pump the app until ``condition()`` holds or ``timeout`` expires.

    Mount + ``call_after_refresh`` chains need a variable number of refresh
    cycles under suite load — fixed ``pilot.pause()`` pairs race them (see
    the flake history of this file). Asserting after the deadline keeps the
    failure message on the caller's assert, not on a timeout error.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if condition():
            return
        await asyncio.sleep(0.05)


# ── Mixin behaviour inside the app (fallback mode, no real processes) ─


class TestTerminalMixinTabs:
    async def test_new_terminal_mounts_widget_and_switches(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_terminal()
            manager = app._terminal_manager
            await _wait_until(pilot, lambda: _second_terminal_settled(app, manager))
            assert len(manager.sessions) == 2
            second = manager.sessions[1]
            assert manager.active is second
            # The new RichLog exists and is the visible one.
            new_log = app.query_one(f"#{second.widget_id}", RichLog)
            base_log = app.query_one("#terminal-log", RichLog)
            assert str(new_log.styles.display) == "block"
            assert str(base_log.styles.display) == "none"

    async def test_tab_bar_shown_in_title(self, fake_session, tmp_path):
        # ``action_new_terminal`` mounts a new ``RichLog`` and then calls
        # ``call_after_refresh(_post_new_terminal)`` which is the step that
        # ultimately calls ``_refresh_terminal_title``.
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_terminal()
            from textual.widgets import Static

            def _title() -> str:
                return str(app.query_one("#terminal-title", Static).render())

            await _wait_until(pilot, lambda: "●2" in _title() and "○1" in _title())
            title = _title()
            assert "●2" in title and "○1" in title, title

    async def test_cycle_terminals(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_terminal()
            manager = app._terminal_manager
            # Waiting for the mount alone is NOT enough: the deferred
            # _post_new_terminal re-activates session 2 and would override
            # a cycle issued before it ran.
            await _wait_until(pilot, lambda: _second_terminal_settled(app, manager))
            assert manager.active_index == 1
            app.action_next_terminal()
            await _wait_until(pilot, lambda: manager.active_index == 0)
            assert manager.active_index == 0
            app.action_prev_terminal()
            await _wait_until(pilot, lambda: manager.active_index == 1)
            assert manager.active_index == 1

    async def test_close_terminal_removes_widget(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_terminal()
            manager = app._terminal_manager
            await _wait_until(pilot, lambda: len(manager.sessions) == 2)
            second = manager.sessions[1]
            app.action_close_terminal()
            await _wait_until(
                pilot,
                lambda: len(manager.sessions) == 1
                and not app.query(f"#{second.widget_id}"),
            )
            assert len(manager.sessions) == 1
            assert not app.query(f"#{second.widget_id}")

    async def test_close_last_terminal_refused(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app._term_manager()  # seed session 1
            app.action_close_terminal()
            await _wait_until(
                pilot,
                lambda: any(
                    "última terminal" in item["message"]
                    for item in app._toast_history
                ),
            )
            assert len(app._terminal_manager.sessions) == 1
            assert any(
                "última terminal" in item["message"] for item in app._toast_history
            )

    async def test_terminal_shortcuts_bound_at_runtime(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            bound_keys = set(app._bindings.key_to_bindings.keys())
            for binding in TERMINAL_BINDINGS:
                assert binding.key in bound_keys


class TestTerminalMixinDispatch:
    async def test_fallback_dispatches_to_shell_worker(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        captured: list[tuple[str, str]] = []

        async def fake_shell_worker(command: str, *, to: str = "chat") -> None:
            captured.append((command, to))

        app._shell_worker = fake_shell_worker
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#terminal-input", Input).value = "echo hola"
            app._run_terminal_command()
            await pilot.pause()
            assert captured == [("echo hola", "terminal")]
            assert app._terminal_history[-1] == "echo hola"

    async def test_empty_input_is_noop_in_fallback(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#terminal-input", Input).value = "   "
            app._run_terminal_command()
            await pilot.pause()
            assert app._terminal_history == []

    async def test_repeated_command_not_duplicated_in_history(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)

        async def fake_shell_worker(command: str, *, to: str = "chat") -> None:
            pass

        app._shell_worker = fake_shell_worker
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(2):
                app.query_one("#terminal-input", Input).value = "git status"
                app._run_terminal_command()
                await pilot.pause()
            assert app._terminal_history.count("git status") == 1

    async def test_clear_command_clears_active_log(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app._terminal_log("algo de texto")
            await pilot.pause()
            log = app.query_one("#terminal-log", RichLog)
            assert log.lines
            app.query_one("#terminal-input", Input).value = "clear"
            app._run_terminal_command()
            await pilot.pause()
            assert not log.lines

    async def test_pty_command_written_to_backend(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            session = app._term_manager().active
            session.backend = FakeBackend()
            app._terminal_force_fallback = False  # PTY path, fake backend is alive
            app.query_one("#terminal-input", Input).value = "python"
            app._run_terminal_command()
            await pilot.pause()
            assert session.backend.written == ["python\r\n"]

    async def test_empty_enter_forwarded_to_live_pty(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            session = app._term_manager().active
            session.backend = FakeBackend()
            app._terminal_force_fallback = False
            app.query_one("#terminal-input", Input).value = ""
            app._run_terminal_command()
            await pilot.pause()
            assert session.backend.written == ["\r\n"]


class TestPtyOutputPipeline:
    async def test_output_lines_reach_log_and_context(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            session = app._term_manager().active
            app._on_pty_output(session, "hola\r\nmundo\r\n")
            await pilot.pause()
            log = app.query_one("#terminal-log", RichLog)
            assert log.lines
            history = app.context_manager._terminal_history
            assert "hola" in history and "mundo" in history

    async def test_markup_in_output_is_not_interpreted(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            session = app._term_manager().active
            # Would raise / render wrong if parsed as Rich markup.
            app._on_pty_output(session, "[bold]no-markup[/oops]\r\n")
            await pilot.pause()
            assert "[bold]no-markup[/oops]" in app.context_manager._terminal_history

    async def test_exit_marks_session_and_reports_code(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            session = app._term_manager().active
            session.backend = FakeBackend()
            app._on_pty_exit(session, 3)
            await pilot.pause()
            assert session.exited is True
            assert session.exit_code == 3
            assert "Exit code: 3" in app.context_manager._terminal_history


class TestProcessCleanup:
    async def test_shutdown_kills_all_backends(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_new_terminal()
            await pilot.pause()
            backends = []
            for session in app._terminal_manager.sessions:
                session.backend = FakeBackend()
                backends.append(session.backend)
            app._shutdown_terminals()
            assert all(backend.killed for backend in backends)
            assert all(
                session.backend is None for session in app._terminal_manager.sessions
            )

    async def test_backends_killed_on_app_unmount(self, fake_session, tmp_path):
        app = _make_app(fake_session, tmp_path)
        backend = FakeBackend()
        async with app.run_test(size=(120, 40)) as pilot:
            session = app._term_manager().active
            session.backend = backend
            await pilot.pause()
        # Leaving run_test unmounts the app → on_unmount → kill.
        assert backend.killed is True
