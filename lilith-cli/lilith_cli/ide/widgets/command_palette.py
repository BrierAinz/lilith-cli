"""Synchronous palette rows: selection indexes always identify the same action."""
from __future__ import annotations

import dataclasses
import unicodedata
from typing import Any, Callable

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


@dataclasses.dataclass
class PaletteItem:
    label: str
    callback: Callable[[], Any]
    category: str = "Comandos"
    search_text: str = ""
    shortcut: str = ""
    disabled_reason: str = ""


def folded(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def matches(query: str, item: PaletteItem) -> bool:
    target = folded(f"{item.label} {item.category} {item.search_text} {item.shortcut}")
    query = folded(query).strip()
    if not query or all(word in target for word in query.split()):
        return True
    chars = iter(target)
    return all(char in chars for char in query)


class CommandPaletteScreen(ModalScreen[Callable[[], Any] | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cerrar")]
    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; }
    #palette-dialog { width: 90%; max-width: 100; height: 85%; background: $surface; border: round $primary; padding: 1; }
    #palette-results { height: 1fr; }
    #palette-hint { height: 2; color: $text-muted; }
    """

    def __init__(self, items: list[PaletteItem]) -> None:
        super().__init__()
        self._all_items = items
        self._filtered: list[PaletteItem] = []

    def compose(self):
        with Vertical(id="palette-dialog", classes="modal-dialog"):
            yield Static("LILITH · COMANDOS", classes="panel-title")
            yield Input(placeholder="Comando, archivo, sesión…", id="palette-input", classes="modal-input")
            yield Static("↑↓ elegir · Enter ejecutar · Esc volver", id="palette-hint")
            yield OptionList(id="palette-results", classes="modal-results")

    def on_mount(self) -> None:
        self.call_after_refresh(self._update_list, "")
        self.query_one("#palette-input", Input).focus()

    def _update_list(self, query: str) -> None:
        self._filtered = [item for item in self._all_items if matches(query, item)]
        if not self.is_mounted:
            return
        listing = self.query_one("#palette-results", OptionList)
        listing.clear_options()
        for index, item in enumerate(self._filtered):
            label = Text(item.label + "\n", style="bold")
            label.append(f"{item.category}  {item.disabled_reason or item.shortcut}", style="dim")
            listing.add_option(Option(label, id=str(index)))
        listing.highlighted = 0 if self._filtered else None
        self.query_one("#palette-hint", Static).update("↑↓ elegir · Enter ejecutar · Esc volver" if self._filtered else "No hay coincidencias. Cambia la búsqueda.")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            self._update_list(event.value)

    def on_key(self, event) -> None:
        if event.key in {"down", "up"} and self.focused is self.query_one("#palette-input", Input):
            listing = self.query_one("#palette-results", OptionList)
            if self._filtered:
                listing.highlighted = max(0, min(len(self._filtered)-1, (listing.highlighted or 0) + (1 if event.key == "down" else -1)))
            event.stop()
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self._select_current()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_index(event.option_index)

    def _select_current(self) -> None:
        self._select_index(self.query_one("#palette-results", OptionList).highlighted)

    def _select_index(self, index: int | None) -> None:
        if index is None or not 0 <= index < len(self._filtered):
            return
        item = self._filtered[index]
        if item.disabled_reason:
            self.notify(item.disabled_reason, severity="warning")
            return
        self.dismiss(item.callback)
