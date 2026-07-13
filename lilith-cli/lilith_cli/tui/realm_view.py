"""Realm status view for the Yggdrasil TUI dashboard.

Displays the status, size, and file count of each of the nine realms
using a Rich Table rendered inside a Textual Static widget.
Auto-refreshes every 30 seconds.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.widgets import Static


# ── Constants ────────────────────────────────────────────────────

YGGDRASIL_ROOT: Path = Path(__file__).resolve().parents[4]

REALMS: list[str] = [
    "Asgard",
    "Vanaheim",
    "Alfheim",
    "Svartalfheim",
    "Muspelheim",
    "Helheim",
    "Niflheim",
    "Jotunheim",
    "Midgard",
]

REALM_COLORS: dict[str, str] = {
    "Asgard": "gold1",
    "Vanaheim": "green",
    "Alfheim": "cyan",
    "Svartalfheim": "magenta",
    "Muspelheim": "red",
    "Helheim": "dim",
    "Niflheim": "blue",
    "Jotunheim": "white",
    "Midgard": "green",
}


def _format_size(size_bytes: int) -> str:
    """Return a human-readable size string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Formatted size string (e.g. '2.3 GiB', '512 MiB').

    """
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0  # type: ignore[assignment]
    return f"{size_bytes:.1f} PiB"


def _realm_info(realm: str) -> dict[str, str | int]:
    """Gather filesystem info for a single realm.

    Args:
        realm: Name of the realm directory.

    Returns:
        Dictionary with keys: name, status, size (bytes), files.

    """
    realm_path = YGGDRASIL_ROOT / realm
    if not realm_path.is_dir():
        return {
            "name": realm,
            "status": "❌ Missing",
            "size": 0,
            "files": 0,
        }

    total_size: int = 0
    file_count: int = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(realm_path):
            for filename in filenames:
                filepath = Path(dirpath) / filename
                try:
                    total_size += filepath.stat().st_size
                    file_count += 1
                except OSError:
                    pass
    except PermissionError:
        pass

    return {
        "name": realm,
        "status": "✅ Active" if file_count > 0 else "⚠️  Empty",
        "size": total_size,
        "files": file_count,
    }


class RealmStatusView(Static):
    """A widget that displays realm status as a Rich Table.

    Auto-refreshes realm data every 30 seconds via ``set_interval``.
    """

    REFRESH_INTERVAL: ClassVar[int] = 30  # seconds

    def __init__(
        self,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the RealmStatusView.

        Args:
            id: Optional widget ID.
            classes: Optional CSS classes.

        """
        super().__init__(id=id, classes=classes or "realm-view")
        self._last_refresh: datetime | None = None

    def compose(self) -> None:
        """Build initial content."""
        self._render_table()

    def on_mount(self) -> None:
        """Set up the auto-refresh interval on mount."""
        self.set_interval(self.REFRESH_INTERVAL, self._refresh_data)

    def _refresh_data(self) -> None:
        """Refresh realm data and re-render the table."""
        self._render_table()

    def _render_table(self) -> None:
        """Render the realm status as a Rich Table and update the widget."""
        table = Table(
            title="🌿 Nine Realms of Yggdrasil",
            title_style="bold gold1",
            show_header=True,
            header_style="bold gold1",
            border_style="gold1",
            title_justify="center",
            expand=True,
        )
        table.add_column("Realm", style="bold", min_width=16)
        table.add_column("Status", justify="center", min_width=12)
        table.add_column("Size", justify="right", min_width=12)
        table.add_column("Files", justify="right", min_width=8)

        for realm in REALMS:
            info = _realm_info(realm)
            color = REALM_COLORS.get(realm, "white")
            realm_text = Text(str(info["name"]), style=color)

            size_bytes = info["size"]
            size_str = (
                _format_size(int(size_bytes)) if isinstance(size_bytes, int) else str(size_bytes)
            )
            file_count = str(info["files"])

            table.add_row(
                realm_text,
                str(info["status"]),
                size_str,
                file_count,
            )

        self._last_refresh = datetime.now(tz=UTC)
        refresh_note = f"Last refreshed: {self._last_refresh.strftime('%H:%M:%S')}"
        table.footer = refresh_note

        self.update(table)
