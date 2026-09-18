"""Reversible presentation/interaction layer; never changes runtime authority."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.widgets import Button, Static, TextArea

from ..ui_quality import configure_visual, context_text, read_state, safe_display, update_state
from ..ui_widgets import FollowLog, InspectorScreen, MessageInput, ToolDetailsScreen

QUALITY_BINDINGS = [
    Binding("f2", "layout_conversation", "Conversación"),
    Binding("f3", "layout_code", "Código"),
    Binding("f4", "layout_review", "Revisión"),
    Binding("f6", "motion", "Movimiento", priority=True),
    Binding("f7", "inspector", "Ejecución", priority=True),
    Binding("f8", "context_files", "Contexto"),
    Binding("f9", "density", "Densidad"),
    Binding("f10", "tool_details", "Herramientas"),
    Binding("ctrl+up", "message_previous", "Mensaje anterior", show=False),
    Binding("ctrl+down", "message_next", "Mensaje siguiente", show=False),
]

QUALITY_CSS = """
#quality-nav { height: 3; background: $surface; }
#quality-nav Button { min-width: 8; width: 1fr; height: 3; margin: 0; }
#quality-nav Button.active { color: $accent; border: tall $primary; }
#context-bar { height: 1; color: $text-muted; padding: 0 1; }
#new-content { display: none; height: 3; width: 1fr; }
#new-content.has-unread { display: block; }
#input-bar { height: 5; border: round $boost; padding: 0; }
#chat-input { height: 1fr; border: none; }
#send-button { width: 12; height: 3; margin: 0 1; }
#workspace { grid-columns: 22 3fr 2fr; }
#review-panel { display: none; border: round $boost; padding: 1; }
#review-summary { height: 1fr; }
#review-panel Button { height: 3; width: 1fr; }
#sidebar, #chat-panel, #editor-panel { border: round $boost; }
#sidebar:focus-within, #chat-panel:focus-within, #editor-panel:focus-within, #input-bar:focus-within { border: round $primary; }
.layout-code #workspace { grid-columns: 22 1fr 3fr; }
.layout-review #workspace { grid-size: 2 1; grid-columns: 2fr 3fr; }
.layout-review #sidebar, .layout-review #editor-panel { display: none; }
.layout-review #review-panel { display: block; }
.narrow #workspace { grid-size: 1 1; grid-columns: 1fr; }
.narrow #sidebar, .narrow #editor-panel, .narrow #review-panel { display: none; }
.narrow.layout-code #chat-panel, .narrow.layout-review #chat-panel { display: none; }
.narrow.layout-code #editor-panel, .narrow.layout-review #review-panel { display: block; }
.narrow #terminal-panel { display: none; }
.narrow #status-left { width: 1fr; }
.narrow #status-right { display: none; }
.compact .panel-title { margin: 0; }
.compact #input-bar { height: 4; }
.short #terminal-panel { display: none; }
.short #input-bar { height: 3; }
#palette-dialog, .modal-dialog, #agent-diff-dialog { max-width: 96%; max-height: 94%; }
"""


class QualityMixin:
    def _init_quality(self) -> None:
        self._ui_state = read_state(self.root)
        self._layout = self._ui_state.get("layout", "conversation")
        if self._layout not in {"conversation", "code", "review"}:
            self._layout = "conversation"
        self.reduced_motion = self._ui_state.get("reduced_motion") is True
        self._compact = self._ui_state.get("compact") is True
        self._context_files: list[str] = []  # Never reattach files silently after restart.
        self._pending_context = ""
        self._tool_events: list[dict] = []
        self._execution_stage = "En espera"
        self._event_at: float | None = None
        self._objective = "Sin tarea enviada"
        self._draft_timer = None
        self._draft_warning = False
        self._recent_commands: list[str] = []

    def _mount_quality(self) -> None:
        configure_visual(self, self.root)
        self._apply_layout()
        draft = self._ui_state.get("draft", "")
        if isinstance(draft, str):
            self.query_one("#chat-input", MessageInput).value = draft[:60000]
        self._context_label()
        self.query_one("#chat-input", MessageInput).focus()
        if self._show_splash and not self.reduced_motion:
            body = self.query_one("#workspace")
            body.styles.opacity = 0.75
            body.styles.animate("opacity", 1.0, duration=0.3)
        self.set_interval(1, self._quality_tick)
        self.query_one("#send-button", Button).tooltip = "Enviar · Ctrl+Enter. Enter agrega una línea."

    def _save_ui(self, **fields) -> None:
        try:
            update_state(self.root, **fields)
        except (OSError, ValueError) as exc:
            self.notify(f"Preferencia solo en memoria: {safe_display(exc, 160)}", severity="warning")

    @on(TextArea.Changed, "#chat-input")
    def _draft_changed(self) -> None:
        if self._draft_timer:
            self._draft_timer.stop()
        self._draft_timer = self.set_timer(0.6, self._save_draft)

    def _save_draft(self) -> None:
        text = self.query_one("#chat-input", MessageInput).value
        if safe_display(text, 60000) != text:
            if not self._draft_warning:
                self.notify("Borrador no guardado: contiene controles, posible credencial o excede el límite.", severity="warning")
                self._draft_warning = True
            return
        self._draft_warning = False
        self._save_ui(draft=text)

    def on_message_input_submitted(self, event: MessageInput.Submitted) -> None:
        event.stop()
        self._send_message()

    def _send_message(self) -> None:
        field = self.query_one("#chat-input", MessageInput)
        text = field.value.strip()
        if not text:
            return
        if self._thinking or (self._active_worker and getattr(self._active_worker, "is_running", False)):
            self.notify("Ya hay una tarea activa. El borrador se conserva.", severity="warning")
            return
        try:
            self._pending_context = context_text(self.root, self._context_files) if not text.startswith("/") else ""
        except (OSError, ValueError, UnicodeError) as exc:
            self.notify(safe_display(exc, 250), severity="error")
            return
        self._chat_history_index = -1
        if not self._chat_history or self._chat_history[-1] != text:
            self._chat_history = (self._chat_history + [text])[-100:]
        field.value = ""
        self._save_draft()
        if text.startswith("/"):
            self._handle_slash(text)
            return
        self._objective = safe_display(text, 500)
        self._chat_user(text)
        self._record_stage("Preparando contexto")
        self._active_worker = self.run_worker(self._agent_worker(text), exclusive=True, group="quality-agent")

    async def _agent_worker(self, text: str) -> None:
        from textual.worker import get_current_worker
        worker = get_current_worker()
        self._thinking = True
        chunks: list[str] = []
        usage = {}
        done = False
        try:
            prompt = await self._build_prompt(text)
            if self._pending_context:
                prompt = "[Contexto explícito de lectura; no concede permisos de edición]\n" + self._pending_context + "\n\n" + prompt
            self._pending_context = ""
            self._record_stage("Solicitud enviada; esperando eventos")
            async for event in self.session.process_message_stream(prompt):
                if worker.is_cancelled:
                    self._record_stage("Cancelación solicitada; resultado no verificado")
                    return
                kind = event.get("type", "")
                if kind == "text":
                    chunk = str(event.get("content", ""))
                    chunks.append(chunk)
                    self._chat_assistant_chunk(chunk)
                    self._record_stage("Recibiendo respuesta")
                elif kind == "reasoning":
                    self._record_stage("Evento de razonamiento recibido")
                elif kind == "tool_call":
                    self._chat_tool_call(event.get("name", "tool"), event.get("arguments", {}))
                elif kind == "tool_result":
                    self._chat_tool_result(event.get("name", "tool"), event.get("content", ""))
                elif kind == "done":
                    usage = event.get("usage") or {}
                    done = True
                    break
            self._record_stage("Respuesta recibida · pendiente de revisión" if done else "Flujo cerrado sin confirmación final")
            if done:
                self._finalize_turn(usage, "".join(chunks))
        except asyncio.CancelledError:
            self._record_stage("Cancelada · cambios/resultados por verificar")
            raise
        except Exception as exc:
            self._record_stage("Error · tarea no verificada")
            self._chat_system(safe_display(exc, 1000))
            self.notify(safe_display(exc, 300), severity="error")
        finally:
            self._pending_context = ""
            self._thinking = False
            self._update_status()

    def _chat_user(self, text: str) -> None:
        self.query_one("#chat-log", FollowLog).write(Text("\nAINZ\n" + safe_display(text, 60000), style="cyan"))

    def _chat_assistant_chunk(self, chunk: str) -> None:
        self.query_one("#chat-log", FollowLog).write(Text(safe_display(chunk, 60000)))

    def _chat_system(self, text: str) -> None:
        # Only legacy application messages use Rich markup; external values use Text.
        self.query_one("#chat-log", FollowLog).write(Text(safe_display(text), style="dim"))

    def _chat_tool_call(self, name: str, args) -> None:
        name = safe_display(name, 100)
        keys = ", ".join(map(str, args)) if isinstance(args, dict) else "No verificados"
        self._tool_events.append({"name": name, "state": "En ejecución", "detail": "Parámetros: " + keys})
        self._tool_events = self._tool_events[-100:]
        self.query_one("#chat-log", FollowLog).write(Text(f"\nHERRAMIENTA · {name} · en ejecución · F10 detalles", style="yellow"))
        self._record_stage("Herramienta: " + name)

    def _chat_tool_result(self, name: str, content: str) -> None:
        name = safe_display(name, 100)
        event = next((e for e in reversed(self._tool_events) if e["name"] == name and e["state"] == "En ejecución"), None)
        if event is None:
            event = {"name": name}
            self._tool_events.append(event)
        event.update(state="Resultado recibido · no aprobado", detail=safe_display(content))
        self._tool_events = self._tool_events[-100:]
        self.query_one("#chat-log", FollowLog).write(Text(f"RESULTADO · {name} · F10 para revisar"))
        self._record_stage("Resultado de herramienta recibido")

    def _record_stage(self, stage: str) -> None:
        self._execution_stage = stage
        self._event_at = time.monotonic()

    def quality_status(self) -> str:
        age = "sin eventos" if self._event_at is None else f"hace {max(0, int(time.monotonic()-self._event_at))} s"
        return f"{self._execution_stage} · {age}"

    def _quality_tick(self) -> None:
        screen_stack = self.screen_stack
        if (
            self.is_mounted
            and not self._exit
            and screen_stack
            and self.screen is screen_stack[0]
            and self.query("#status-center")
            and self.query("#review-summary")
        ):
            self.query_one("#status-center", Static).update(Text(self.quality_status()))
            self.query_one("#review-summary", Static).update(Text(
                "REVISIÓN\n\n" + self.quality_status() + "\n\n"
                "El diff muestra cambios actuales; no implica aprobación.\n"
                "Las pruebas solo cuentan con un resultado registrado.\n"
                "Un resultado recibido no equivale a tarea terminada.\n\n"
                f"{len(self._tool_events)} eventos disponibles · F10\n"
                "Revisión, commit y publicación son acciones separadas."
            ))

    def _inspector_text(self) -> str:
        cfg = self.session.config
        return (f"EJECUCIÓN · información observada\n\nProyecto: {self.root}\n"
                f"Objetivo: {self._objective}\nEstado: {self.quality_status()}\n\n"
                "Responsable: Lilith\nVör: sin resultado de revisión registrado\n"
                "Muninn: sin resultado de revisión registrado\n\n"
                f"Proveedor configurado: {cfg.provider}\nModelo configurado: {cfg.model}\n"
                "Ruta efectiva / cuota: no verificadas por esta vista\n"
                "Permisos: los del runtime; esta interfaz no los amplía\n"
                f"Contexto explícito de lectura: {len(self._context_files)} archivos\n\n"
                "Pruebas: consultar evidencia de herramientas / Pruebas de la Hoguera\n"
                "Decisión de producto: no registrada. Recibir una respuesta no la aprueba.")

    def action_inspector(self) -> None:
        self.push_screen(InspectorScreen(self._inspector_text))

    def action_tool_details(self) -> None:
        self.push_screen(ToolDetailsScreen(self._tool_events))

    def action_context_files(self) -> None:
        from ..hearth_screens import FilePicker
        self.push_screen(FilePicker(self.root, self._context_files), self._set_context)

    def _set_context(self, names) -> None:
        if names is None:
            return
        try:
            context_text(self.root, names)
        except (OSError, ValueError, UnicodeError) as exc:
            self.notify(safe_display(exc, 250), severity="error")
            return
        self._context_files = list(names)
        self._context_label()

    def _context_label(self) -> None:
        names = ", ".join(self._context_files) or "ninguno"
        self.query_one("#context-bar", Static).update(Text("Lectura: " + names + " · F8 añadir/quitar · Enter nueva línea · Ctrl+Enter enviar"))

    def _apply_layout(self, size=None) -> None:
        size = self.size if size is None else size
        for name in ("conversation", "code", "review"):
            self.screen_stack[0].set_class(self._layout == name, "layout-" + name)
            self.query_one("#view-" + name, Button).set_class(self._layout == name, "active")
        self.screen_stack[0].set_class(self._compact, "compact")
        self.screen_stack[0].set_class(size.width < 100, "narrow")
        self.screen_stack[0].set_class(size.height < 30, "short")

    def on_resize(self, event) -> None:
        if self.is_mounted and not self._exit:
            self._apply_layout(event.size)

    def _set_layout(self, layout: str) -> None:
        self._layout = layout
        self._apply_layout()
        self._save_ui(layout=layout)
        if not self.reduced_motion:
            panel = self.query_one("#workspace")
            panel.styles.opacity = 0.8
            panel.styles.animate("opacity", 1.0, duration=0.14)
        if layout == "conversation":
            self.query_one("#chat-input", MessageInput).focus()
        elif layout == "code":
            editor = self._current_editor()
            if editor:
                editor.focus()

    def action_layout_conversation(self) -> None:
        self._set_layout("conversation")

    def action_layout_code(self) -> None:
        self._set_layout("code")

    def action_layout_review(self) -> None:
        self._set_layout("review")

    def action_motion(self) -> None:
        self.reduced_motion = not self.reduced_motion
        self.query_one("#workspace").styles.opacity = 1
        self._save_ui(reduced_motion=self.reduced_motion)
        self.notify("Movimiento reducido" if self.reduced_motion else "Movimiento activo")

    def action_density(self) -> None:
        self._compact = not self._compact
        self._apply_layout()
        self._save_ui(compact=self._compact)

    def action_message_previous(self) -> None:
        if self._chat_history and self._chat_history_index < len(self._chat_history)-1:
            self._chat_history_index += 1
            self.query_one("#chat-input", MessageInput).value = self._chat_history[-1-self._chat_history_index]

    def action_message_next(self) -> None:
        self._chat_history_index = max(-1, self._chat_history_index-1)
        self.query_one("#chat-input", MessageInput).value = "" if self._chat_history_index == -1 else self._chat_history[-1-self._chat_history_index]

    def on_follow_log_new_content(self, event: FollowLog.NewContent) -> None:
        if event.log.id == "chat-log":
            button = self.query_one("#new-content", Button)
            button.set_class(event.log.unread > 0, "has-unread")
            button.label = f"{event.log.unread} actualizaciones nuevas · ir al final"

    def quality_button(self, button_id: str | None) -> bool:
        actions = {"view-conversation": self.action_layout_conversation,
                   "view-code": self.action_layout_code, "view-review": self.action_layout_review,
                   "view-inspector": self.action_inspector, "view-context": self.action_context_files,
                   "review-diff": self.action_show_diff, "review-tools": self.action_tool_details,
                   "new-content": lambda: self.query_one("#chat-log", FollowLog).latest()}
        action = actions.get(button_id)
        if action:
            action()
        return action is not None
