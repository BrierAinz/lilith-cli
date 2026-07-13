"""Default key bindings for the Lilith IDE app."""

from textual.binding import Binding

IDE_BINDINGS = [
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
    Binding("ctrl+equal", "zoom_in", "Zoom +"),
    Binding("ctrl+minus", "zoom_out", "Zoom -"),
    Binding("ctrl+shift+z", "toggle_soft_wrap", "Soft wrap"),
    Binding("ctrl+shift+n", "toast_history", "Notificaciones"),
    Binding("ctrl+comma", "open_config", "Config"),
    Binding("ctrl+shift+d", "show_diff", "Diff"),
    Binding("ctrl+shift+m", "toggle_zen_mode", "Zen mode"),
    Binding("ctrl+shift+grave", "toggle_terminal_fullscreen", "Terminal fullscreen"),
    Binding("ctrl+grave", "focus_terminal", "Terminal"),
    Binding("escape", "cancel_generation", "Cancelar"),
    Binding("f1", "show_help", "Ayuda"),
]

# Multi-terminal shortcuts. Kept in their own list because app.py still
# hardcodes its BINDINGS (and Textual does not merge BINDINGS from plain
# mixins): TerminalMixin.on_ready registers these dynamically via App.bind.
# When app.py migrates to IDE_BINDINGS they will come along for free.
TERMINAL_BINDINGS = [
    Binding("ctrl+shift+u", "new_terminal", "Nueva terminal"),
    Binding("ctrl+shift+w", "close_terminal", "Cerrar terminal"),
    Binding("ctrl+shift+j", "next_terminal", "Terminal sig."),
    Binding("ctrl+shift+k", "prev_terminal", "Terminal ant."),
]

IDE_BINDINGS.extend(TERMINAL_BINDINGS)
