"""Lilith IDE TUI application core."""

from __future__ import annotations

import asyncio
import dataclasses
import difflib
import json
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

if TYPE_CHECKING:
    from ..agent import AgentSession

from ..config import CONFIG_DIR
from .config import IDEConfig
from .context import ContextManager
from .lsp.manager import LSPManager
from .plan import AgentPlan, build_execution_prompt, build_planning_prompt, parse_plan
from .plugins import PluginManager
from .realms import RealmManager
from .runestones import RunestoneForge
from .theme import _NORSE_CSS, _NORSE_LIGHT_THEME, _NORSE_THEME
from .utils.helpers import (
    GrepResult,
    _apply_patch,
    _backup_path,
    _detect_language,
    _normalize_line_endings,
    _parse_unified_diff,
    _shorten_path,
)
from .screens.modals import (
    CompletionScreen,
    ConfigScreen,
    DiagnosticsScreen,
    DiffScreen,
    FileSearchScreen,
    FindReplaceScreen,
    FindScreen,
    GoToLineScreen,
    GrepScreen,
    HistoryScreen,
    HoverScreen,
    OutlineScreen,
    PatchScreen,
    ProjectFindReplaceScreen,
    RecentFilesScreen,
    RunestoneScreen,
    ToastHistoryScreen,
    YggdrasilPanelScreen,
)
from .screens.splash import SplashScreen
from .widgets.command_palette import CommandPaletteScreen, PaletteItem
from .widgets.file_tree import RuneDirectoryTree
from .views.editor import EditorMixin
from .views.agent_view import AgentMixin
from .views.file_tree import FileTreeMixin
from .views.terminal import TerminalMixin
from .views.git_view import GitMixin
from .views.yggdrasil_panel import YggdrasilPanelMixin

class LilithIDEApp(
    FileTreeMixin,
    TerminalMixin,
    AgentMixin,
    YggdrasilPanelMixin,
    GitMixin,
    EditorMixin,
    App[None],
):
    """Textual TUI application for the Lilith coding agent.

    MRO is deliberate:
      * TerminalMixin before AgentMixin — `_handle_slash` (AgentMixin) calls
        `self._shell_worker(...)` which lives on TerminalMixin.
      * GitMixin before EditorMixin — EditorMixin wires
        `self.run_worker(self._git_info_worker(), ...)` in two places and
        `self._update_editor_info_text(...)` is consumed from inside
        GitMixin. Both directions resolve via MRO.
    """

    CSS = _NORSE_CSS
    BINDINGS = [
        Binding("ctrl+q", "quit", "Salir"),
        Binding("ctrl+s", "save_file", "Guardar"),
        Binding("ctrl+r", "refresh_tree", "Refresh"),
        Binding("ctrl+p", "open_file_search", "Buscar archivo"),
        Binding("ctrl+shift+f", "open_grep", "Buscar texto"),
        Binding("ctrl+f", "find_in_file", "Find"),
        Binding("ctrl+h", "find_replace", "Reemplazar"),
        Binding("ctrl+shift+h", "project_find_replace", "Reemplazar proyecto"),
        Binding("ctrl+g", "go_to_line", "Ir a línea"),
        Binding("ctrl+shift+g", "open_git", "Git"),
        Binding("ctrl+shift+alt+c", "commit", "Commit"),
        Binding("ctrl+shift+y", "open_history", "Historial"),
        Binding("ctrl+y", "open_yggdrasil_panel", "Yggdrasil"),
        Binding("ctrl+t", "toggle_theme", "Tema"),
        Binding("ctrl+m", "toggle_markdown", "Markdown"),
        Binding("ctrl+w", "close_tab", "Cerrar tab"),
        Binding("ctrl+shift+t", "reopen_closed_tab", "Reabrir tab"),
        Binding("ctrl+e", "recent_files", "Archivos recientes"),
        Binding("ctrl+shift+p", "command_palette", "Command palette"),
        Binding("ctrl+shift+i", "format_file", "Formatear"),
        Binding("ctrl+shift+o", "show_outline", "Outline"),
        Binding("ctrl+shift+b", "toggle_bookmark", "Bookmark"),
        Binding("ctrl+shift+c", "copy_path", "Copiar path"),
        Binding("ctrl+f5", "run_current_file", "Ejecutar archivo"),
        Binding("ctrl+f9", "debug_current_file", "Debug archivo"),
        Binding("ctrl+equal", "zoom_in", "Zoom +"),
        Binding("ctrl+minus", "zoom_out", "Zoom -"),
        Binding("ctrl+shift+z", "toggle_soft_wrap", "Soft wrap"),
        Binding("ctrl+shift+n", "toast_history", "Notificaciones"),
        Binding("ctrl+comma", "open_config", "Config"),
        Binding("ctrl+shift+d", "show_diff", "Diff"),
        Binding("ctrl+space", "request_completion", "Completar"),
        Binding("ctrl+shift+i", "show_hover", "Hover"),
        Binding("f12", "go_to_definition", "Definición"),
        Binding("ctrl+shift+m", "toggle_zen_mode", "Zen mode"),
        Binding("ctrl+shift+grave", "toggle_terminal_fullscreen", "Terminal fullscreen"),
        Binding("ctrl+grave", "focus_terminal", "Terminal"),
        Binding("escape", "cancel_generation", "Cancelar"),
        Binding("f1", "show_help", "Ayuda"),
    ]

    current_file: reactive[Path | None] = reactive(None)

    def __init__(
        self,
        session: AgentSession,
        root: Path | None = None,
        *,
        title: str = "Lilith IDE — Hlidskjalf Console",
        show_splash: bool = True,
    ) -> None:
        super().__init__()
        self.session = session
        self.root = (root or Path.cwd()).resolve()
        self._show_splash = show_splash
        self.context_manager = ContextManager(self.root)
        self.runestone_forge = RunestoneForge()
        self.realm_manager = RealmManager(self.root)
        self.lsp_manager = LSPManager(self.root)
        self.lsp_manager.on_diagnostics = self._on_lsp_diagnostics
        self.plugin_manager = PluginManager(self.root)
        self._current_diagnostics: dict[Path, list[dict[str, Any]]] = {}
        self._completion_timer: Any = None
        self._completion_request_position: tuple[int, int] | None = None
        self._current_plan = AgentPlan()
        self._title = title
        self._token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self._active_worker: Any = None
        self._open_files: dict[str, Path] = {}
        self._tab_paths: dict[str, Path] = {}
        self._file_versions: dict[Path, int] = {}
        self._modified: set[str] = set()
        self._closed_tabs: list[Path] = []
        self._recent_files: list[Path] = []
        self._editor_id_counter: int = 0
        self.ide_config = IDEConfig.load()
        self._file_mtimes: dict[Path, float] = {}
        self._markdown_mode: bool = False
        self._bookmarks: dict[Path, set[int]] = {}
        self._zoom_level: int = 0
        self._soft_wrap: bool = False
        self._toast_history: list[dict[str, str]] = []
        self._terminal_history: list[str] = []
        self._terminal_history_index: int = -1
        self._chat_history: list[str] = []
        self._chat_history_index: int = -1
        self._zen_mode: bool = False
        self._zen_mode_displays: dict[str, str] = {}
        self._terminal_fullscreen: bool = False
        self._terminal_normal_height: int = 8
        self._thinking: bool = False
        self._thinking_frame: int = 0
        self._thinking_worker_task: Any = None
        self._snippets: dict[str, str] = {
            "py": "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
            "test": "def test_():\n    assert True\n",
            "class": "class :\n    def __init__(self):\n        pass\n",
            "md": "# Título\n\n## Subtítulo\n\nTexto.\n",
        }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            with Horizontal(id="workspace"):
                with Vertical(id="sidebar"):
                    yield Static("🌳 Yggdrasil — Reinos", classes="panel-title")
                    yield RuneDirectoryTree(self.root, id="file-tree")
                with Vertical(id="chat-panel"):
                    yield Static("💬 Hlidskjalf — Oráculo", classes="panel-title")
                    yield RichLog(id="chat-log", highlight=True, markup=True)
                with Vertical(id="editor-panel"):
                    yield Static("📜 Runas — Editor", classes="panel-title")
                    with TabbedContent(id="editor-tabs"):
                        yield TabPane(
                            "Bienvenida",
                            Static("Seleccioná un archivo para empezar."),
                            id="tab-welcome",
                        )
                    yield Static("Ningún archivo abierto", id="editor-info", classes="editor-info")
            with Horizontal(id="input-bar"):
                yield Input(placeholder="Escribí tu mensaje para Lilith…", id="chat-input")
                yield Button("Enviar", id="send-button", variant="primary")
            with Vertical(id="terminal-panel"):
                yield Static("⚡ Terminal — Midgard", id="terminal-title")
                yield RichLog(id="terminal-log", highlight=True, markup=True)
                yield Input(placeholder="> comando", id="terminal-input")
            with Horizontal(id="status-bar"):
                yield Static(self._status_left(), id="status-left", classes="status-left")
                yield Static("", id="status-center", classes="status-center")
                yield Static(self._status_right(), id="status-right", classes="status-right")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._title
        self.sub_title = f"{self.session.config.provider} / {self.session.config.model}"
        self.register_theme(_NORSE_THEME)
        self.register_theme(_NORSE_LIGHT_THEME)
        self.theme = self.ide_config.theme if self.ide_config.theme in self.available_themes else "norse-dark"
        if self._show_splash:
            self.push_screen(SplashScreen())
        self.run_worker(
            self._welcome_typewriter_worker(),
            exclusive=False,
        )
        self._update_status()
        self._restore_session()
        self.realm_manager.auto_index()
        self.realm_manager.save()
        self._load_plugins()
        if self.ide_config.auto_reload:
            self.run_worker(self._auto_reload_worker(), exclusive=False)
        self.run_worker(self._auto_save_files_worker(), exclusive=False)
        self.run_worker(self._session_persistence_worker(), exclusive=False)

    def _restore_session(self) -> None:
        """Reopen files, active tab, cursor positions and UI layout from the last session."""
        # Restore layout first so further actions operate on the intended sizes.
        try:
            terminal = self.query_one("#terminal-panel")
            terminal.styles.height = self.ide_config.terminal_height
            self._terminal_normal_height = self.ide_config.terminal_height
        except Exception:
            pass
        if self.ide_config.sidebar_width is not None:
            try:
                self.query_one("#sidebar").styles.width = self.ide_config.sidebar_width
            except Exception:
                pass
        if self.ide_config.zen_mode:
            self.action_toggle_zen_mode()
        if self.ide_config.terminal_fullscreen:
            self.action_toggle_terminal_fullscreen()

        # Reopen previously open files.
        for rel in self.ide_config.open_files:
            path = self.root / rel
            if path.exists() and path.is_file():
                self._open_file(path)

        # Focus the last active tab.
        if self.ide_config.active_file:
            active_path = self.root / self.ide_config.active_file
            if active_path.exists() and active_path.is_file():
                self._open_file(active_path)

        # Restore cursor positions for each reopened file.
        for rel, (row, col) in self.ide_config.cursor_positions.items():
            path = self.root / rel
            tab_id: str | None = None
            for tid, p in self._tab_paths.items():
                if p == path:
                    tab_id = tid
                    break
            if not tab_id:
                continue

            def _apply_cursor(t: str = tab_id, r: int = row, c: int = col) -> None:
                try:
                    editor = self.query_one(f"#editor-{t}", TextArea)
                    editor.cursor_location = (r, c)
                    editor.scroll_cursor_visible()
                except Exception:
                    pass

            self.call_after_refresh(_apply_cursor)

    async def _welcome_typewriter_worker(self) -> None:
        """Write the welcome message line-by-line with a subtle typewriter effect."""
        chat_log = self.query_one("#chat-log", RichLog)
        lines = [
            "[bold green]Lilith[/] despierta en [bold]$primary-lighten-2$[/].",
            "Navegá el árbol de reinos, abrí archivos, editá código y consultá al oráculo.",
            "[dim]Atajos: Ctrl+Q salir | Ctrl+P buscar | Ctrl+E recientes | Ctrl+F find | Ctrl+H replace | Ctrl+G línea | Ctrl+Shift+F grep | Ctrl+Shift+H replace proyecto | Ctrl+Shift+G git | Ctrl+Shift+D diff | Ctrl+Shift+B blame | Ctrl+Shift+Y historial | Ctrl+Shift+N notif. | Ctrl+, config | Ctrl+Shift+M zen | Ctrl+Shift+` term-fs | Ctrl+T tema | Ctrl+M markdown | Esc cancelar[/]",
        ]
        for line in lines:
            chat_log.write(line)
            await asyncio.sleep(0.15)

    def _load_plugins(self) -> None:
        """Discover and register plugins from .yggdrasil/plugins/."""
        try:
            self.plugin_manager.load_all()
            loaded = self.plugin_manager.register_all(self)
            if loaded:
                self._chat_system(f"[dim]Plugins cargados: {', '.join(loaded)}[/]")
        except Exception:
            pass

    def notify(
        self,
        message: Any,
        title: str = "",
        *,
        severity: str = "information",
        timeout: float = 3.0,
    ) -> None:
        """Show a toast and keep a rolling history for Ctrl+Shift+N."""
        self._toast_history.append(
            {
                "message": str(message),
                "title": title,
                "severity": severity,
                "time": datetime.now(UTC).isoformat(),
            }
        )
        if len(self._toast_history) > 250:
            self._toast_history = self._toast_history[-250:]
        super().notify(
            message,
            title=title,
            severity=severity,
            timeout=timeout,
        )

    # ── Event handlers ──────────────────────────────────────────────

    async def _auto_reload_worker(self) -> None:
        """Periodically reload open files if they changed on disk."""
        from textual.worker import get_current_worker

        worker = get_current_worker()
        while not worker.is_cancelled:
            await asyncio.sleep(self.ide_config.auto_reload_interval)
            if worker.is_cancelled:
                break
            for tab_id, path in list(self._open_files.items()):
                if tab_id in self._modified:
                    continue
                if not path.exists():
                    continue
                mtime = path.stat().st_mtime
                if self._file_mtimes.get(path) == mtime:
                    continue
                self._file_mtimes[path] = mtime
                self.call_from_thread(self._refresh_editor_tab, tab_id, path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._send_message()
        elif event.input.id == "terminal-input":
            self._run_terminal_command()

    def on_key(self, event: Key) -> None:
        """Cycle through terminal/chat history with up/down arrows."""
        focused = self.focused
        if not focused:
            return
        if focused.id == "terminal-input":
            if event.key == "up":
                event.stop()
                if self._terminal_history and self._terminal_history_index < len(self._terminal_history) - 1:
                    self._terminal_history_index += 1
                    focused.value = self._terminal_history[-(self._terminal_history_index + 1)]
            elif event.key == "down":
                event.stop()
                if self._terminal_history_index > 0:
                    self._terminal_history_index -= 1
                    focused.value = self._terminal_history[-(self._terminal_history_index + 1)]
                else:
                    self._terminal_history_index = -1
                    focused.value = ""
        elif focused.id == "chat-input":
            if event.key == "up":
                event.stop()
                if self._chat_history and self._chat_history_index < len(self._chat_history) - 1:
                    self._chat_history_index += 1
                    focused.value = self._chat_history[-(self._chat_history_index + 1)]
            elif event.key == "down":
                event.stop()
                if self._chat_history_index > 0:
                    self._chat_history_index -= 1
                    focused.value = self._chat_history[-(self._chat_history_index + 1)]
                else:
                    self._chat_history_index = -1
                    focused.value = ""
        # Keep cursor position in the status bar in sync.
        if isinstance(focused, TextArea) and focused.id and focused.id.startswith("editor-"):
            self._update_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            self._send_message()

    # ── Actions ─────────────────────────────────────────────────────

    def action_show_help(self) -> None:
        self._chat_system(
            "[bold]Lilith IDE[/]\n"
            "• Click / Enter en un archivo del árbol para abrirlo en una tab.\n"
            "• Ctrl+P busca archivos, Ctrl+E archivos recientes, Ctrl+F busca en archivo, Ctrl+H reemplaza en archivo.\n"
            "• Ctrl+G ir a línea, Ctrl+Shift+O outline.\n"
            "• Ctrl+Shift+F grep en proyecto (navega a la línea), Ctrl+Shift+H reemplazar en proyecto.\n"
            "• Ctrl+Shift+G git status/diff, Ctrl+Shift+Alt+C commit, Ctrl+Shift+D diff lado a lado, Ctrl+Shift+B blame.\n"
            "• Ctrl+M alterna preview markdown, Ctrl+Shift+N historial de notificaciones, Ctrl+, config.\n"
            "• Ctrl+Shift+M zen mode, Ctrl+Shift+` terminal fullscreen.\n"
            "• Ctrl+T cambia tema (norse-dark, norse-light, textual-dark, textual-light; persiste en ~/.yggdrasil/ide.yaml).\n"
            "• Ctrl+` enfoca la terminal; flechas ↑↓ recorren historial de comandos y chat. /run <cmd> y /test ejecutan comandos.\n"
            "• Ctrl+S guarda el archivo activo (con backup automático) y ejecuta run-on-save si está configurado.\n"
            "• Esc cancela la generación actual.\n"
            "• /patch pega un diff y lo aplica tras revisión.\n"
            "• /git-stash, /git-checkout <branch>, /git-branch <nombre>, /git-commit <mensaje>.\n"
            "• /new <template> <ruta> — templates: py, test, class, md.\n"
            "• Ctrl+Q cierra la consola."
        )

    def action_open_grep(self) -> None:
        def _on_select(result: GrepResult | None) -> None:
            if result:
                self._open_file(result.path, line=result.line)
                self._chat_system(f"Resultado grep: {_shorten_path(result.path, self.root)}:{result.line}")
        self.push_screen(GrepScreen(self.root), _on_select)

    def action_open_history(self) -> None:
        def _on_select(path: Path | None) -> None:
            if path:
                self._load_conversation(path)
        self.push_screen(HistoryScreen(CONFIG_DIR / "conversations"), _on_select)

    def action_toast_history(self) -> None:
        self.push_screen(ToastHistoryScreen(self._toast_history))

    def action_open_config(self) -> None:
        def _on_save(config: IDEConfig | None) -> None:
            if config is None:
                return
            self.ide_config = config
            self.ide_config.save()
            self.theme = config.theme if config.theme in self.available_themes else self.theme
            self.notify("Configuración guardada", severity="information")
        self.push_screen(ConfigScreen(self.ide_config), _on_save)

    def action_show_diff(self) -> None:
        self.push_screen(DiffScreen(self.root, self.current_file))

    def action_toggle_theme(self) -> None:
        cycle = ["norse-dark", "norse-light", "textual-dark", "textual-light"]
        try:
            idx = cycle.index(self.theme)
        except ValueError:
            idx = -1
        next_theme = cycle[(idx + 1) % len(cycle)]
        self.theme = next_theme
        self.ide_config.theme = next_theme
        self.ide_config.save()
        self.notify(f"Tema: {next_theme}", severity="information")

    def action_quit(self) -> None:
        """Save the current session and exit the IDE."""
        self._save_session()
        self.exit()

    def _save_session(self) -> None:
        """Persist the current IDE session (files, cursor, layout)."""
        try:
            self._save_session_state()
            self.ide_config.zen_mode = self._zen_mode
            self.ide_config.terminal_fullscreen = self._terminal_fullscreen
            if not self._terminal_fullscreen:
                try:
                    terminal = self.query_one("#terminal-panel")
                    self.ide_config.terminal_height = int(terminal.styles.height)
                except Exception:
                    pass
            else:
                self.ide_config.terminal_height = self._terminal_normal_height
            try:
                sidebar = self.query_one("#sidebar")
                width = sidebar.styles.width
                self.ide_config.sidebar_width = int(width) if width is not None else None
            except Exception:
                pass
            self.ide_config.save()
        except Exception:
            pass

    async def _session_persistence_worker(self) -> None:
        """Periodically save the current IDE session every 30 seconds."""
        from textual.worker import get_current_worker

        worker = get_current_worker()
        while not worker.is_cancelled:
            await asyncio.sleep(30)
            if worker.is_cancelled:
                break
            self.call_from_thread(self._save_session)

    def action_toggle_zen_mode(self) -> None:
        """Toggle distraction-free editor mode."""
        self._zen_mode = not self._zen_mode
        ids = ["#sidebar", "#chat-panel", "#input-bar", "#terminal-panel"]
        if self._zen_mode:
            for widget_id in ids:
                try:
                    widget = self.query_one(widget_id)
                    self._zen_mode_displays[widget_id] = str(widget.styles.display)
                    widget.styles.display = "none"
                except Exception:
                    pass
            self.notify("Zen mode activado", severity="information")
        else:
            for widget_id in ids:
                try:
                    widget = self.query_one(widget_id)
                    widget.styles.display = self._zen_mode_displays.get(widget_id, "block")
                except Exception:
                    pass
            self.notify("Zen mode desactivado", severity="information")

    def action_command_palette(self) -> None:
        """Open a searchable command palette with commands, files and runestones."""
        items: list[PaletteItem] = []

        # IDE commands.
        commands = [
            ("Guardar archivo", self.action_save_file),
            ("Buscar archivo", self.action_open_file_search),
            ("Archivos recientes", self.action_recent_files),
            ("Grep en proyecto", self.action_open_grep),
            ("Reemplazar en proyecto", self.action_project_find_replace),
            ("Git status/diff", self.action_open_git),
            ("Git commit", self.action_commit),
            ("Git log", self.action_show_git_log),
            ("Diff lado a lado", self.action_show_diff),
            ("Historial", self.action_open_history),
            ("Notificaciones", self.action_toast_history),
            ("Configuración", self.action_open_config),
            ("Zen mode", self.action_toggle_zen_mode),
            ("Terminal fullscreen", self.action_toggle_terminal_fullscreen),
            ("Tema claro/oscuro", self.action_toggle_theme),
            ("Cerrar tab", self.action_close_tab),
            ("Reabrir tab cerrada", self.action_reopen_closed_tab),
            ("Formatear archivo", self.action_format_file),
            ("Outline", self.action_show_outline),
            ("Contexto", self._show_context),
            ("Runestones", self._list_runestones),
            ("Ayuda", self.action_show_help),
        ]
        for label, callback in commands:
            items.append(PaletteItem(label=label, callback=callback, category="Comandos"))

        # Open files / tabs.
        for tab_id, path in self._tab_paths.items():
            rel = _shorten_path(path, self.root)
            items.append(
                PaletteItem(
                    label=rel,
                    callback=lambda p=path: self._open_file(p),
                    category="Tabs abiertos",
                    search_text=str(path),
                )
            )

        # Recent files.
        for path in self._recent_files[:20]:
            rel = _shorten_path(path, self.root)
            items.append(
                PaletteItem(
                    label=rel,
                    callback=lambda p=path: self._open_file(p),
                    category="Archivos recientes",
                    search_text=str(path),
                )
            )

        # Runestones.
        for stone in self.runestone_forge.list():
            items.append(
                PaletteItem(
                    label=f"{stone.title} ({stone.language})",
                    callback=lambda sid=stone.id: self._preview_runestone(sid),
                    category="Runestones",
                    search_text=stone.id,
                )
            )

        def _on_select(callback: Callable[[], Any] | None) -> None:
            if callback:
                callback()

        self.push_screen(CommandPaletteScreen(items), _on_select)

    # ── UI updates ────────────────────────────────────────────────────

    def _chat_user(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[bold cyan]Tú:[/] {text}")

    def _chat_assistant_chunk(self, chunk: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(chunk, scroll_end=True)

    def _chat_system(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[dim]{text}[/]")

    def _chat_tool_call(self, name: str, args: Any) -> None:
        log = self.query_one("#chat-log", RichLog)
        args_str = json.dumps(args, ensure_ascii=False, indent=2) if args else "{}"
        log.write(f"\n[bold yellow]⚡ Tool:[/] [yellow]{name}[/]\n[dim]{args_str}[/]")

    def _chat_tool_result(self, name: str, content: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        preview = content[:500] + "…" if len(content) > 500 else content
        log.write(f"\n[bold green]✓ Resultado {name}:[/]\n[dim]{preview}[/]")

    def _load_conversation(self, path: Path) -> None:
        try:
            import json as _json

            data = _json.loads(path.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            self.session.history.clear()
            self.session.history.extend(messages)
            log = self.query_one("#chat-log", RichLog)
            log.clear()
            log.write(f"[dim]Conversación cargada: {path.name} ({len(messages)} mensajes)[/]")
        except Exception as exc:
            self.notify(f"Error cargando historial: {exc}", severity="error")

    # ── Status bar ────────────────────────────────────────────────────

    def _status_left(self) -> str:
        cfg = self.session.config
        branch = self._git_branch()
        branch_info = f"   {branch}" if branch else ""
        return f"🌿 {cfg.provider} / {cfg.model}{branch_info}"

    def _status_center(self) -> str:
        if not self.is_mounted:
            return ""
        if self._thinking:
            runes = ["ᚠ", "ᚢ", "ᚦ", "ᚨ", "ᚱ", "ᚲ", "ᚷ", "ᚹ"]
            rune = runes[self._thinking_frame % len(runes)]
            return f"{rune} Lilith está forjando runas…"
        try:
            tab_id = self._current_tab_id()
            if tab_id and tab_id in self._modified:
                return "● modificado"
        except Exception:
            pass
        return ""

    def _status_right(self) -> str:
        u = self._token_usage
        cursor = ""
        if self.is_mounted:
            try:
                editor = self._current_editor()
                if editor:
                    row, col = editor.cursor_location
                    cursor = f"Ln {row + 1}, Col {col + 1}  "
            except Exception:
                pass
        return f"{cursor}Tokens ↑{u['prompt']} ↓{u['completion']} Σ{u['total']}"

    def _update_status(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#status-left", Static).update(self._status_left())
        self.query_one("#status-center", Static).update(self._status_center())
        self.query_one("#status-right", Static).update(self._status_right())

# ── Public entry point ──────────────────────────────────────────────

def run_ide(session: AgentSession, root: Path | None = None) -> None:
    """Launch the Lilith IDE TUI."""
    app = LilithIDEApp(session, root=root)
    app.run()
