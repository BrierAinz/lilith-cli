"""Task, review and memory surfaces for the Hoguera."""

from pathlib import Path
import time
import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen, ModalScreen
from textual.widgets import Button, Checkbox, DirectoryTree, Input, Label, OptionList, RichLog, Select, Static, TabbedContent, TabPane, TextArea
from textual.widgets.option_list import Option

from .task_workspace import TaskSpec, TaskRun, TEMPLATES
from .ui_widgets import FollowLog
from .ui_quality import themed_css, read_state, update_state, safe_display

STATUS_LABELS = {"verified": "Pruebas aprobadas", "responded": "Respuesta sin verificación",
                 "paused": "En pausa", "cancelled": "Cancelada", "interrupted": "Resultado desconocido",
                 "blocked": "Bloqueada", "running": "En curso"}


class WorkspaceTree(DirectoryTree):
    """Keep the picker inside source directories, without following junctions."""
    def filter_paths(self, paths):
        for path in paths:
            if path.name in (".git", ".venv", "venv", "node_modules", "__pycache__"):
                continue
            try:
                if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                    continue
            except OSError:
                continue
            yield path


class FilePicker(ModalScreen[list[str]]):
    BINDINGS = [("escape", "cancel", "Cancelar")]
    CSS = """
    FilePicker { align: center middle; }
    #picker { width: 90%; height: 90%; background: #111820; border: round #8fd8e8; padding: 1; }
    #files-tree { height: 1fr; }
    #chosen { height: 3; }
    #choose { height: 3; }
    """
    def __init__(self, root: Path, selected: list[str]):
        super().__init__()
        self.root = root
        self.selected = list(selected)
        self.original_selection = list(selected)

    def action_cancel(self):
        self.dismiss(self.original_selection)

    def compose(self):
        with Vertical(id="picker"):
            yield Label("Selecciona archivos; pulsa de nuevo para quitar uno")
            yield WorkspaceTree(str(self.root), id="files-tree")
            yield Static(", ".join(self.selected), id="chosen")
            yield Button("Usar selección", id="choose")

    @on(DirectoryTree.FileSelected)
    def selected_file(self, event):
        try:
            name = str(event.path.resolve().relative_to(self.root))
        except ValueError:
            self.notify("El archivo está fuera del proyecto", severity="error")
            return
        if name in self.selected:
            self.selected.remove(name)
        else:
            self.selected.append(name)
        self.query_one("#chosen", Static).update(Text(", ".join(self.selected)))

    @on(Button.Pressed, "#choose")
    def choose(self):
        self.dismiss(self.selected)


class TaskScreen(Screen):
    BINDINGS = [("escape", "back", "Volver"), ("f7", "inspector", "Ejecución")]
    CSS = """
    TaskScreen { background: #0b1016; color: #dce3e8; }
    #task-form { height: auto; padding: 0 2; }
    #task-title { height: 2; color: #8fd8e8; text-style: bold; }
    #preset-row { height: 3; }
    #preset-row Select { width: 1fr; }
    #objective { height: 5; }
    #file-row { height: 3; }
    #task-files { width: 1fr; }
    #browse { width: 18; }
    #task-actions { height: 3; padding: 0 1; }
    #task-actions Button { width: 1fr; min-width: 8; margin: 0 1; }
    #live-status { height: 2; padding: 0 2; color: #d5b96d; }
    #work-tabs { height: 1fr; }
    RichLog { height: 1fr; background: #111820; }
    #diff-view { height: 1fr; }
    .compact #task-form { padding: 0 1; }
    .compact #objective { height: 3; }
    .compact #task-title { height: 1; }
    """
    def __init__(self, root: Path, *, runner_factory=TaskRun, initial_run=None):
        super().__init__()
        self.root = root
        self.runner_factory = runner_factory
        self.run = initial_run
        self.busy = False
        self.started = 0.0
        self.current_action = "Preparada"
        self.review_seen = False
        self.reported_tokens = "sin reporte"
        self._restoring = initial_run is not None
        self.changed_count = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="task-form"):
            yield Label("NUEVA SAGA · " + self.root.name, id="task-title")
            with Horizontal(id="preset-row"):
                yield Select([(title, key) for key, (title, _) in TEMPLATES.items()], value="fix", allow_blank=False, id="template")
                yield Select([("Compacto · modelos modestos", "compact"), ("Estándar · más contexto", "standard"),
                              ("Lector · sin herramientas", "reader")], value="compact", allow_blank=False, id="execution-profile")
            yield TextArea(TEMPLATES["fix"][1], id="objective")
            with Horizontal(id="file-row"):
                yield Input(placeholder="Archivos relativos, separados por comas; también puedes indicar uno nuevo", id="task-files")
                yield Button("Archivos…", id="browse")
            yield Input(placeholder='Verificación local, por ejemplo: node --test tests/fix.test.mjs', id="verify-command")
        yield Checkbox("Permitir editar los archivos seleccionados", id="allow-edit")
        with Horizontal(id="task-actions"):
            yield Button("Iniciar", id="execute")
            yield Button("Pausa", id="pause", disabled=True)
            yield Button("Cancelar", id="cancel", disabled=True)
            yield Button("Retomar", id="resume-task", disabled=True)
            yield Button("Registrar revisión", id="accept", disabled=True)
            yield Button("Volver", id="back")
        yield Static("Preparada · selecciona archivos y verificación", id="live-status")
        yield Button("Contenido nuevo · ir al final", id="task-latest")
        with TabbedContent(id="work-tabs"):
            with TabPane("Actividad", id="activity-tab"):
                yield FollowLog(id="activity", wrap=True, markup=False)
            with TabPane("Cambios", id="changes-tab"):
                yield TextArea("El diff aparecerá al terminar la ejecución.", read_only=True, id="diff-view")
            with TabPane("Pruebas", id="checks-tab"):
                yield FollowLog(id="checks", wrap=True, markup=False)

    def on_mount(self):
        self.query_one("#task-latest", Button).display = False
        self.call_after_refresh(self._restore_task_draft)
        self.query_one("#allow-edit", Checkbox).tooltip = "Los cambios se aplican en la carpeta. La aceptación registra tu revisión local, no hace commit ni publica."
        self.query_one("#pause", Button).tooltip = "Espera a que termine la llamada actual y guarda un checkpoint seguro."
        self.query_one("#cancel", Button).tooltip = "Solicita el cierre y conserva cualquier resultado desconocido para revisión."
        self.set_interval(0.25, self.update_clock)
        if self.run is not None:
            spec = self.run.spec
            self.query_one("#objective", TextArea).load_text(spec.objective)
            self.query_one("#task-files", Input).value = ",".join(spec.files)
            self.query_one("#verify-command", Input).value = spec.verify
            self.query_one("#allow-edit", Checkbox).value = spec.edit
            self.query_one("#execution-profile", Select).value = spec.profile
            self.query_one("#diff-view", TextArea).load_text(self.run.diff())
            progress = self.run.result.get("progress") or {}
            checks = progress.get("verification") or {}
            self.query_one("#checks", RichLog).write(Text(checks.get("output") or "Sin verificación registrada"))
            self.query_one("#live-status", Static).update(Text("Estado guardado: " + self.run.result.get("status", "desconocido")))
            self.query_one("#resume-task", Button).disabled = self.run.result.get("status") != "paused"
            self.call_after_refresh(setattr, self, "_restoring", False)

    def on_resize(self, event):
        self.set_class(event.size.width < 90 or event.size.height < 35, "compact")

    def update_clock(self):
        if self.busy:
            self.query_one("#live-status", Static).update(Text(f"{self.current_action} · {time.monotonic()-self.started:.0f}s · tokens: {self.reported_tokens} · {self.changed_count} cambios"))

    @on(Select.Changed, "#template")
    def template_selected(self, event):
        if event.value in TEMPLATES and not self.busy and not self._restoring:
            self.query_one("#objective", TextArea).load_text(TEMPLATES[event.value][1])
            if event.value == "review":
                self.query_one("#allow-edit", Checkbox).value = False

    @on(Select.Changed, "#execution-profile")
    def profile_selected(self, event):
        if event.value == "reader":
            self.query_one("#allow-edit", Checkbox).value = False

    @on(Button.Pressed, "#browse")
    def browse(self):
        selected = [p.strip() for p in self.query_one("#task-files", Input).value.split(",") if p.strip()]
        self.app.push_screen(FilePicker(self.root, selected), self.files_chosen)

    def files_chosen(self, selected):
        self.query_one("#task-files", Input).value = ",".join(selected)

    @on(Button.Pressed, "#execute")
    def execute(self):
        if self.busy:
            return
        try:
            spec = TaskSpec(self.root, self.query_one("#objective", TextArea).text,
                self.query_one("#task-files", Input).value.split(","),
                self.query_one("#verify-command", Input).value, self.query_one("#allow-edit", Checkbox).value,
                self.query_one("#execution-profile", Select).value,
                {"fix": "bug-fix", "review": "code-review", "tests": "test-authoring", "docs": "docs-update"}.get(self.query_one("#template", Select).value))
            self.run = self.runner_factory(spec)
        except (ValueError, OSError, UnicodeError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.launch()

    def activity(self, line):
        if self.run is not None:
            self.changed_count = sum(self.run._read(name) != before for name, before in self.run.before.items())
        if line.startswith("[Lilith] tokens: "):
            self.reported_tokens = line.removeprefix("[Lilith] tokens: ")
        self.current_action = line
        self.query_one("#activity", RichLog).write(Text(line))

    def set_busy(self, value):
        self.busy = value
        for selector in ("#execute", "#template", "#execution-profile", "#objective", "#task-files", "#verify-command", "#allow-edit", "#browse"):
            self.query_one(selector).disabled = value
        self.query_one("#pause", Button).disabled = not value
        self.query_one("#cancel", Button).disabled = not value
        self.query_one("#accept", Button).disabled = True
        self.query_one("#resume-task", Button).disabled = True

    @work(exclusive=True)
    async def launch(self, resume=None):
        self.review_seen = False
        self.set_busy(True)
        self.started = time.monotonic()
        try:
            result = await self.run.execute(self.activity, resume=resume)
            self.query_one("#diff-view", TextArea).load_text(self.run.diff())
            checks = result.get("progress", {}).get("verification") or {}
            self.query_one("#checks", RichLog).write(Text(checks.get("output") or checks.get("error") or "No se ejecutó una verificación."))
            self.query_one("#activity", RichLog).write(Text(result.get("response") or result.get("error") or ""))
            usage = result.get("usage") or {}
            tokens = usage.get("total_tokens") or "no reportado"
            status_label = STATUS_LABELS.get(result.get("status"), "Estado desconocido")
            self.query_one("#live-status", Static).update(Text(f"{status_label} · tokens: {tokens} · {time.monotonic()-self.started:.1f}s"))
        except Exception as exc:
            self.activity(type(exc).__name__ + ": " + str(exc))
            self.query_one("#live-status", Static).update(Text("Error: " + str(exc)))
            self.run.result = {"status": "blocked", "error": str(exc)}
        finally:
            self.set_busy(False)
        status = self.run.result.get("status")
        self.query_one("#resume-task", Button).disabled = status != "paused"
        self.query_one("#accept", Button).disabled = status not in ("responded", "verified") or not self.review_seen

    @on(TabbedContent.TabActivated, "#work-tabs")
    def reviewed_diff(self, event):
        if event.pane.id == "changes-tab":
            self.review_seen = True
            if self.run is not None and not self.busy:
                self.query_one("#accept", Button).disabled = self.run.result.get("status") not in ("responded", "verified")

    @on(Button.Pressed, "#pause")
    def pause(self):
        self.run.request("pause")
        self.activity("Pausa solicitada: esperando checkpoint seguro")

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.run.request("cancel")
        self.activity("Cancelación solicitada: esperando cierre del proceso")

    @on(Button.Pressed, "#resume-task")
    def resume_task(self):
        self.launch(resume=Path(self.run.result["session"]).stem)

    @on(Button.Pressed, "#accept")
    def accept(self):
        try:
            self.run.accept()
        except (ValueError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify("Revisión guardada. No se creó un commit ni se publicó nada.")
        self.query_one("#accept", Button).disabled = True

    @on(Button.Pressed, "#back")
    def action_back(self):
        if self.busy:
            self.notify("Pausa o cancela la tarea antes de salir.", severity="warning")
        else:
            self._save_task_draft()
            self.app.pop_screen()
            self.app.call_after_refresh(self.app.action_refresh)


    def _restore_task_draft(self):
        if self.run is not None:
            return
        draft = read_state(self.root).get("task_draft", {})
        if not isinstance(draft, dict):
            return
        for key, selector, kind in (("objective", "#objective", TextArea), ("files", "#task-files", Input), ("verify", "#verify-command", Input)):
            value = draft.get(key)
            if isinstance(value, str):
                widget = self.query_one(selector, kind)
                if kind is TextArea:
                    widget.load_text(value[:60000])
                else:
                    widget.value = value[:4000]
        self.query_one("#allow-edit", Checkbox).value = False

    def _save_task_draft(self):
        if self.run is not None or self.busy:
            return
        draft = {"objective": self.query_one("#objective", TextArea).text,
                 "files": self.query_one("#task-files", Input).value,
                 "verify": self.query_one("#verify-command", Input).value}
        if any(safe_display(value, 60000) != value for value in draft.values()):
            return
        try:
            update_state(self.root, task_draft=draft)
        except (OSError, ValueError):
            self.notify("Borrador de tarea solo en memoria", severity="warning")

    @on(TextArea.Changed, "#objective")
    @on(Input.Changed, "#task-files")
    @on(Input.Changed, "#verify-command")
    def task_draft_changed(self):
        if getattr(self, "_task_draft_timer", None):
            self._task_draft_timer.stop()
        self._task_draft_timer = self.set_timer(0.6, self._save_task_draft)

    def action_inspector(self):
        from .ui_widgets import InspectorScreen
        def render():
            result = self.run.result if self.run is not None else {}
            checks = (result.get("progress") or {}).get("verification") or {}
            edit = bool(self.run.spec.edit) if self.run is not None else self.query_one("#allow-edit", Checkbox).value
            return (f"EJECUCIÓN · {self.root.name}\n\n"
                    f"Estado registrado: {result.get('status', 'sin ejecución')}\n"
                    f"Último evento: {safe_display(self.current_action, 600)}\n"
                    f"Edición en alcance: {'permitida' if edit else 'no permitida'}\n"
                    f"Cambios observados: {self.changed_count}\n\n"
                    "Responsable: Lilith\nVör: sin revisión registrada\nMuninn: sin revisión registrada\n"
                    "Modelo y ruta efectiva: consultar registro del runtime\n\n"
                    f"Verificación registrada: {safe_display(checks, 1500) if checks else 'no disponible'}\n\n"
                    "Registrar revisión no hace commit ni publica.")
        self.app.push_screen(InspectorScreen(render))

    def on_follow_log_new_content(self, event):
        if event.log.id in {"activity", "checks"}:
            self.query_one("#task-latest", Button).display = event.log.unread > 0

    @on(Button.Pressed, "#task-latest")
    def task_latest(self):
        self.query_one("#activity", FollowLog).latest()
        self.query_one("#checks", FollowLog).latest()
        self.query_one("#task-latest", Button).display = False


class MemoryScreen(Screen):
    BINDINGS = [("escape", "back", "Volver")]
    CSS = """
    MemoryScreen { background: #0b1016; color: #dce3e8; padding: 1 2; }
    #memory-list { height: 1fr; }
    #memory-value { height: 5; }
    #memory-actions { height: 3; }
    #memory-actions Button { width: 1fr; }
    """
    def __init__(self, root):
        super().__init__()
        self.root = root
    def compose(self):
        yield Label("MEMORIA DE LILITH · preferencias explícitas, nunca credenciales")
        yield Select([("Personal", "personal"), ("Este proyecto", "project")], value="personal", allow_blank=False, id="memory-scope")
        yield OptionList(id="memory-list")
        yield Input(placeholder="Nombre de la preferencia", id="memory-key")
        yield Label("Valor (editable)")
        yield TextArea(id="memory-value")
        with Horizontal(id="memory-actions"):
            yield Button("Guardar / corregir", id="memory-save")
            yield Button("Olvidar", id="memory-forget")
            yield Button("Volver", id="memory-back")
    def scope(self):
        return str(self.root) if self.query_one("#memory-scope", Select).value == "project" else None
    def on_mount(self):
        self.refresh_memory()
    def refresh_memory(self):
        from .work_memory import records
        listing = self.query_one("#memory-list", OptionList)
        listing.clear_options()
        for record in records(self.scope()):
            listing.add_option(Option(Text(record["key"] + " · " + record["value"]), id=record["key"]))
    @on(Select.Changed, "#memory-scope")
    def scope_changed(self):
        self.refresh_memory()
    @on(OptionList.OptionSelected, "#memory-list")
    def selected(self, event):
        from .work_memory import records
        record = next(row for row in records(self.scope()) if row["key"] == event.option.id)
        self.query_one("#memory-key", Input).value = record["key"]
        self.query_one("#memory-value", TextArea).load_text(record["value"])
    @on(Button.Pressed, "#memory-save")
    async def save(self):
        from .work_memory import save_preference
        key = self.query_one("#memory-key", Input).value.strip()
        value = self.query_one("#memory-value", TextArea).text
        try:
            await save_preference(key, value, self.scope())
        except (SystemExit, ValueError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.refresh_memory()
        self.notify("Preferencia guardada")
    @on(Button.Pressed, "#memory-forget")
    async def forget(self):
        from .work_memory import delete_preference
        await delete_preference(self.query_one("#memory-key", Input).value.strip(), self.scope())
        self.refresh_memory()
        self.notify("Preferencia olvidada")
    @on(Button.Pressed, "#memory-back")
    def action_back(self):
        self.app.pop_screen()


for _screen in (FilePicker, TaskScreen, MemoryScreen):
    _screen.CSS = themed_css(_screen.CSS)

TaskScreen.CSS += """
#task-latest { height: 3; width: 100%; }
#accept { min-width: 20; }
.compact #task-form { height: 11; }
.compact #task-actions { layout: grid; grid-size: 3 2; grid-rows: 3 3; height: 6; }
.compact #task-actions Button { width: 1fr; min-width: 8; margin: 0; }
"""
