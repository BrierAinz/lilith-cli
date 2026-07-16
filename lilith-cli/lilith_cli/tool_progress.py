"""Rich live-updating tool progress panel for parallel tool execution."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from .render import console


def _format_duration(elapsed: float) -> str:
    """Return a compact human-readable duration (Spanish labels)."""
    if elapsed < 1:
        return f"{elapsed * 1000:.0f}ms"
    if elapsed < 60:
        return f"{elapsed:.1f}s"
    mins, secs = divmod(int(elapsed), 60)
    return f"{mins}m {secs}s"


def _build_tool_progress_renderable(
    running: list[str],
    completed: list[tuple[str, float]],
    failed: list[tuple[str, str]],
    start_time: float,
) -> Panel:
    """Build a Rich renderable for the live tool progress panel.

    Parameters
    ----------
    running:
        Names of tools currently executing.
    completed:
        Tuples of (tool_name, duration_seconds) for finished tools.
    failed:
        Tuples of (tool_name, error_message) for failed tools.
    start_time:
        ``time.perf_counter()`` when the first tool started.

    Returns
    -------
    A Rich Panel with a spinner, running/completed/failed tool lists.
    """
    now = time.perf_counter()
    lines: list[Any] = []

    # Header: spinner + running count while active, plain summary when done.
    header_text = Text()
    if running:
        header_text.append(
            f"Ejecutando {len(running)} herramienta(s) en paralelo", style="bold cyan"
        )
        header_text.append(f": {', '.join(running)}", style="italic")
        lines.append(Group(Spinner("dots", style="cyan"), header_text))
    else:
        done = len(completed) + len(failed)
        header_text.append(f"{done} herramienta(s) ejecutada(s)", style="bold cyan")
        lines.append(header_text)

    # Running tools with elapsed time.
    if running:
        for name in running:
            elapsed = now - start_time
            line = Text()
            line.append("  ⦿ ", style="cyan")
            line.append(name, style="tool.name")
            line.append(f"  ({_format_duration(elapsed)})", style="dim")
            lines.append(line)

    # Completed tools with checkmark and duration.
    if completed:
        for name, duration in completed:
            line = Text()
            line.append("  ✓ ", style="green")
            line.append(name, style="tool.name")
            line.append(f"  ({_format_duration(duration)})", style="dim")
            lines.append(line)

    # Failed tools with X and error.
    if failed:
        for name, error in failed:
            line = Text()
            line.append("  ✗ ", style="red")
            line.append(name, style="tool.name")
            line.append(f"  {error}", style="dim red")
            lines.append(line)

    return Panel(
        Group(*lines),
        title="[bold]Progreso de herramientas[/]",
        border_style="cyan",
        expand=False,
        padding=(0, 1),
    )


class ToolProgressTracker:
    """Tracks running/completed/failed tools and drives a Rich Live panel.

    Usage::

        tracker = ToolProgressTracker()
        with tracker:
            tracker.start("read_file")
            ...
            tracker.complete("read_file")
            tracker.render_summary()

    The panel updates automatically on each state change.
    """

    def __init__(self) -> None:
        self.running: dict[str, float] = {}
        self.completed: list[tuple[str, float]] = []
        self.failed: list[tuple[str, str]] = []
        self._start_time: float = 0.0
        self._live: Live | None = None

    def start(self, name: str) -> None:
        """Mark a tool as running."""
        now = time.perf_counter()
        if not self.running:
            self._start_time = now
        self.running[name] = now
        self._ensure_live()
        self._refresh()

    def complete(self, name: str, *, error: str | None = None) -> None:
        """Move a tool from running to completed or failed."""
        start = self.running.pop(name, time.perf_counter())
        duration = time.perf_counter() - start
        if error:
            self.failed.append((name, error))
        else:
            self.completed.append((name, duration))
        self._refresh()

    def is_active(self) -> bool:
        """Return True if any tool has been tracked this turn."""
        return bool(self.running or self.completed or self.failed)

    def total_duration(self) -> float:
        """Return elapsed time since the first tool started."""
        if not self._start_time:
            return 0.0
        return time.perf_counter() - self._start_time

    def _refresh(self) -> None:
        """Update the Live panel if it is active."""
        if self._live is not live_none:
            self._live.update(
                _build_tool_progress_renderable(
                    list(self.running.keys()),
                    self.completed,
                    self.failed,
                    self._start_time,
                )
            )

    def render_summary(self) -> None:
        """Print a final summary line of tool execution."""
        total = len(self.completed) + len(self.failed)
        if total == 0:
            return
        duration = self.total_duration()
        status = "✓" if not self.failed else "✗"
        console.print(
            f"[status.ok]{status} {total} herramienta(s) ejecutada(s) en {_format_duration(duration)}[/]"
        )

    def pause_live(self) -> None:
        """Close the Live panel without resetting counters.  Rich allows
        only one active Live at a time, so the REPL pauses the tracker
        before opening its streaming display; the panel reopens
        automatically on the next ``start()``.  The panel is transient, so
        pausing erases it instead of stacking a cumulative copy in the
        scrollback on every stream/tool alternation; ``render_summary()``
        prints the one persistent line at end of turn.
        """
        if self._live is not live_none:
            self._live.__exit__(None, None, None)
            self._live = None

    def _ensure_live(self) -> None:
        """Create and start the Live panel on first use."""
        if self._live is live_none:
            self._live = Live(
                _build_tool_progress_renderable(
                    list(self.running.keys()),
                    self.completed,
                    self.failed,
                    self._start_time,
                ),
                console=console,
                refresh_per_second=12,
                transient=True,
            )
            self._live.__enter__()

    def __enter__(self) -> "ToolProgressTracker":
        # Lazy: the Live panel is only created when the first tool starts,
        # so turns without tool calls render no empty progress box.
        return self

    def __exit__(self, *args: object) -> None:
        if self._live is not live_none:
            self._live.__exit__(*args)
            self._live = None


live_none: Live | None = None


def render_tool_progress(
    running: list[str],
    completed: list[str],
    failed: list[str],
) -> Live:
    """Create a live-updating Rich panel showing tool execution progress.

    Parameters
    ----------
    running:
        Names of tools currently executing.
    completed:
        Names of tools that completed successfully.
    failed:
        Names of tools that failed.

    Returns
    -------
    A started Rich ``Live`` instance. The caller must call ``update()`` on it
    with new lists and ``stop()`` when finished.

    Note
    ----
    For full lifecycle management (start/complete/summary) prefer
    ``ToolProgressTracker``.
    """
    completed_with_durations: list[tuple[str, float]] = [(name, 0.0) for name in completed]
    failed_with_messages: list[tuple[str, str]] = [(name, "") for name in failed]
    panel = _build_tool_progress_renderable(
        running, completed_with_durations, failed_with_messages, time.perf_counter()
    )
    live = Live(panel, console=console, refresh_per_second=12, vertical_overflow="visible")
    live.__enter__()
    return live



# ── Delegation streaming panel (tanda 6, ITEM 3) ──────────────────────


class DelegationStreamBuffer:
    """Thread-safe buffer of streaming chunks emitted by a delegate run.

    ``delegate_subagent`` runs on a worker thread via
    ``asyncio.to_thread``. To display live output in the REPL without
    invasively coupling to the tool execution flow, the tool may push
    chunks here (from the worker thread) and the REPL loop polls the
    buffer on its own cadence.

    The buffer is intentionally simple — a deque of recent lines plus a
    few fields the panel needs (preset, model, current turn, last tool,
    state). Pushes are O(1); reads are O(1) on the latest snapshot.
    """

    __slots__ = (
        "preset",
        "model",
        "agentic",
        "lines",
        "current_turn",
        "last_tool",
        "last_status",
        "started_at",
        "finished_at",
        "final_error",
    )

    def __init__(self, preset: str, model: str, agentic: bool) -> None:
        self.preset = preset
        self.model = model
        self.agentic = agentic
        # Bounded tail — keep the last 12 lines so a long delegation
        # does not flood the live frame (Rich would duplicate overflow
        # on every refresh).
        from collections import deque

        self.lines: deque[str] = deque(maxlen=12)
        self.current_turn: int = 0
        self.last_tool: str | None = None
        self.last_status: str | None = None
        import time

        self.started_at: float = time.perf_counter()
        self.finished_at: float | None = None
        self.final_error: str | None = None

    def push_line(self, line: str) -> None:
        """Append a streaming chunk (caller is the worker thread)."""
        if not line:
            return
        self.lines.append(line)

    def push_turn(self, turn: int, tool: str | None, status: str | None) -> None:
        """Record the latest agentic turn state (tool name + ok/error)."""
        self.current_turn = int(turn)
        if tool is not None:
            self.last_tool = tool
        if status is not None:
            self.last_status = status

    def finish(self, error: str | None = None) -> None:
        """Mark the delegation as finished. Idempotent on the timestamp:
        the first call wins; subsequent calls only update ``final_error``
        when one was not already recorded, so a late cleanup cannot
        overwrite the result that arrived via ``tool_result``.
        """
        import time

        if self.finished_at is None:
            self.finished_at = time.perf_counter()
        if error and self.final_error is None:
            self.final_error = error

    def is_finished(self) -> bool:
        return self.finished_at is not None

    def elapsed(self) -> float:
        import time

        end = self.finished_at if self.finished_at is not None else time.perf_counter()
        return end - self.started_at

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot the panel can render."""
        return {
            "preset": self.preset,
            "model": self.model,
            "agentic": self.agentic,
            "lines": list(self.lines),
            "current_turn": self.current_turn,
            "last_tool": self.last_tool,
            "last_status": self.last_status,
            "elapsed": self.elapsed(),
            "finished": self.is_finished(),
            "error": self.final_error,
        }


class DelegationLive:
    """Live panel that tails a ``DelegationStreamBuffer``.

    Why a separate class instead of reusing ``ToolProgressTracker``:
    the tool progress tracker is keyed on tool names and fires on
    start/complete pairs; the delegation needs a *tail* of streamed
    content plus a longer-lived status line. Trying to overload
    ToolProgressTracker would entangle two concerns.

    Lifecycle::

        buf = DelegationStreamBuffer(preset, model, agentic)
        with DelegationLive(buf) as live:
            ... tool runs in a thread, pushing into buf ...
            live.refresh()  # pull latest from buf into the frame
        # final frame erased (transient=True); summary printed on close

    Falls back to no-op when stdout is not a TTY (CI, redirected
    output, tests) — the buffer still updates, but nothing is
    rendered, so tests don't need to mock Rich.
    """

    def __init__(self, buffer: DelegationStreamBuffer) -> None:
        self._buffer = buffer
        self._live: "Live | None" = None
        self._enabled = self._should_enable()

    @staticmethod
    def _should_enable() -> bool:
        try:
            import sys

            return bool(sys.stdout.isatty())
        except Exception:  # pragma: no cover - defensive
            return False

    def _build_renderable(self) -> "object":
        from rich.console import Group
        from rich.panel import Panel
        from rich.spinner import Spinner
        from rich.text import Text

        snap = self._buffer.snapshot()
        lines: list[object] = []
        if snap["finished"]:
            err = snap.get("error")
            if err:
                header_text = Text()
                header_text.append("✗ ", style="red")
                header_text.append(
                    f"delegate {snap['preset']} falló", style="bold red"
                )
                header = header_text
            else:
                header_text = Text()
                header_text.append("✓ ", style="green")
                header_text.append(
                    f"delegate {snap['preset']} ok", style="bold green"
                )
                header = header_text
        else:
            header_text = Text()
            header_text.append("  delegando → ", style="bold cyan")
            header_text.append(str(snap["preset"]), style="tool.name")
            header_text.append(f"  ({snap['model']})", style="dim")
            if snap["agentic"]:
                header_text.append("  agentic", style="italic dim")
            header = Group(Spinner("dots", style="cyan"), header_text)
        lines.append(header)

        # Body: tail of streamed lines (skip if empty + finished).
        body_lines = list(snap["lines"])  # type: ignore[arg-type]
        if body_lines:
            body_text = Text()
            for ln in body_lines:
                body_text.append(ln, style="dim")
                body_text.append("\n")
            lines.append(body_text)

        # Agentic status footer.
        if snap["agentic"]:
            foot = Text()
            turn = snap["current_turn"]
            tool = snap["last_tool"] or "—"
            status = snap["last_status"] or "running"
            foot.append(f"turno {turn}  tool={tool}  estado={status}", style="dim")
            lines.append(foot)

        # Elapsed footer.
        elapsed = snap["elapsed"]
        if isinstance(elapsed, float):
            if elapsed < 1:
                elapsed_s = f"{elapsed * 1000:.0f}ms"
            elif elapsed < 60:
                elapsed_s = f"{elapsed:.1f}s"
            else:
                mins, secs = divmod(int(elapsed), 60)
                elapsed_s = f"{mins}m {secs}s"
            lines.append(Text(f"⏱  {elapsed_s}", style="dim"))

        title = f"[bold]Delegación: {snap['preset']}[/]"
        return Panel(
            Group(*lines),
            title=title,
            border_style="cyan",
            expand=False,
            padding=(0, 1),
        )

    def refresh(self) -> None:
        """Pull the latest snapshot into the Live frame."""
        if not self._enabled or self._live is None:
            return
        self._live.update(self._build_renderable())

    def __enter__(self) -> "DelegationLive":
        if self._enabled:
            self._live = Live(
                self._build_renderable(),
                console=console,
                refresh_per_second=8,
                transient=True,
            )
            self._live.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        # Refresh once with the final state before closing so the user
        # sees ok/error + last lines, then the transient frame erases.
        if self._enabled and self._live is not None:
            try:
                self._live.update(self._build_renderable())
            except Exception:
                pass
            self._live.__exit__(None, None, None)
            self._live = None


# ── Module-level helper exposed to the agent loop ─────────────────────


def make_delegation_buffer(preset: str, model: str, agentic: bool) -> DelegationStreamBuffer:
    """Construct the thread-safe buffer a ``delegate_subagent`` run can
    push into. The REPL owns the corresponding ``DelegationLive`` and
    polls it on each turn.

    Returns a fresh buffer; callers are responsible for handing it to
    the tool via a thread-local / contextvar hook (left for the actual
    agentic integration to wire up — see repl.py for the polling side).
    """
    return DelegationStreamBuffer(preset=preset, model=model, agentic=agentic)
