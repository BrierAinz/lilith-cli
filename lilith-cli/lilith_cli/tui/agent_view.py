"""Agent monitor view for the Yggdrasil TUI dashboard.

Displays agent status information with placeholder data.
Will be connected to the lilith-api backend in the future.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.widgets import Static


# ── Placeholder Agent Data ───────────────────────────────────────

PLACEHOLDER_AGENTS: list[dict[str, str]] = [
    {
        "name": "Lilith",
        "status": "🟢 Online",
        "last_activity": "2 minutes ago",
    },
    {
        "name": "Odin",
        "status": "🟢 Online",
        "last_activity": "5 minutes ago",
    },
    {
        "name": "Eva",
        "status": "🟡 Idle",
        "last_activity": "1 hour ago",
    },
    {
        "name": "Shalltear",
        "status": "🔴 Offline",
        "last_activity": "3 hours ago",
    },
]

AGENT_COLORS: dict[str, str] = {
    "Lilith": "gold1",
    "Odin": "cyan",
    "Eva": "green",
    "Shalltear": "magenta",
}


class AgentMonitorView(Static):
    """A widget that displays agent status as a Rich Table.

    Currently shows placeholder data; will be connected to lilith-api
    for real-time agent monitoring in the future.
    """

    REFRESH_INTERVAL: ClassVar[int] = 30  # seconds

    def __init__(
        self,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the AgentMonitorView.

        Args:
            id: Optional widget ID.
            classes: Optional CSS classes.

        """
        super().__init__(id=id, classes=classes or "agent-view")
        self._last_refresh: datetime | None = None

    def compose(self) -> None:
        """Build initial content."""
        self._render_table()

    def on_mount(self) -> None:
        """Set up the auto-refresh interval on mount."""
        self.set_interval(self.REFRESH_INTERVAL, self._refresh_data)

    def _refresh_data(self) -> None:
        """Refresh agent data and re-render the table.

        Note: Currently uses placeholder data. This method will be
        updated to fetch from lilith-api when available.
        """
        self._render_table()

    def _render_table(self) -> None:
        """Render the agent status as a Rich Table and update the widget."""
        table = Table(
            title="⚔️  Agent Monitor",
            title_style="bold gold1",
            show_header=True,
            header_style="bold gold1",
            border_style="gold1",
            title_justify="center",
            expand=True,
        )
        table.add_column("Agent", style="bold", min_width=14)
        table.add_column("Status", justify="center", min_width=14)
        table.add_column("Last Activity", justify="center", min_width=18)

        for agent in PLACEHOLDER_AGENTS:
            name = agent["name"]
            color = AGENT_COLORS.get(name, "white")
            name_text = Text(name, style=color)

            status_str = agent["status"]
            if "Online" in status_str:
                status_text = Text(status_str, style="green")
            elif "Idle" in status_str:
                status_text = Text(status_str, style="yellow")
            else:
                status_text = Text(status_str, style="red")

            table.add_row(
                name_text,
                status_text,
                agent["last_activity"],
            )

        self._last_refresh = datetime.now(tz=UTC)
        table.footer = (
            f"[dim]Last refreshed: "
            f"{self._last_refresh.strftime('%H:%M:%S')} — Placeholder data[/dim]"
        )

        self.update(table)
