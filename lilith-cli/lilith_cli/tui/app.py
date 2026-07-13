"""Main Yggdrasil TUI Dashboard application.

Provides a Textual-based terminal user interface with dark fantasy
theming for monitoring the nine realms and agents of Yggdrasil.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from lilith_cli.tui.agent_view import AgentMonitorView
from lilith_cli.tui.log_view import LogViewer
from lilith_cli.tui.realm_view import RealmStatusView


class YggdrasilDashboard(App[None]):
    """The main Yggdrasil TUI dashboard application.

    A dark fantasy-themed terminal dashboard for monitoring the nine
    realms and agents of the Yggdrasil ecosystem. Features a tabbed
    interface with Realms, Agents, and Logs views.

    Key Bindings:
        q       - Quit the application
        r       - Refresh all data
        1       - Switch to Realms tab
        2       - Switch to Agents tab
        3       - Switch to Logs tab
    """

    TITLE: str = "Yggdrasil - The Sacred Tree"

    CSS_PATH: str = "styles.tcss"

    BINDINGS: list[Binding] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("1", "switch_tab('realms')", "Realms", show=True, key_display="1"),
        Binding("2", "switch_tab('agents')", "Agents", show=True, key_display="2"),
        Binding("3", "switch_tab('logs')", "Logs", show=True, key_display="3"),
    ]

    def compose(self) -> ComposeResult:
        """Compose the dashboard layout.

        Yields:
            Widgets in order: Header, TabbedContainer (with 3 tabs), Footer.

        """
        yield Header(show_clock=True)

        with TabbedContent():
            with TabPane("Realms", id="realms"):
                yield RealmStatusView()
            with TabPane("Agents", id="agents"):
                yield AgentMonitorView()
            with TabPane("Logs", id="logs"):
                yield LogViewer()

        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to the specified tab.

        Args:
            tab_id: The ID of the tab to switch to
                ('realms', 'agents', or 'logs').

        """
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

        # Log the tab switch
        log = self.query_one(LogViewer)
        log.info(f"Switched to {tab_id.title()} tab")

    def action_refresh(self) -> None:
        """Refresh all dashboard data."""
        log = self.query_one(LogViewer)
        realm_view = self.query_one(RealmStatusView)
        agent_view = self.query_one(AgentMonitorView)

        realm_view._refresh_data()
        agent_view._refresh_data()
        log.info("Dashboard refreshed")


def main() -> None:
    """Entry point for the yggdrasil-tui console script.

    Creates and runs the YggdrasilDashboard application.
    """
    app = YggdrasilDashboard()
    app.run()


if __name__ == "__main__":
    main()
