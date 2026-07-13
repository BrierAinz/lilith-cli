"""Command palette widget for the Lilith IDE."""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static


@dataclasses.dataclass
class PaletteItem:
    """A single entry in the command palette."""

    label: str
    callback: Callable[[], Any]
    category: str = "Comandos"
    search_text: str = ""


class CommandPaletteScreen(ModalScreen[Callable[[], Any] | None]):
    """Ctrl+Shift+P searchable list of IDE commands, files and artifacts.

    Items are grouped by category and filtered by label + search_text.
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, items: list[PaletteItem]) -> None:
        super().__init__()
        self._all_items = items
        self._filtered: list[PaletteItem] = []

    def compose(self):
        with Vertical(id="palette-dialog", classes="modal-dialog"):
            yield Static("Command Palette", classes="panel-title")
            yield Input(placeholder="Comando, archivo, runestone…", id="palette-input", classes="modal-input")
            yield ListView(id="palette-results", classes="modal-results")

    def on_mount(self) -> None:
        self._update_list("")
        self.query_one("#palette-input", Input).focus()

    def _update_list(self, query: str) -> None:
        query_lower = query.lower()
        if query_lower:
            self._filtered = [
                item
                for item in self._all_items
                if query_lower in item.label.lower()
                or query_lower in item.category.lower()
                or query_lower in item.search_text.lower()
            ]
        else:
            self._filtered = list(self._all_items)

        if not self.is_mounted:
            return
        list_view = self.query_one("#palette-results", ListView)
        list_view.clear()
        current_category: str | None = None
        for item in self._filtered:
            if item.category != current_category:
                current_category = item.category
                list_view.append(
                    ListItem(
                        Label(f"[dim]{current_category}[/]", classes="palette-category"),
                        disabled=True,
                    )
                )
            list_view.append(
                ListItem(Label(f"  {item.label}", classes="file-search-item"))
            )
        if list_view.children:
            # Skip disabled category headers when selecting.
            list_view.index = self._first_selectable_index(0)

    def _first_selectable_index(self, start: int) -> int | None:
        list_view = self.query_one("#palette-results", ListView)
        children = list(list_view.children)
        for i in range(start, len(children)):
            child = children[i]
            if not getattr(child, "disabled", False):
                return i
        return None

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            self._update_list(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self._select_current()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._select_index(event.list_view.index)

    def _select_current(self) -> None:
        list_view = self.query_one("#palette-results", ListView)
        self._select_index(list_view.index)

    def _select_index(self, index: int | None) -> None:
        list_view = self.query_one("#palette-results", ListView)
        if index is None or index < 0 or index >= len(self._filtered):
            self.dismiss(None)
            return
        self.dismiss(self._filtered[index].callback)
