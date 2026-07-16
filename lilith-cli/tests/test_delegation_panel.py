"""Tests for the delegation streaming panel (tanda 6, ITEM 3).

The panel is split into two pieces:

- ``DelegationStreamBuffer`` — thread-safe buffer the worker thread
  pushes into. Pure data, no Rich dependency, easy to assert on.
- ``DelegationLive`` — Rich Live wrapper that tails the buffer and
  renders a transient frame. Skips rendering when stdout is not a
  TTY (CI, redirected output) so the no-op path is also covered.

These tests never reach the network: they assert on the buffer
state and on the rendered Group/Panel structure (without actually
drawing it). The REPL wiring (open on tool_call, close on
tool_result) is exercised by the existing tool progress suite.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

# Ensure lilith_cli is importable when running tests directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)


# ── Buffer ────────────────────────────────────────────────────────────


def test_buffer_starts_with_no_lines():
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    snap = buf.snapshot()
    assert snap["preset"] == "p"
    assert snap["model"] == "m"
    assert snap["agentic"] is False
    assert snap["lines"] == []
    assert snap["current_turn"] == 0
    assert snap["last_tool"] is None
    assert snap["last_status"] is None
    assert snap["finished"] is False
    assert snap["error"] is None
    assert snap["elapsed"] >= 0.0


def test_buffer_push_line_appends():
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    buf.push_line("hello")
    buf.push_line("world")
    assert buf.snapshot()["lines"] == ["hello", "world"]


def test_buffer_push_line_bounded_to_maxlen():
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    for i in range(50):
        buf.push_line(f"line-{i}")
    snap = buf.snapshot()
    assert len(snap["lines"]) == 12  # deque(maxlen=12)
    assert snap["lines"][0] == "line-38"
    assert snap["lines"][-1] == "line-49"


def test_buffer_push_line_ignores_empty():
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    buf.push_line("")
    buf.push_line(None)  # type: ignore[arg-type]
    assert buf.snapshot()["lines"] == []


def test_buffer_push_turn_updates_fields():
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=True)
    buf.push_turn(1, "file_read", "ok")
    snap = buf.snapshot()
    assert snap["current_turn"] == 1
    assert snap["last_tool"] == "file_read"
    assert snap["last_status"] == "ok"


def test_buffer_finish_is_idempotent():
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    # First call with error wins.
    buf.finish(error="auth failed: 401")
    first_finished = buf.snapshot()["finished"]
    first_error = buf.snapshot()["error"]
    # Second call (cleanup) must NOT clobber the first error.
    buf.finish(error="late cleanup error")
    second_snap = buf.snapshot()
    assert first_finished is True
    assert second_snap["finished"] is True
    assert first_error == "auth failed: 401"
    assert second_snap["error"] == "auth failed: 401"  # late cleanup ignored

    # Subsequent finish() with no error also does not clear the error.
    buf2 = DelegationStreamBuffer(preset="p2", model="m", agentic=False)
    buf2.finish(error="boom")
    buf2.finish()
    assert buf2.snapshot()["error"] == "boom"


def test_buffer_thread_safe_under_concurrent_push():
    """Multiple threads pushing lines concurrently must not corrupt the
    deque; final snapshot must have exactly N items (bounded by maxlen)."""
    from lilith_cli.tool_progress import DelegationStreamBuffer

    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    n_threads = 8
    per_thread = 50

    def worker(offset: int) -> None:
        for i in range(per_thread):
            buf.push_line(f"t{offset}-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = buf.snapshot()
    assert len(snap["lines"]) == 12  # maxlen
    # Every line must be one of the well-formed values (no torn writes).
    for ln in snap["lines"]:
        assert ln.startswith("t")


# ── DelegationLive (no-op path) ───────────────────────────────────────


def test_delegation_live_noop_when_not_tty(monkeypatch):
    """When stdout is not a TTY (the test harness), DelegationLive is a
    no-op: __enter__/__exit__/refresh are safe and the buffer still
    accumulates."""
    import sys

    from lilith_cli.tool_progress import DelegationLive, DelegationStreamBuffer

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
    live = DelegationLive(buf)
    with live as entered:
        # Live is a no-op but the protocol still works.
        assert entered is live
        buf.push_line("hello")
        entered.refresh()  # must not raise
    # Snapshot reflects what was pushed, regardless of the TTY check.
    assert buf.snapshot()["lines"] == ["hello"]


def test_delegation_live_build_renderable_no_tty(monkeypatch):
    """Even with a real TTY (the default), build_renderable should
    return a Rich Panel without actually starting a Live. This is
    useful for snapshot-style tests of the layout."""
    import sys

    from rich.panel import Panel

    from lilith_cli.tool_progress import DelegationLive, DelegationStreamBuffer

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    buf = DelegationStreamBuffer(preset="ejecutor-kimi", model="kimi-k2", agentic=True)
    buf.push_line("reading files...")
    buf.push_turn(3, "file_write", "ok")
    live = DelegationLive(buf)
    renderable = live._build_renderable()
    assert isinstance(renderable, Panel)
    # The title is "[bold]Delegación: ejecutor-kimi[/]".
    rendered_str = str(renderable.title)
    assert "Delegación" in rendered_str
    assert "ejecutor-kimi" in rendered_str


def _render_to_text(renderable) -> str:
    """Render a Rich renderable to a plain string for assertions."""
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, force_terminal=False, width=120).print(renderable)
    return buf.getvalue()


def test_delegation_live_finished_renders_checkmark():
    """When the buffer is finished, the panel header shows ok/error
    instead of the spinner state."""
    import sys

    from lilith_cli.tool_progress import DelegationLive, DelegationStreamBuffer

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    try:
        buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
        buf.push_line("ok done")
        buf.finish()
        live = DelegationLive(buf)
        rendered_str = _render_to_text(live._build_renderable())
        assert "delegate p ok" in rendered_str
        assert "falló" not in rendered_str
    finally:
        monkeypatch.undo()


def test_delegation_live_error_renders_cross():
    import sys

    from lilith_cli.tool_progress import DelegationLive, DelegationStreamBuffer

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    try:
        buf = DelegationStreamBuffer(preset="p", model="m", agentic=False)
        buf.finish(error="auth failed: 401")
        live = DelegationLive(buf)
        rendered_str = _render_to_text(live._build_renderable())
        assert "✗" in rendered_str
        assert "falló" in rendered_str
    finally:
        monkeypatch.undo()


def test_make_delegation_buffer_helper():
    from lilith_cli.tool_progress import DelegationStreamBuffer, make_delegation_buffer

    buf = make_delegation_buffer(preset="x", model="y", agentic=False)
    assert isinstance(buf, DelegationStreamBuffer)
    assert buf.preset == "x"
    assert buf.model == "y"