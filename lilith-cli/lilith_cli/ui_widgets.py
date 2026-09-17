"""Keyboard-first widgets shared by the Hoguera and terminal IDE."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, RichLog, Static, TextArea

from .ui_quality import safe_display


class MessageInput(TextArea):
    """Enter inserts a newline. Ctrl+Enter sends; the button works on all terminals."""
    BINDINGS = [Binding("ctrl+enter", "submit", "Enviar", priority=True)]

    class Submitted(Message):
        pass

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.load_text(text)

    def action_submit(self) -> None:
        self.post_message(self.Submitted())


class FollowLog(RichLog):
    """Follow only while the reader is already at the bottom; never force a jump."""
    unread = reactive(0)

    class NewContent(Message):
        def __init__(self, log: FollowLog) -> None:
            self.log = log
            super().__init__()

    def __init__(self, *args, **kwargs):
        kwargs.update(auto_scroll=False, max_lines=4000, min_width=1)
        super().__init__(*args, **kwargs)

    def write(self, content, width=None, expand=False, shrink=True, scroll_end=None, animate=False):
        following = self.is_vertical_scroll_end
        result = super().write(content, width=width, expand=expand, shrink=shrink,
                               scroll_end=False, animate=False)
        if following:
            self.scroll_end(animate=False, immediate=True, x_axis=False)
        if not following:
            self.unread += 1
            self.post_message(self.NewContent(self))
        return result

    def latest(self) -> None:
        self.unread = 0
        self.scroll_end(animate=False, immediate=True)
        self.post_message(self.NewContent(self))

    def clear(self):
        self.unread = 0
        return super().clear()


class ToolDetailsScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Volver")]
    DEFAULT_CSS = """
    ToolDetailsScreen { align: center middle; }
    #tool-details { width: 94%; height: 90%; background: $surface; border: round $primary; padding: 1; }
    #tool-events { height: 1fr; }
    #tool-details Button { height: 3; }
    #tool-events Collapsible { height: auto; }
    """

    def __init__(self, events: list[dict]):
        super().__init__()
        self.events = list(events[-100:])

    def compose(self) -> ComposeResult:
        with Vertical(id="tool-details"):
            yield Static("ACTIVIDAD · detalles disponibles en esta sesión", classes="panel-title")
            with VerticalScroll(id="tool-events"):
                if not self.events:
                    yield Static("Todavía no se han recibido eventos de herramientas.")
                for event in self.events:
                    title = f"{event['name']} · {event['state']}"
                    with Collapsible(title=safe_display(title, 180), collapsed=True):
                        yield Static(Text(safe_display(event.get("detail", "Sin detalles"))))
            yield Button("Volver", id="tools-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tools-close":
            self.dismiss()


class InspectorScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Volver")]
    DEFAULT_CSS = """
    InspectorScreen { align: center middle; }
    #execution-inspector { width: 94%; max-width: 110; height: 80%; background: $surface; border: round $primary; padding: 1 2; }
    #inspector-scroll { height: 1fr; }
    #inspector-text { height: auto; }
    """

    def __init__(self, render_state):
        super().__init__()
        self.render_state = render_state

    def compose(self) -> ComposeResult:
        with Vertical(id="execution-inspector"):
            with VerticalScroll(id="inspector-scroll"):
                yield Static(Text(self.render_state()), id="inspector-text")
            yield Button("Volver", id="inspector-close")

    def on_mount(self) -> None:
        self.set_interval(1, self._update)

    def _update(self) -> None:
        if self.is_current:
            self.query_one("#inspector-text", Static).update(Text(self.render_state()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "inspector-close":
            self.dismiss()
