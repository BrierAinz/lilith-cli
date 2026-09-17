"""Read-only saved collaborator references in the Hoguera."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import ClassVar

from lilith_tools.cli_job_inspect import CliJobInspectTool
from lilith_tools.cli_job_journal import CliJobJournal
from rich.text import Text
from textual import on, work
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option


class CollaboratorScreen(Screen):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "back", "Volver"),
        ("f5", "refresh", "Actualizar"),
    ]
    CSS = """
    CollaboratorScreen { background: #0b1016; color: #dce3e8; }
    #collab-panel { margin: 1 2; padding: 1; border: round #8fd8e8; background: #111820; }
    #collab-title { color: #d5b96d; height: 2; }
    #collab-scope { height: 3; color: #9ba9b5; }
    #collab-list { height: 1fr; }
    #collab-detail { height: 7; padding: 1; }
    #collab-back { height: 3; }
    """

    def compose(self):
        with Vertical(id="collab-panel"):
            yield Label("COLABORADORES · REFERENCIAS GUARDADAS", id="collab-title")
            yield Static(
                "Últimas 20 referencias globales, no filtradas por proyecto.\n"
                "Enter consulta el marcador. No relanza, cancela ni verifica el trabajo.",
                id="collab-scope",
            )
            yield OptionList(id="collab-list")
            yield Static("Cargando referencias…", id="collab-detail")
            yield Button("Volver · Esc", id="collab-back")

    def on_mount(self):
        self.action_refresh()

    @work(exclusive=True, group="collab-read")
    async def action_refresh(self):
        listing = self.query_one("#collab-list", OptionList)
        listing.clear_options()
        try:
            rows = await asyncio.to_thread(CliJobJournal().recent)
        except (OSError, ValueError, sqlite3.Error):
            self.query_one("#collab-detail", Static).update(
                "Registro no disponible o dañado. No se ejecutó nada."
            )
            return
        for row in rows:
            observation = row["observation"] or {}
            label = Text(f"{row['agent']} · {row['reference']}\n", style="bold")
            label.append(
                f"{observation.get('status') or 'sin observación'} · {row['created_at']}"
            )
            listing.add_option(Option(label, id=row["reference"]))
        self.query_one("#collab-detail", Static).update(
            "Selecciona una referencia y pulsa Enter para consultar su marcador actual."
            if rows
            else "No hay referencias guardadas. Esta vista no inicia colaboradores."
        )
        listing.focus()

    @on(OptionList.OptionSelected, "#collab-list")
    def select_reference(self, event):
        self.inspect_reference(str(event.option.id))

    @work(exclusive=True, group="collab-read")
    async def inspect_reference(self, reference: str):
        detail = self.query_one("#collab-detail", Static)
        detail.update("Consultando marcador, sin ejecutar trabajo…")
        try:
            saved = await asyncio.to_thread(CliJobJournal().lookup, reference)
            if saved is None or saved["job_id"] is None:
                detail.update(
                    "Referencia sin ID de worker registrado. Requiere reconciliación; no se reintentó."
                )
                return
            result = await asyncio.to_thread(
                CliJobInspectTool().execute,
                agent=saved["agent"],
                job_id=saved["job_id"],
            )
        except (OSError, ValueError, sqlite3.Error):
            detail.update("No se pudo leer la referencia. No se ejecutó nada.")
            return
        labels = {
            "reported_success": "Salida 0 reportada; falta revisar el trabajo.",
            "reported_failure": "El colaborador reportó un fallo.",
            "unknown": "Sin marcador. No sabemos si sigue activo o terminó.",
            "root_unavailable": "Directorio del colaborador no disponible.",
            "unreadable": "Marcador ilegible o inválido.",
        }
        cleanup = result.data.get("cleanup_status")
        cleanup_note = (
            "\nReserva pendiente: requiere recuperación manual."
            if cleanup == "manual_recovery_required"
            else "\nEstado de liberación no verificado."
            if cleanup in ("unknown", "unreadable")
            else ""
        )
        detail.update(
            Text(
                f"{saved['agent']} · {saved['job_id']}\n"
                f"{labels.get(result.data['status'], 'No se pudo consultar.')}\n"
                "Sin relanzar ni cancelar. Esto no verifica el contenido del trabajo."
                + cleanup_note
            )
        )

    @on(Button.Pressed, "#collab-back")
    def action_back(self):
        self.workers.cancel_group(self, "collab-read")
        self.app.pop_screen()
