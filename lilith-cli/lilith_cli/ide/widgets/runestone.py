"""Runestone placeholder widget for the Lilith IDE."""

from __future__ import annotations

from textual.widgets import Static


class RunestoneWidget(Static):
    """Placeholder widget for runestone/artifact display."""

    def __init__(self, label: str = "Runestone") -> None:
        super().__init__(label)
