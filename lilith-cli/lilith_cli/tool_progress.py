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
