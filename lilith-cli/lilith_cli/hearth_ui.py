"""Interactive, keyboard-first Hoguera for the personal Lilith workspace."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option
import yaml

from . import config as config_module
from .ui_quality import configure_visual, read_state, update_state, themed_css


class HearthApp(App[tuple[str, str, str | None]]):
    TITLE = "Lilith · La Hoguera"
    BINDINGS = [
        Binding("ctrl+shift+p", "command_palette", "Comandos", priority=True),
        Binding("f9", "density", "Densidad"),
        Binding("ctrl+t", "toggle_theme", "Tema"),
        Binding("ctrl+n", "new_saga", "Nueva saga"),
        Binding("ctrl+f", "search", "Buscar"),
        Binding("f5", "refresh", "Actualizar"),
        Binding("f6", "motion", "Movimiento"),
        Binding("f7", "collaborators", "Colaboradores"),
        Binding("f8", "coordinate", "Coordinar"),
        Binding("escape", "clear_search", "Limpiar"),
        Binding("ctrl+q", "quit", "Salir"),
    ]
    CSS = """
    Screen { background: #0b1016; color: #dce3e8; }
    #brand { height: 6; padding: 1 3; background: #111820; border-bottom: solid #35444d; }
    #brand-title { color: #8fd8e8; text-style: bold; height: 1; }
    #brand-subtitle { color: #9ba9b5; margin-top: 1; height: 1; }
    #ember { dock: right; width: 22; color: #d5b96d; text-align: right; }
    #body { height: 1fr; padding: 1 2; }
    #controls { width: 34; padding: 1 2; margin-right: 2; background: #111820; border: round #35444d; }
    .section-title { color: #d5b96d; text-style: bold; height: 2; }
    #project { width: 1fr; margin-bottom: 1; }
    #project-hint { color: #9ba9b5; height: auto; margin-bottom: 1; }
    #actions { height: auto; }
    Button { width: 1fr; margin-bottom: 1; background: #1c2934; color: #dce3e8; border: tall transparent; }
    Button:hover { background: #2a3e4b; color: #8fd8e8; }
    Button:focus { border: tall #8fd8e8; }
    #new { background: #8fd8e8; color: #10161e; text-style: bold; }
    #sessions-panel { width: 1fr; padding: 1 2; border: round #35444d; background: #111820; }
    #summary { height: 2; color: #9ba9b5; }
    Input { background: #0b1016; border: tall #35444d; }
    Input:focus { border: tall #8fd8e8; }
    #search { margin-bottom: 1; }
    #sessions { height: 1fr; background: #111820; border: none; }
    #empty { height: auto; color: #9ba9b5; padding: 1 0; }
    #session-detail { height: 3; color: #9ba9b5; border-top: solid #35444d; padding-top: 1; }
    #shortcuts { dock: bottom; height: 1; background: #18232e; color: #9ba9b5; }
    .narrow #body { layout: vertical; padding: 0 1; }
    .narrow #controls { width: 1fr; height: 9; margin-right: 0; padding: 0 1; }
    .narrow #actions { layout: horizontal; height: 3; }
    .narrow #actions Button { width: 1fr; }
    .narrow #project-hint { display: none; }
    .narrow #controls Button { height: 3; margin-bottom: 0; }
    .narrow #controls .section-title { height: 1; }
    .narrow #project { margin-bottom: 0; }
    .narrow #sessions-panel { width: 1fr; height: 1fr; padding: 0 1; }
    .narrow #brand { height: 4; padding: 0 2; }
    .narrow #ember { display: none; }
    .narrow #brand-subtitle { margin-top: 0; }
    .narrow #summary { height: 1; }
    .narrow #session-detail { display: none; }
    """

    def __init__(self, root: Path, *, sessions: list[dict] | None = None,
                 preferences: Path | None = None) -> None:
        super().__init__()
        self.root = root.resolve()
        self._supplied_sessions = sessions
        self._sessions: list[dict] = []
        self._visible: list[dict] = []
        self.preferences = preferences or self.root / ".ygg/ui/hearth.yaml"
        self._preference_root = self.root
        state = read_state(self.root)
        pins = state.get("pinned_projects", [])
        self._pinned_projects = [p for p in pins if isinstance(p, str)][:12] if isinstance(pins, list) else []
        self._compact = state.get("compact") is True
        self.reduced_motion = state.get("reduced_motion") is True
        try:
            prefs = yaml.safe_load(self.preferences.read_text(encoding="utf-8")) or {}
            self.reduced_motion = bool(prefs.get("reduced_motion", False))
        except (OSError, ValueError, AttributeError, yaml.YAMLError):
            pass
        self._frame = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="brand"):
            yield Static("·  ᛚ  ·   LA HOGUERA", id="ember")
            yield Label("L I L I T H   /   TALLER DE SAGAS", id="brand-title")
            yield Label("Tu trabajo permanece. Elige dónde continuar.", id="brand-subtitle")
        with Horizontal(id="body"):
            with Vertical(id="controls"):
                yield Label("PROYECTO", classes="section-title")
                yield Input(str(self.root), id="project", placeholder="Carpeta de trabajo")
                yield Static("Cambia la ruta y pulsa Enter.\nTus sesiones se filtran por proyecto.", id="project-hint")
                with Vertical(id="actions"):
                    yield Button("Continuar", id="continue-saga")
                    yield Button("Coordinar · F8", id="coordinate")
                    yield Button("Nueva saga", id="new")
                    yield Button("Memoria", id="memory")
                    yield Button("Configurar modelo", id="setup")
                    yield Button("Movimiento: activo", id="motion")
            with Vertical(id="sessions-panel"):
                yield Button("Colaboradores · F7", id="collaborators")
                yield Label("CONTINUAR UNA SAGA", classes="section-title")
                yield Static("", id="summary")
                yield Input(placeholder="Buscar por objetivo o sesión…", id="search")
                yield Static("", id="empty")
                yield OptionList(id="sessions")
                yield Static("Selecciona una sesión para retomarla. Enter abre el chat.", id="session-detail")
        yield Static(" Ctrl+N Nueva · Ctrl+F Buscar · F5 Actualizar · F6 Movimiento · Ctrl+Q Salir", id="shortcuts")

    def on_mount(self) -> None:
        configure_visual(self, self.root)
        self.screen.set_class(self._compact, "compact-ui")
        self._refresh()
        self._motion_label()
        self.query_one("#search", Input).focus()
        if not self.reduced_motion:
            body = self.query_one("#body")
            body.styles.opacity = 0.3
            body.styles.animate("opacity", 1.0, duration=0.3)
        self.set_interval(1.2, self._animate_ember)

    def on_resize(self, event) -> None:
        self.screen.set_class(event.size.width < 90, "narrow")

    def _animate_ember(self) -> None:
        if self.reduced_motion or not self.is_mounted or not self.app_focus or self.screen is not self.screen_stack[0] or self.size.width < 90:
            return
        frames = ("·  ᛚ  ·", "˙  ᛚ  ·", "·  ᛚ  ˙", "·  ᛚ  ·")
        self._frame = (self._frame + 1) % len(frames)
        self.query_one("#ember", Static).update(f"{frames[self._frame]}   LA HOGUERA")

    def _refresh(self) -> None:
        from .repl import _list_saved_conversations

        source = self._supplied_sessions if self._supplied_sessions is not None else _list_saved_conversations()
        self._sessions = [s for s in source if isinstance(s.get("project_root"), str)
                          and Path(s["project_root"]).resolve() == self.root]
        self._filter(self.query_one("#search", Input).value)

    def _filter(self, query: str) -> None:
        query = query.casefold().strip()
        options = self.query_one("#sessions", OptionList)
        options.clear_options()
        self._visible = []
        for session in self._sessions:
            goal = session.get("goal") if isinstance(session.get("goal"), dict) else {}
            title = str(goal.get("objective") or session.get("preview") or "Conversación")
            if query and query not in f"{title} {session['name']}".casefold():
                continue
            state = {"active": "EN CURSO", "paused": "EN PAUSA", "completed": "TERMINADA"}.get(
                goal.get("status"), "CONVERSACIÓN")
            progress = session.get("progress") or {}
            state = {"running": "EN CURSO", "paused": "EN PAUSA", "blocked": "BLOQUEADA",
                     "interrupted": "REVISAR INTERRUPCIÓN", "responded": "POR VERIFICAR",
                     "verified": "PRUEBAS APROBADAS"}.get(progress.get("status"), state)
            if progress.get("pending"):
                state = "RESULTADO PENDIENTE"
            label = Text(title + "\n", style="bold")
            label.append(f"{state}  ·  {session.get('model', 'modelo')}  ·  {session['name']}\n", style="dim")
            options.add_option(Option(label, id=session["name"]))
            self._visible.append(session)
        self.query_one("#summary", Static).update(f"{len(self._visible)} de {len(self._sessions)} sesiones · {self.root.name}")
        empty = self.query_one("#empty", Static)
        empty.display = not self._visible
        empty.update("No hay coincidencias. Esc limpia la búsqueda." if query else
                     "Tu próxima saga empieza aquí.\nPulsa Nueva saga o Ctrl+N para trabajar en este proyecto.")

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        if self._visible:
            listing = self.query_one("#sessions", OptionList)
            listing.focus()
            listing.highlighted = 0

    @on(Input.Submitted, "#project")
    def project_submitted(self, event: Input.Submitted) -> None:
        candidate = Path(event.value).expanduser().resolve()
        if not candidate.is_dir():
            self.notify("Esa carpeta no existe.", severity="error")
            return
        self.root = candidate
        self._refresh()
        self.notify("Proyecto seleccionado")

    @on(OptionList.OptionSelected, "#sessions")
    def session_selected(self, event: OptionList.OptionSelected) -> None:
        session = next((item for item in self._visible if item["name"] == event.option.id), None)
        if session and session.get("task_request"):
            from .task_workspace import TaskRun
            from .hearth_screens import TaskScreen
            try:
                run = TaskRun.reopen(Path(session["task_request"]))
                if run.spec.root != self.root:
                    raise ValueError("La tarea corresponde a otro proyecto.")
                self.push_screen(TaskScreen(self.root, initial_run=run))
            except (OSError, ValueError, KeyError) as exc:
                self.notify(str(exc), severity="error")
            return
        self.exit(("resume", str(self.root), str(event.option.id)))

    @on(OptionList.OptionHighlighted, "#sessions")
    def session_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        session = next((s for s in self._visible if s["name"] == event.option.id), None)
        if session:
            progress = session.get("progress") or {}
            detail = (f"{progress.get('tools_completed', 0)} herramientas · "
                      f"{progress.get('tool_errors', 0)} errores · "
                      f"{len(progress.get('changed_files', []))} cambios\n"
                      f"{progress.get('current_action', 'Enter para retomar')}")
            self.query_one("#session-detail", Static).update(Text(
                detail if progress else f"{session.get('message_count', 0)} mensajes · {session.get('timestamp', '')}\nEnter para retomar · modelo actual de tu configuración"))

    @on(Button.Pressed, "#continue-saga")
    def continue_pressed(self) -> None:
        self.action_continue()

    @on(Button.Pressed, "#new")
    def action_new_saga(self) -> None:
        candidate = Path(self.query_one("#project", Input).value).expanduser().resolve()
        if not candidate.is_dir():
            self.notify("Elige una carpeta de proyecto válida.", severity="error")
            self.query_one("#project", Input).focus()
            return
        self.root = candidate
        from .hearth_screens import TaskScreen
        self.push_screen(TaskScreen(self.root))

    @on(Button.Pressed, "#memory")
    def open_memory(self):
        from .hearth_screens import MemoryScreen
        self.push_screen(MemoryScreen(self.root))

    @on(Button.Pressed, "#collaborators")
    def action_collaborators(self):
        from .collaborator_screen import CollaboratorScreen
        self.push_screen(CollaboratorScreen())

    @on(Button.Pressed, "#coordinate")
    def action_coordinate(self):
        candidate = Path(self.query_one("#project", Input).value).expanduser().resolve()
        if not candidate.is_dir():
            self.notify("Elige una carpeta de proyecto válida.", severity="error")
            return
        self.exit(("coordinate", str(candidate), None))

    async def action_quit(self):
        if getattr(self.screen, "busy", False):
            self.notify("Pausa o cancela la tarea antes de salir.", severity="warning")
            return
        await super().action_quit()

    @on(Button.Pressed, "#setup")
    def configure_model(self) -> None:
        self.exit(("setup", str(self.root), None))

    @on(Button.Pressed, "#motion")
    def action_motion(self) -> None:
        from .hearth import _write_yaml

        self.reduced_motion = not self.reduced_motion
        self._motion_label()
        self.query_one("#body").styles.opacity = 1.0
        self._persist_visual(reduced_motion=self.reduced_motion)
        try:
            _write_yaml(self.preferences, {"reduced_motion": self.reduced_motion})
        except OSError:
            self.notify("Preferencia aplicada solo a esta sesión; no pudo guardarse.", severity="warning")

    def _motion_label(self) -> None:
        self.query_one("#motion", Button).label = "Movimiento: reducido" if self.reduced_motion else "Movimiento: activo"

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        self.query_one("#search", Input).value = ""

    def action_refresh(self) -> None:
        self._refresh()
        self.notify("Sesiones actualizadas")

    def action_continue(self) -> None:
        listing = self.query_one("#sessions", OptionList)
        if self._visible:
            if listing.highlighted is None:
                listing.highlighted = 0
            listing.action_select()
        else:
            self.notify("No hay sesión para continuar en este proyecto.")

    def action_pin_project(self) -> None:
        name = str(self.root)
        self._pinned_projects = [p for p in self._pinned_projects if p != name] if name in self._pinned_projects else [name, *self._pinned_projects][:12]
        self._persist_visual(pinned_projects=self._pinned_projects)
        self.notify("Proyectos fijados actualizados")

    def _choose_project(self, path: str) -> None:
        self.query_one("#project", Input).value = path
        self.project_submitted(Input.Submitted(self.query_one("#project", Input), path))

    def action_density(self) -> None:
        self._compact = not self._compact
        self.screen_stack[0].set_class(self._compact, "compact-ui")
        self._persist_visual(compact=self._compact)

    def action_toggle_theme(self) -> None:
        self.theme = "norse-light" if self.theme == "norse-dark" else "norse-dark"
        self._persist_visual(theme=self.theme)

    def _persist_visual(self, **fields) -> None:
        try:
            update_state(self._preference_root, **fields)
        except (OSError, ValueError):
            self.notify("Preferencia aplicada solo en memoria.", severity="warning")

    def action_command_palette(self) -> None:
        from .ide.widgets.command_palette import CommandPaletteScreen, PaletteItem
        busy = getattr(self.screen, "busy", False)
        guard = "Pausa o cancela la tarea activa primero" if busy else ""
        actions = [("Continuar sesión", self.action_continue, guard),
                   ("Nueva saga", self.action_new_saga, guard),
                   ("Coordinar · F8", self.action_coordinate, guard),
                   ("Colaboradores · F7", self.action_collaborators, ""),
                   ("Buscar sesiones", self.action_search, ""),
                   ("Fijar / quitar proyecto", self.action_pin_project, ""),
                   ("Tema claro / oscuro", self.action_toggle_theme, ""),
                   ("Movimiento · F6", self.action_motion, ""),
                   ("Densidad · F9", self.action_density, ""),
                   ("Memoria", self.open_memory, ""),
                   ("Configurar modelo", self.configure_model, guard)]
        items = [PaletteItem(label, cb, disabled_reason=reason) for label, cb, reason in actions]
        items.extend(PaletteItem(Path(p).name, lambda path=p: self._choose_project(path), category="Proyectos fijados", search_text=p, disabled_reason=guard) for p in self._pinned_projects)
        def selected(callback):
            if callback:
                callback()
        self.push_screen(CommandPaletteScreen(items), selected)



HearthApp.CSS = themed_css(HearthApp.CSS) + """
#controls { overflow-y: auto; }
#controls Button { margin-bottom: 0; height: 3; }
#continue-saga { color: $accent; text-style: bold; }
.narrow #memory, .narrow #setup, .narrow #motion { display: none; }
.compact-ui #brand { height: 4; padding: 0 2; }
.compact-ui #brand-subtitle { margin: 0; }
.compact-ui #body { padding: 0 1; }
"""
