"""EditorMixin — file/tab management, edit operations, LSP did* and modal find/replace/outline.

Owns the editor surface area: open/close/reopen tabs, save, format, find/replace,
go-to-line, outline, bookmarks, zoom, soft wrap, markdown preview, run/debug current
file, LSP didOpen/didChange/didSave + completion/hover/definition/diagnostics.

State that lives here (initialised in App.__init__):
    _open_files: dict[str, Path]    -- tab_id -> path
    _tab_paths: dict[str, Path]     -- tab_id -> path (mirror of _open_files)
    _file_mtimes: dict[Path, float] -- path -> last seen mtime
    _file_versions: dict[Path, int] -- path -> LSP text version counter
    _modified: set[str]             -- tab_ids with unsaved edits
    _closed_tabs: list[Path]        -- stack of recently closed tabs (for reopen)
    _recent_files: list[Path]       -- MRU list of opened files
    _editor_id_counter: int         -- monotonic id generator for new tabs
    _markdown_mode: bool            -- toggle for .md preview render
    _bookmarks: dict[Path, set[int]] -- per-file bookmarked lines
    _zoom_level: int                -- relative zoom deltas
    _soft_wrap: bool                -- soft wrap toggle

The App owns:
    current_file: reactive[Path | None] -- the editor panel's current path

Cross-domain calls (resolved via the composed LilithIDEApp instance):
    self._chat_system (AgentMixin)         -- write to chat log
    self._run_on_save_worker              -- defined here, called from save_file
    self.notify (App override)            -- toasts
    self.lsp_manager / PluginManager      -- from lsp/, package-level singletons
    self.run_worker                       -- Textual App base method
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from rich.markdown import Markdown
from textual.widgets import Static, TabbedContent, TabPane, TextArea

from ..screens.modals import (
    CompletionScreen,
    DiagnosticsScreen,
    FileSearchScreen,
    FindReplaceScreen,
    FindScreen,
    GoToLineScreen,
    HoverScreen,
    OutlineScreen,
    ProjectFindReplaceScreen,
)
from ..utils.helpers import (
    _backup_path,
    _detect_language,
    _shorten_path,
)

if TYPE_CHECKING:
    pass


def _uri_to_path(uri: str) -> Path:
    """Convert a file:// URI to a filesystem Path, handling Windows URIs."""
    if uri.startswith("file://"):
        parsed = urlparse(uri).path
        # Windows file URIs look like file:///C:/foo; strip the leading slash.
        if parsed.startswith("/") and len(parsed) > 2 and parsed[2] == ":":
            parsed = parsed[1:]
        return Path(parsed)
    return Path(uri)


class EditorMixin:
    """File/tab management, edit operations, and LSP UI plumbing."""

    # ── Tab bookkeeping ────────────────────────────────────────────

    def _current_tab_id(self) -> str | None:
        if not self.is_mounted:  # type: ignore[attr-defined]
            return None
        try:
            tabs = self.query_one("#editor-tabs", TabbedContent)  # type: ignore[attr-defined]
            return tabs.active
        except Exception:
            return None

    def _current_editor(self) -> TextArea | None:
        if not self.is_mounted:  # type: ignore[attr-defined]
            return None
        tab_id = self._current_tab_id()
        if not tab_id or tab_id == "tab-welcome":
            return None
        if tab_id not in self._tab_paths:  # type: ignore[attr-defined]
            return None
        try:
            return self.query_one(f"#editor-{tab_id}", TextArea)  # type: ignore[attr-defined]
        except Exception:
            return None

    def _save_session_state(self) -> None:
        """Collect open tabs, active tab and cursor positions into ``self.ide_config``."""
        cfg = self.ide_config  # type: ignore[attr-defined]
        cfg.open_files = [
            _shorten_path(path, self.root)  # type: ignore[attr-defined]
            for path in self._tab_paths.values()  # type: ignore[attr-defined]
        ]
        active_tab = self._current_tab_id()
        if active_tab and active_tab in self._tab_paths:  # type: ignore[attr-defined]
            cfg.active_file = _shorten_path(
                self._tab_paths[active_tab], self.root  # type: ignore[attr-defined]
            )
        else:
            cfg.active_file = ""
        positions: dict[str, tuple[int, int]] = {}
        for tab_id, path in self._tab_paths.items():  # type: ignore[attr-defined]
            try:
                editor = self.query_one(f"#editor-{tab_id}", TextArea)
                row, col = editor.cursor_location
                positions[_shorten_path(path, self.root)] = (row, col)  # type: ignore[attr-defined]
            except Exception:
                pass
        cfg.cursor_positions = positions

    def _get_editor_selection(self) -> str:
        """Return the selected text in the current editor, if any."""
        editor = self._current_editor()
        if not editor:
            return ""
        try:
            return editor.selected_text
        except Exception:
            return ""

    # ── Open / close / reopen ──────────────────────────────────────

    def _open_file(self, path: Path, line: int | None = None) -> None:
        """Open a file in a new tab or focus existing tab; optionally jump to a line."""
        if not path.is_file():
            return
        # Track recent files (most recent first, no duplicates, max 50).
        if path in self._recent_files:  # type: ignore[attr-defined]
            self._recent_files.remove(path)  # type: ignore[attr-defined]
        self._recent_files.insert(0, path)  # type: ignore[attr-defined]
        self._recent_files = self._recent_files[:50]  # type: ignore[attr-defined]
        # Reuse existing tab for this path.
        existing_tab: str | None = None
        for tab, tab_path in self._tab_paths.items():  # type: ignore[attr-defined]
            if tab_path == path:
                existing_tab = tab
                break
        if existing_tab:
            tabs = self.query_one("#editor-tabs", TabbedContent)  # type: ignore[attr-defined]
            tabs.active = existing_tab
            if line is not None:
                self._jump_to_line(line)
            return

        self._editor_id_counter += 1  # type: ignore[attr-defined]
        tab_id = f"tab-{self._editor_id_counter}"  # type: ignore[attr-defined]
        self._open_files[tab_id] = path  # type: ignore[attr-defined]
        self._tab_paths[tab_id] = path  # type: ignore[attr-defined]
        self._file_mtimes[path] = path.stat().st_mtime  # type: ignore[attr-defined]
        tabs = self.query_one("#editor-tabs", TabbedContent)  # type: ignore[attr-defined]
        content_widget = self._build_editor_widget(path, tab_id)
        tabs.add_pane(TabPane(path.name, content_widget, id=tab_id))
        tabs.active = tab_id
        self.current_file = path  # type: ignore[attr-defined]
        if line is not None:
            self._jump_to_line(line)
        self._chat_system(f"Abierto: {_shorten_path(path, self.root)}")  # type: ignore[attr-defined]
        # Notify LSP server about the opened document.
        language = _detect_language(path)
        if language:
            self.run_worker(self._lsp_did_open(path, language), exclusive=False)  # type: ignore[attr-defined]

    def action_close_tab(self) -> None:
        tab_id = self._current_tab_id()
        if not tab_id or tab_id == "tab-welcome":
            return
        path = self._open_files.pop(tab_id, None)  # type: ignore[attr-defined]
        self._tab_paths.pop(tab_id, None)  # type: ignore[attr-defined]
        if path:
            self._closed_tabs.append(path)  # type: ignore[attr-defined]
            self._file_mtimes.pop(path, None)  # type: ignore[attr-defined]
        tabs = self.query_one("#editor-tabs", TabbedContent)  # type: ignore[attr-defined]
        tabs.remove_pane(tab_id)
        self._modified.discard(tab_id)  # type: ignore[attr-defined]
        # Update current_file to whatever tab is now active.
        active = tabs.active
        self.current_file = self._tab_paths.get(active)  # type: ignore[attr-defined]
        self._update_editor_info()

    def action_reopen_closed_tab(self) -> None:
        if not self._closed_tabs:  # type: ignore[attr-defined]
            self.notify("No hay tabs cerradas", severity="warning")  # type: ignore[attr-defined]
            return
        path = self._closed_tabs.pop()  # type: ignore[attr-defined]
        self._open_file(path)

    # ── Editor widget construction + refresh ───────────────────────

    def _build_editor_widget(self, path: Path, tab_id: str) -> TextArea | Static:
        """Create either a TextArea or a markdown preview for a file."""
        text = path.read_text(encoding="utf-8", errors="replace")
        widget_id = f"editor-{tab_id}"
        if path.suffix.lower() == ".md" and self._markdown_mode:  # type: ignore[attr-defined]
            return Static(Markdown(text), id=widget_id)
        editor = TextArea(text=text, read_only=False, show_line_numbers=True, id=widget_id)
        language = _detect_language(path)
        if language:
            try:
                editor.language = language
            except Exception:
                pass
        return editor

    def _refresh_editor_tab(self, tab_id: str, path: Path) -> None:
        """Refresh a single editor tab from disk."""
        if tab_id in self._modified:  # type: ignore[attr-defined]
            return
        if tab_id not in self._tab_paths:  # type: ignore[attr-defined]
            return
        try:
            widget_id = f"editor-{tab_id}"
            try:
                old = self.query_one(f"#{widget_id}")  # type: ignore[attr-defined]
                old.remove()
            except Exception:
                pass
            new_widget = self._build_editor_widget(path, tab_id)
            tabs = self.query_one("#editor-tabs", TabbedContent)  # type: ignore[attr-defined]
            pane = tabs.get_pane(tab_id)
            if pane:
                pane.compose_add_child(new_widget)
            self._update_editor_info()
        except Exception:
            pass

    def _refresh_current_editor(self) -> None:
        if not self.current_file:  # type: ignore[attr-defined]
            return
        editor = self._current_editor()
        if editor:
            editor.text = self.current_file.read_text(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            tab_id = self._current_tab_id()
            if tab_id:
                self._modified.discard(tab_id)  # type: ignore[attr-defined]
            self._update_editor_info()

    # ── Editor info / status bar updates ───────────────────────────

    def _update_editor_info(self) -> None:
        info = self.query_one("#editor-info", Static)  # type: ignore[attr-defined]
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            info.update("Ningún archivo abierto")
            self._update_status()  # type: ignore[attr-defined]
            return
        tab_id = self._current_tab_id()
        modified = " ●" if tab_id in self._modified else ""  # type: ignore[attr-defined]
        diagnostics = self._current_diagnostics.get(path.resolve(), [])  # type: ignore[attr-defined]
        if diagnostics:
            errors = sum(1 for d in diagnostics if d.get("severity", 1) == 1)
            warnings = sum(1 for d in diagnostics if d.get("severity", 1) == 2)
            diag_text = f"  |  {errors} errores / {warnings} warnings"
        else:
            diag_text = ""
        info.update(f"{_shorten_path(path, self.root)}{modified}{diag_text}")  # type: ignore[attr-defined]
        self._update_status()  # type: ignore[attr-defined]
        # Refresh git status/blame asynchronously.
        self.run_worker(self._git_info_worker(), exclusive=False)  # type: ignore[attr-defined]

    def _update_editor_info_text(self, text: str) -> None:
        """Update the editor info bar without triggering status updates."""
        try:
            info = self.query_one("#editor-info", Static)  # type: ignore[attr-defined]
            info.update(text)
        except Exception:
            pass

    def _on_lsp_diagnostics(self, uri: str, diagnostics: list[dict[str, Any]]) -> None:
        """Store incoming diagnostics and refresh the info bar when relevant."""
        try:
            path = _uri_to_path(uri)
        except Exception:
            return
        resolved = path.resolve()
        self._current_diagnostics[resolved] = list(diagnostics)  # type: ignore[attr-defined]
        current = self.current_file  # type: ignore[attr-defined]
        if current and resolved == current.resolve():
            self._update_editor_info()

    def watch_current_file(self, path: Path | None) -> None:
        if not self.is_mounted:  # type: ignore[attr-defined]
            return
        self._update_editor_info()

    # ── Navigation ─────────────────────────────────────────────────

    def _jump_to_line(self, line: int) -> None:
        """Move the cursor to the given 1-based line in the current editor."""

        def _do_jump() -> None:
            editor = self._current_editor()
            if not editor:
                return
            try:
                editor.cursor_location = (max(1, line) - 1, 0)
                editor.scroll_cursor_visible()
            except Exception:
                pass

        self.call_after_refresh(_do_jump)  # type: ignore[attr-defined]

    def action_go_to_line(self) -> None:
        editor = self._current_editor()
        if not editor:
            self.notify("No hay editor activo", severity="warning")  # type: ignore[attr-defined]
            return

        def _on_line(line: int | None) -> None:
            if line is not None:
                editor.cursor_location = (line - 1, 0)
                editor.scroll_cursor_visible()

        self.push_screen(GoToLineScreen(), _on_line)  # type: ignore[attr-defined]

    def action_show_outline(self) -> None:
        def _on_select(symbol: tuple[int, str] | None) -> None:
            if symbol:
                editor = self._current_editor()
                if editor:
                    editor.cursor_location = (symbol[0] - 1, 0)
                    editor.scroll_cursor_visible()
        self.push_screen(OutlineScreen(self.current_file), _on_select)  # type: ignore[attr-defined]

    # ── Save + run on save ─────────────────────────────────────────

    def action_save_file(self) -> None:
        editor = self._current_editor()
        if not editor or not self.current_file:  # type: ignore[attr-defined]
            self.notify("No hay archivo abierto para guardar", severity="warning")  # type: ignore[attr-defined]
            return
        try:
            backup = _backup_path(self.current_file)  # type: ignore[attr-defined]
            backup.write_text(self.current_file.read_text(encoding="utf-8"), encoding="utf-8")  # type: ignore[attr-defined]
            self.current_file.write_text(editor.text, encoding="utf-8")  # type: ignore[attr-defined]
            tab_id = self._current_tab_id()
            if tab_id:
                self._modified.discard(tab_id)  # type: ignore[attr-defined]
            self._update_editor_info()
            self.notify(f"Guardado: {self.current_file.name}", severity="information")  # type: ignore[attr-defined]
            if self.ide_config.run_on_save:  # type: ignore[attr-defined]
                self.run_worker(self._run_on_save_worker(), exclusive=False)  # type: ignore[attr-defined]
            language = _detect_language(self.current_file)  # type: ignore[attr-defined]
            if language:
                self.run_worker(
                    self.lsp_manager.did_save(self.current_file, language),  # type: ignore[attr-defined]
                    exclusive=False,
                )
        except Exception as exc:
            self.notify(f"Error guardando: {exc}", severity="error")  # type: ignore[attr-defined]

    async def _run_on_save_worker(self) -> None:
        """Execute the configured post-save command."""
        command = self.ide_config.run_on_save  # type: ignore[attr-defined]
        if not command:
            return
        self._chat_system(f"[dim]Run on save: {command}[/]")  # type: ignore[attr-defined]
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            stdout, stderr = await proc.communicate()
            if stdout:
                self.call_from_thread(self._chat_system, f"[dim]{stdout.decode('utf-8', errors='replace').strip()}[/]")  # type: ignore[attr-defined]
            if stderr:
                self.call_from_thread(self._chat_system, f"[red]{stderr.decode('utf-8', errors='replace').strip()}[/]")  # type: ignore[attr-defined]
            if proc.returncode != 0:
                self.call_from_thread(self.notify, f"Run on save falló (exit {proc.returncode})", severity="warning")  # type: ignore[attr-defined]
            else:
                self.call_from_thread(self.notify, "Run on save completado", severity="information")  # type: ignore[attr-defined]
        except Exception as exc:
            self.call_from_thread(self.notify, f"Run on save error: {exc}", severity="error")  # type: ignore[attr-defined]

    # ── Markdown / zoom / soft wrap / bookmarks / copy path ────────

    def action_toggle_markdown(self) -> None:
        self._markdown_mode = not self._markdown_mode  # type: ignore[attr-defined]
        self.notify(  # type: ignore[attr-defined]
            f"Markdown preview: {'on' if self._markdown_mode else 'off'}",  # type: ignore[attr-defined]
            severity="information",
        )
        if self.current_file:  # type: ignore[attr-defined]
            self._refresh_editor_tab(f"tab-{self.current_file.as_posix()}", self.current_file)  # type: ignore[attr-defined]

    def action_zoom_in(self) -> None:
        self._zoom_level += 1  # type: ignore[attr-defined]
        self._apply_zoom()

    def action_zoom_out(self) -> None:
        self._zoom_level = max(-3, self._zoom_level - 1)  # type: ignore[attr-defined]
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        editor = self._current_editor()
        if editor:
            base_size = 12
            editor.styles.text_style = f"size: {base_size + self._zoom_level}"  # type: ignore[attr-defined]
        self.notify(f"Zoom: {self._zoom_level}", severity="information")  # type: ignore[attr-defined]

    def action_toggle_soft_wrap(self) -> None:
        self._soft_wrap = not self._soft_wrap  # type: ignore[attr-defined]
        editor = self._current_editor()
        if editor:
            editor.soft_wrap = self._soft_wrap  # type: ignore[attr-defined]
        self.notify(  # type: ignore[attr-defined]
            f"Soft wrap: {'on' if self._soft_wrap else 'off'}",  # type: ignore[attr-defined]
            severity="information",
        )

    def action_toggle_bookmark(self) -> None:
        path = self.current_file  # type: ignore[attr-defined]
        editor = self._current_editor()
        if not path or not editor:
            return
        line = editor.cursor_location[0] + 1
        marks = self._bookmarks.setdefault(path, set())  # type: ignore[attr-defined]
        if line in marks:
            marks.discard(line)
            self.notify(f"Bookmark removido: línea {line}", severity="information")  # type: ignore[attr-defined]
        else:
            marks.add(line)
            self.notify(f"Bookmark agregado: línea {line}", severity="information")  # type: ignore[attr-defined]

    def action_copy_path(self) -> None:
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        rel = _shorten_path(path, self.root)  # type: ignore[attr-defined]
        self._copy_to_clipboard(rel)
        self.notify(f"Path copiado: {rel}", severity="information")  # type: ignore[attr-defined]

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            subprocess.run(["clip.exe"], input=text.encode("utf-8"), check=True, capture_output=True)
        except Exception:
            pass

    # ── Find / replace ─────────────────────────────────────────────

    def action_find_in_file(self) -> None:
        editor = self._current_editor()
        if not editor:
            self.notify("No hay editor activo", severity="warning")  # type: ignore[attr-defined]
            return

        def _on_find(query: str | None) -> None:
            if query:
                self._find_next(query)

        self.push_screen(FindScreen(), _on_find)  # type: ignore[attr-defined]

    def _find_next(self, query: str) -> None:
        editor = self._current_editor()
        if not editor or not query:
            return
        text = editor.text
        cursor = editor.cursor_location
        start = editor.document.get_index_from_location(cursor)
        idx = text.find(query, start + 1)
        if idx == -1:
            idx = text.find(query, 0)
        if idx != -1:
            location = editor.document.get_location_from_index(idx)
            editor.cursor_location = location
            editor.scroll_cursor_visible()
            editor.select_text(location, (location[0], location[1] + len(query)))

    def action_find_replace(self) -> None:
        editor = self._current_editor()
        if not editor:
            self.notify("No hay editor activo", severity="warning")  # type: ignore[attr-defined]
            return

        def _on_replace(result: tuple[str, str, str] | None) -> None:
            if not result:
                return
            find_text, replace_text, action = result
            if not find_text:
                return
            if action == "all":
                self._replace_all(find_text, replace_text)
            else:
                self._replace_next(find_text, replace_text)

        self.push_screen(FindReplaceScreen(), _on_replace)  # type: ignore[attr-defined]

    def action_project_find_replace(self) -> None:
        def _on_replace(result: tuple[str, str] | None) -> None:
            if not result:
                return
            find_text, replace_text = result
            if not find_text:
                return
            self.run_worker(self._project_replace_worker(find_text, replace_text), exclusive=True)  # type: ignore[attr-defined]

        self.push_screen(ProjectFindReplaceScreen(), _on_replace)  # type: ignore[attr-defined]

    async def _project_replace_worker(self, find_text: str, replace_text: str) -> None:
        """Replace occurrences of find_text with replace_text across project files."""
        self.call_from_thread(self._chat_system, f"[dim]Buscando '{find_text}' en el proyecto…[/]")  # type: ignore[attr-defined]
        try:
            cmd = self._build_project_search_command(find_text)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace")
            results = self._parse_project_search_output(text)
            if not results:
                self.call_from_thread(self._chat_system, f"[dim]No se encontró '{find_text}'.[/]")  # type: ignore[attr-defined]
                return

            # Group by file.
            files: dict[Path, list[int]] = {}
            for res in results:
                files.setdefault(res.path, []).append(res.line)

            changed: list[str] = []
            for path, _lines in files.items():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    if find_text not in content:
                        continue
                    backup = _backup_path(path)
                    backup.write_text(content, encoding="utf-8")
                    new_content = content.replace(find_text, replace_text)
                    path.write_text(new_content, encoding="utf-8")
                    changed.append(_shorten_path(path, self.root))  # type: ignore[attr-defined]
                    # Refresh editor if the file is open.
                    if self.current_file == path:  # type: ignore[attr-defined]
                        self.call_from_thread(self._refresh_current_editor)  # type: ignore[attr-defined]
                except Exception as exc:
                    self.call_from_thread(self._chat_system, f"[red]Error en {_shorten_path(path, self.root)}:[/] {exc}")  # type: ignore[attr-defined]

            self.call_from_thread(
                self._chat_system,
                f"[green]Reemplazado en {len(changed)} archivos:[/] {', '.join(changed)}",  # type: ignore[attr-defined]
            )
        except Exception as exc:
            self.call_from_thread(self._chat_system, f"[red]Error reemplazando en proyecto:[/] {exc}")  # type: ignore[attr-defined]

    def _build_project_search_command(self, query: str) -> list[str]:
        """Build a project-wide search command preferring ripgrep."""
        if shutil.which("rg"):
            return ["rg", "--line-number", "--no-heading", "--color", "never", "-g", "!.git", "-g", "!*.bak.*", query]
        if shutil.which("grep"):
            return ["grep", "-RIn", "--exclude-dir=.git", "--exclude=*~", "--exclude=*.bak.*", query, "."]
        return ["findstr", "/S", "/N", query, "*"]

    def _parse_project_search_output(self, text: str) -> list:  # returns list[GrepResult]
        """Parse ripgrep/grep output into GrepResult objects."""
        from ..utils.helpers import GrepResult  # local import to avoid cycle at module load

        results: list[GrepResult] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            try:
                path = self.root / parts[0]  # type: ignore[attr-defined]
                ln = int(parts[1])
                txt = parts[2]
            except (ValueError, IndexError):
                continue
            results.append(GrepResult(path, ln, txt))
        return results

    def _replace_next(self, find_text: str, replace_text: str) -> None:
        editor = self._current_editor()
        if not editor:
            return
        text = editor.text
        cursor = editor.cursor_location
        start = editor.document.get_index_from_location(cursor)
        idx = text.find(find_text, start)
        if idx == -1:
            idx = text.find(find_text, 0)
        if idx != -1:
            location = editor.document.get_location_from_index(idx)
            end_location = editor.document.get_location_from_index(idx + len(find_text))
            editor.replace(find_text, replace_text, (location, end_location))
            editor.cursor_location = editor.document.get_location_from_index(idx + len(replace_text))
            editor.scroll_cursor_visible()
            self._mark_current_modified()

    def _replace_all(self, find_text: str, replace_text: str) -> None:
        editor = self._current_editor()
        if not editor:
            return
        new_text = editor.text.replace(find_text, replace_text)
        if new_text != editor.text:
            editor.text = new_text
            self._mark_current_modified()
            self.notify(f"Reemplazadas {editor.text.count(replace_text)} ocurrencias", severity="information")  # type: ignore[attr-defined]

    def _mark_current_modified(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id:
            self._modified.add(tab_id)  # type: ignore[attr-defined]
            self._update_editor_info()

    # ── Format / run / debug current file ──────────────────────────

    def action_format_file(self) -> None:
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        self.run_worker(self._format_worker(path), exclusive=True)  # type: ignore[attr-defined]

    async def _format_worker(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix == ".py":
            commands = [["ruff", "format", str(path)], ["black", str(path)]]
        elif suffix in (".js", ".ts", ".jsx", ".tsx"):
            commands = [["prettier", "--write", str(path)]]
        elif suffix in (".json", ".md", ".yaml", ".yml"):
            commands = [["prettier", "--write", str(path)]]
        else:
            self.call_from_thread(self.notify, f"Formato no soportado para {suffix}", severity="warning")  # type: ignore[attr-defined]
            return

        for cmd in commands:
            if shutil.which(cmd[0]):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    if proc.returncode == 0:
                        self.call_from_thread(self.notify, f"Formateado: {path.name}", severity="information")  # type: ignore[attr-defined]
                        self.call_from_thread(self._refresh_current_editor)  # type: ignore[attr-defined]
                        return
                    else:
                        err = stderr.decode("utf-8", errors="replace")[:200]
                        self.call_from_thread(self.notify, f"Error formateando: {err}", severity="error")  # type: ignore[attr-defined]
                        return
                except Exception as exc:
                    self.call_from_thread(self.notify, f"Error formateando: {exc}", severity="error")  # type: ignore[attr-defined]
                    return
        self.call_from_thread(self.notify, "No se encontró formateador (ruff/black/prettier)", severity="warning")  # type: ignore[attr-defined]

    def action_run_current_file(self) -> None:
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        self._terminal_log(f"> Ejecutando {path.name}…")  # type: ignore[attr-defined]
        self.run_worker(self._run_file_worker(path), exclusive=True)  # type: ignore[attr-defined]

    async def _run_file_worker(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix == ".py":
            cmd = ["python", str(path)]
        elif suffix == ".sh":
            cmd = ["bash", str(path)]
        elif suffix in (".js", ".ts"):
            runner = "node" if suffix == ".js" else "tsx"
            cmd = [runner, str(path)]
        elif suffix in (".go",):
            cmd = ["go", "run", str(path)]
        elif suffix in (".rs",):
            cmd = ["cargo", "run", "--manifest-path", str(path.parent / "Cargo.toml")]
        else:
            self.call_from_thread(self._terminal_log, f"[red]Ejecución no soportada para {suffix}[/]")  # type: ignore[attr-defined]
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            output = ""
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                output += decoded + "\n"
                self.call_from_thread(self._terminal_log, f"[dim]{decoded}[/]")  # type: ignore[attr-defined]
            await proc.wait()
            self.call_from_thread(
                self._terminal_log,
                f"[dim]Exit code: {proc.returncode}[/]",  # type: ignore[attr-defined]
            )
        except Exception as exc:
            self.call_from_thread(self._terminal_log, f"[red]Error ejecutando archivo:[/] {exc}")  # type: ignore[attr-defined]

    def action_debug_current_file(self) -> None:
        """Run the current Python file under pdb (Ctrl+F9)."""
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        if path.suffix.lower() != ".py":
            self.notify("Debugger solo soporta archivos Python", severity="warning")  # type: ignore[attr-defined]
            return
        self._terminal_log(f"> Debug {path.name}…")  # type: ignore[attr-defined]
        self.run_worker(self._debug_worker(path), exclusive=True)  # type: ignore[attr-defined]

    async def _debug_worker(self, path: Path) -> None:
        """Execute a Python file under pdb/ipdb and stream output to the terminal."""
        # ipdb provides the same commands with nicer output when available.
        debugger = "ipdb" if shutil.which("python") and self._module_available("ipdb") else "pdb"
        # -c continue starts execution immediately; without breakpoints it runs to completion.
        cmd = ["python", "-m", debugger, "-c", "continue", str(path)]
        try:
            self._terminal_log(f"[dim]Debugger: {debugger}[/]")  # type: ignore[attr-defined]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                self._terminal_log(f"[dim]{decoded}[/]")  # type: ignore[attr-defined]
            await proc.wait()
            self._terminal_log(f"[dim]Debugger exit code: {proc.returncode}[/]")  # type: ignore[attr-defined]
        except Exception as exc:
            self._terminal_log(f"[red]Error en debugger:[/] {exc}")  # type: ignore[attr-defined]

    def _module_available(self, name: str) -> bool:
        """Return True if *name* can be imported."""
        try:
            __import__(name)
            return True
        except Exception:
            return False

    # ── Auto-save ──────────────────────────────────────────────────

    async def _auto_save_files_worker(self) -> None:
        """Periodically save modified open files if auto-save is enabled."""
        from textual.worker import get_current_worker

        worker = get_current_worker()
        while not worker.is_cancelled:
            await asyncio.sleep(30)
            if worker.is_cancelled:
                break
            if self.ide_config.auto_save:  # type: ignore[attr-defined]
                self.call_from_thread(self._auto_save_modified_files)  # type: ignore[attr-defined]

    def _auto_save_modified_files(self) -> None:
        saved: list[str] = []
        for tab_id in list(self._modified):  # type: ignore[attr-defined]
            path = self._tab_paths.get(tab_id)  # type: ignore[attr-defined]
            if not path:
                continue
            try:
                editor = self.query_one(f"#editor-{tab_id}", TextArea)  # type: ignore[attr-defined]
                backup = _backup_path(path)
                backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                path.write_text(editor.text, encoding="utf-8")
                self._modified.discard(tab_id)  # type: ignore[attr-defined]
                saved.append(path.name)
            except Exception:
                pass
        if saved:
            self.notify(f"Auto-guardado: {', '.join(saved)}", severity="information")  # type: ignore[attr-defined]
            self._update_editor_info()

    # ── Editor TextArea event handlers ─────────────────────────────

    def _cancel_completion_timer(self) -> None:
        """Cancel any pending automatic completion trigger."""
        timer = getattr(self, "_completion_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._completion_timer = None  # type: ignore[attr-defined]

    def _schedule_completion(self, path: Path, language: str, row: int, col: int) -> None:
        """Schedule an automatic completion after a short pause."""
        self._cancel_completion_timer()

        def _trigger() -> None:
            self._completion_timer = None  # type: ignore[attr-defined]
            if self.current_file != path:  # type: ignore[attr-defined]
                return
            self._completion_request_position = (row, col)  # type: ignore[attr-defined]
            self.run_worker(
                self._lsp_completion_worker(path, language, row, col),
                exclusive=False,  # type: ignore[attr-defined]
            )

        self._completion_timer = self.set_timer(0.5, _trigger)  # type: ignore[attr-defined]

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Track modifications, notify LSP servers and trigger auto-completion."""
        editor = event.text_area
        current = self._current_editor()
        if editor is not current:
            return
        tab_id = self._current_tab_id()
        if not tab_id or tab_id == "tab-welcome":
            return
        path = self._tab_paths.get(tab_id)  # type: ignore[attr-defined]
        if not path:
            return
        self._modified.add(tab_id)  # type: ignore[attr-defined]
        self._update_editor_info()
        language = _detect_language(path)
        if language:
            version = self._file_versions.get(path, 1) + 1  # type: ignore[attr-defined]
            self._file_versions[path] = version  # type: ignore[attr-defined]
            self.run_worker(
                self._lsp_did_change(path, language, editor.text, version),
                exclusive=False,  # type: ignore[attr-defined]
            )
        # Automatic completion triggers.
        if not language:
            return
        try:
            row, col = editor.cursor_location
        except Exception:
            return
        if col <= 0:
            return
        try:
            line_text = str(editor.get_line(row))
        except Exception:
            return
        if col > len(line_text):
            return
        char = line_text[col - 1]
        if char == ".":
            self._cancel_completion_timer()
            self._completion_request_position = (row, col)  # type: ignore[attr-defined]
            self.run_worker(
                self._lsp_completion_worker(path, language, row, col),
                exclusive=False,  # type: ignore[attr-defined]
            )
        elif char.isalnum() or char == "_":
            self._schedule_completion(path, language, row, col)

    def on_text_area_cursor_location_changed(self, event: TextArea.CursorLocationChanged) -> None:
        """Refresh inline blame when the cursor moves."""
        self.run_worker(self._git_info_worker(), exclusive=False)  # type: ignore[attr-defined]

    def on_tabbed_content_active_tab_changed(self, event) -> None:
        """Update current_file when switching tabs."""
        tab_id = event.tab.id
        self.current_file = self._tab_paths.get(tab_id)  # type: ignore[attr-defined]
        self._update_editor_info()

    # ── LSP didOpen / didChange (text sync) ────────────────────────

    async def _lsp_did_open(self, path: Path, language: str) -> None:
        """Send textDocument/didOpen to the language server."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            self._file_versions[path] = 1  # type: ignore[attr-defined]
            await self.lsp_manager.did_open(path, language, text)  # type: ignore[attr-defined]
        except Exception:
            pass

    async def _lsp_did_change(self, path: Path, language: str, text: str, version: int) -> None:
        """Send textDocument/didChange to the language server."""
        try:
            await self.lsp_manager.did_change(path, language, text, version)  # type: ignore[attr-defined]
        except Exception:
            pass

    # ── LSP completion / hover / definition / diagnostics ──────────

    def action_request_completion(self) -> None:
        """Ctrl+Space: request LSP completions for the current cursor position."""
        editor = self._current_editor()
        path = self.current_file  # type: ignore[attr-defined]
        if not editor or not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        language = _detect_language(path)
        if not language:
            self.notify("Lenguaje no soportado por LSP", severity="warning")  # type: ignore[attr-defined]
            return
        try:
            row, col = editor.cursor_location
        except Exception:
            row, col = 0, 0
        self._completion_request_position = (row, col)  # type: ignore[attr-defined]
        self.run_worker(
            self._lsp_completion_worker(path, language, row, col),
            exclusive=False,  # type: ignore[attr-defined]
        )

    async def _lsp_completion_worker(
        self,
        path: Path,
        language: str,
        line: int,
        character: int,
    ) -> None:
        """Fetch completions from the LSP server and show a picker modal."""
        try:
            items = await self.lsp_manager.completion(path, language, line, character)  # type: ignore[attr-defined]
            if not items:
                self._chat_system("[dim]Sin completions disponibles.[/]")  # type: ignore[attr-defined]
                return
            self.push_screen(CompletionScreen(items), self._on_completion_selected)  # type: ignore[attr-defined]
        except Exception as exc:
            self._chat_system(f"[red]Error LSP completion:[/] {exc}")  # type: ignore[attr-defined]

    def _on_completion_selected(self, text: str | None) -> None:
        """Insert the chosen completion, replacing the current word prefix."""
        if text is None:
            return
        editor = self._current_editor()
        if not editor:
            return
        try:
            position = getattr(self, "_completion_request_position", None)
            if position is None:
                editor.insert(text, editor.cursor_location)
                return
            row, col = position
            line_text = str(editor.get_line(row))
            start_col = col
            while start_col > 0:
                prev = line_text[start_col - 1]
                if prev.isalnum() or prev == "_":
                    start_col -= 1
                else:
                    break
            editor.replace(text, (row, start_col), (row, col))
            editor.cursor_location = (row, start_col + len(text))
            editor.scroll_cursor_visible()
        except Exception as exc:
            self._chat_system(f"[red]Error insertando completion:[/] {exc}")  # type: ignore[attr-defined]

    def action_show_hover(self) -> None:
        """Ctrl+Shift+I: show LSP hover for the current cursor position."""
        editor = self._current_editor()
        path = self.current_file  # type: ignore[attr-defined]
        if not editor or not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        language = _detect_language(path)
        if not language:
            self.notify("Lenguaje no soportado por LSP", severity="warning")  # type: ignore[attr-defined]
            return
        try:
            row, col = editor.cursor_location
        except Exception:
            row, col = 0, 0
        self.run_worker(
            self._lsp_hover_worker(path, language, row, col),
            exclusive=False,  # type: ignore[attr-defined]
        )

    async def _lsp_hover_worker(
        self,
        path: Path,
        language: str,
        line: int,
        character: int,
    ) -> None:
        """Fetch hover info from the LSP server and show it in a modal."""
        try:
            text = await self.lsp_manager.hover(path, language, line, character)  # type: ignore[attr-defined]
            if not text:
                self._chat_system("[dim]Sin información de hover.[/]")  # type: ignore[attr-defined]
                return
            self.push_screen(HoverScreen(text, title=f"Hover — {path.name}"))  # type: ignore[attr-defined]
        except Exception as exc:
            self._chat_system(f"[red]Error LSP hover:[/] {exc}")  # type: ignore[attr-defined]

    def action_go_to_definition(self) -> None:
        """F12: jump to the definition of the symbol under the cursor."""
        editor = self._current_editor()
        path = self.current_file  # type: ignore[attr-defined]
        if not editor or not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        language = _detect_language(path)
        if not language:
            self.notify("Lenguaje no soportado por LSP", severity="warning")  # type: ignore[attr-defined]
            return
        try:
            row, col = editor.cursor_location
        except Exception:
            row, col = 0, 0
        self.run_worker(
            self._lsp_definition_worker(path, language, row, col),
            exclusive=False,  # type: ignore[attr-defined]
        )

    async def _lsp_definition_worker(
        self,
        path: Path,
        language: str,
        line: int,
        character: int,
    ) -> None:
        """Fetch definition locations from the LSP server and jump to the first one."""
        try:
            locations = await self.lsp_manager.definition(path, language, line, character)  # type: ignore[attr-defined]
            if not locations:
                self._chat_system("[dim]Sin definición encontrada.[/]")  # type: ignore[attr-defined]
                return
            loc = locations[0]
            uri = loc.get("uri", "")
            target_path = _uri_to_path(uri) if uri else path
            start = loc.get("range", {}).get("start", {})
            target_line = start.get("line", 0) + 1
            target_col = start.get("character", 0)
            self._open_file(target_path, line=target_line)

            def _set_cursor() -> None:
                editor = self._current_editor()
                if editor:
                    editor.cursor_location = (max(1, target_line) - 1, target_col)
                    editor.scroll_cursor_visible()

            self.call_after_refresh(_set_cursor)  # type: ignore[attr-defined]
        except Exception as exc:
            self._chat_system(f"[red]Error LSP definition:[/] {exc}")  # type: ignore[attr-defined]

    def action_show_diagnostics(self) -> None:
        """Show LSP diagnostics for the current file."""
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self.notify("No hay archivo activo", severity="warning")  # type: ignore[attr-defined]
            return
        diagnostics = self.lsp_manager.diagnostics_for(path)  # type: ignore[attr-defined]
        if not diagnostics:
            self._chat_system("[dim]Sin diagnostics para este archivo.[/]")  # type: ignore[attr-defined]
            return
        self.push_screen(DiagnosticsScreen(diagnostics, path), self._on_diagnostic_selected)  # type: ignore[attr-defined]

    def _on_diagnostic_selected(self, result: tuple[str, int] | None) -> None:
        """Jump to the line of a selected diagnostic."""
        if not result:
            return
        target_path = Path(result[0])
        self._open_file(target_path, line=result[1])

    # ── Recent files navigation ───────────────────────────────────

    def action_recent_files(self) -> None:
        def _on_select(path: Path | None) -> None:
            if path:
                self._open_file(path)
        if not self._recent_files:  # type: ignore[attr-defined]
            self.notify("No hay archivos recientes", severity="information")  # type: ignore[attr-defined]
            return
        # Imported lazily to keep this module free of file-tree modal import cycle.
        from ..screens.modals import RecentFilesScreen

        self.push_screen(RecentFilesScreen(self._recent_files, self.root), _on_select)  # type: ignore[attr-defined]

    # ── File search modal ──────────────────────────────────────────

    def action_open_file_search(self) -> None:
        """Open the FileSearch modal and route the selection to _open_file."""
        def _on_select(path: Path | None) -> None:
            if path:
                self._open_file(path)
        self.push_screen(FileSearchScreen(self.root), _on_select)  # type: ignore[attr-defined]  # noqa: F821  (FileSearchScreen reexported by lazy fallback — see below)
