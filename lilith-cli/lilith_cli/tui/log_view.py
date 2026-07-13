"""Log viewer widget for the Yggdrasil TUI dashboard.

Provides a RichLog-based log viewer with color-coded log levels
(info, warning, error) and automatic scrolling.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rich.text import Text
from textual.widgets import RichLog


class LogViewer(RichLog):
    """A log viewer widget that displays timestamped, color-coded entries.

    Extends Textual's RichLog with convenience methods for adding
    log entries at different severity levels (info, warning, error).
    Auto-scrolls to the bottom on each new entry.
    """

    def __init__(
        self,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the LogViewer.

        Args:
            id: Optional widget ID.
            classes: Optional CSS classes.

        """
        super().__init__(
            id=id,
            classes=classes or "log-view",
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=True,
        )

    def _timestamp(self) -> str:
        """Return the current timestamp as a formatted string.

        Returns:
            Timestamp string in HH:MM:SS format.

        """
        return datetime.now(tz=UTC).strftime("%H:%M:%S")

    def info(self, message: str) -> None:
        """Log an informational message.

        Args:
            message: The info message to log.

        """
        ts = self._timestamp()
        self.write(
            Text(f"[{ts}] ", style="dim") + Text("ℹ️  ", style="cyan") + Text(message, style="cyan"),
        )

    def warning(self, message: str) -> None:
        """Log a warning message.

        Args:
            message: The warning message to log.

        """
        ts = self._timestamp()
        self.write(
            Text(f"[{ts}] ", style="dim")
            + Text("⚠️  ", style="yellow")
            + Text(message, style="yellow"),
        )

    def error(self, message: str) -> None:
        """Log an error message.

        Args:
            message: The error message to log.

        """
        ts = self._timestamp()
        self.write(
            Text(f"[{ts}] ", style="dim")
            + Text("❌ ", style="bold red")
            + Text(message, style="red"),
        )

    def on_mount(self) -> None:
        """Display a welcome message when the log viewer is mounted."""
        self.info("Yggdrasil Log Viewer initialized")
        self.info("The Sacred Tree watches over all realms...")
