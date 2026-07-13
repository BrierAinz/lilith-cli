"""Splash screen shown when Lilith IDE starts."""

from __future__ import annotations

import asyncio

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

class SplashScreen(ModalScreen[None]):
    """A short-lived welcome splash with Yggdrasil ASCII art."""

    _YGGDRASIL_ART = r"""
                  ᛟ
                 /|\
                / | \
               /  |  \
              ᚦ   ᚾ   ᚢ
             /|         /|\
            / | \       / | \
           ᛒ  ᛋ  ᚷ     ᚠ  ᚢ  ᚦ
          /|             /|\
         / | \           / | \
        ᛁ  ᛊ  ᛏ         ᚲ  ᛈ  ᛚ  ᛗ

        ╔═══════════════════════════════════╗
        ║     LILITH  CLI  —  IDE           ║
        ║   Hlidskjalf Console · Yggdrasil  ║
        ╚═══════════════════════════════════╝

        [dim]Presiona cualquier tecla para despertar a Lilith…[/]
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cerrar"),
    ]

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
    }
    #splash-dialog {
        width: 60;
        height: 28;
        border: thick $accent 80%;
        background: $surface;
        padding: 1 2;
    }
    #splash-art {
        width: 100%;
        height: 1fr;
        content-align: center middle;
        color: $accent;
    }
    """

    def compose(self) -> None:
        with Vertical(id="splash-dialog"):
            yield Static(self._YGGDRASIL_ART, id="splash-art")

    def on_mount(self) -> None:
        """Auto-dismiss after a short delay so the user isn't blocked."""
        self.run_worker(self._auto_dismiss(), exclusive=False)

    async def _auto_dismiss(self) -> None:
        await asyncio.sleep(2.5)
        if self.is_current:
            self.dismiss()

    def on_key(self) -> None:
        """Any key press dismisses the splash."""
        self.dismiss()
