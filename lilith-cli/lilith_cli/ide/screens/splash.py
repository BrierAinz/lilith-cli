"""Splash screen shown when Lilith IDE starts."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class SplashScreen(ModalScreen[None]):
    """A short-lived Queen Orchestrator welcome splash."""

    _YGGDRASIL_ART = r"""

                    ᛚ
                 ╱  │  ╲
              ᚦ     │     ᚱ
                    │
             ╔══════════════════════════════╗
             ║          L I L I T H         ║
             ║      QUEEN ORCHESTRATOR      ║
             ╠══════════════════════════════╣
             ║  Mission Kernel · Court      ║
             ║  Skills · MCP · Longrun      ║
             ║  Sebas · Computer Use/Admin  ║
             ╚══════════════════════════════╝

             Ainz / Overlord · authority root
             [dim]Press any key to enter.[/]
    """

    BINDINGS: ClassVar[list[Binding]] = [
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
