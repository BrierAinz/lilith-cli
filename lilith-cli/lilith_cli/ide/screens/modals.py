"""Modal screens used by the Lilith IDE."""

from __future__ import annotations

import asyncio
import difflib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    TextArea,
)

from ..config import IDEConfig
from ..runestones import Runestone
from ..utils.helpers import GrepResult, ProposedChange, _shorten_path

if TYPE_CHECKING:
    pass


class FileSearchScreen(ModalScreen[Path | None]):
    """Ctrl+P style file picker."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
        Binding("ctrl+c", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, root: Path, *, excluded: set[str] | None = None) -> None:
        super().__init__()
        self.root = root
        self.excluded = excluded or {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "node_modules",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "dist",
            "build",
            ".egg-info",
        }
        self._all_files: list[Path] = []
        self._filtered: list[Path] = []

    def compose(self) -> Any:
        with Vertical(id="file-search-dialog", classes="modal-dialog"):
            yield Input(placeholder="Buscar archivo…", id="file-search-input", classes="modal-input")
            yield ListView(id="file-search-results", classes="modal-results")

    def on_mount(self) -> None:
        self._all_files = self._collect_files()
        self._update_results("")
        self.query_one("#file-search-input", Input).focus()

    def _collect_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in self.excluded for part in path.parts):
                continue
            try:
                path.relative_to(self.root)
            except ValueError:
                continue
            files.append(path)
        return sorted(files)

    def _update_results(self, query: str) -> None:
        query_lower = query.lower()
        if query_lower:
            self._filtered = [f for f in self._all_files if query_lower in f.name.lower()]
        else:
            self._filtered = self._all_files[:250]

        if not self.is_mounted:
            return

        list_view = self.query_one("#file-search-results", ListView)
        list_view.clear()
        for path in self._filtered:
            rel = _shorten_path(path, self.root)
            list_view.append(ListItem(Label(rel, classes="file-search-item")))
        if list_view.children:
            list_view.index = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "file-search-input":
            self._update_results(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "file-search-input":
            self._select_current()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._select_index(event.list_view.index)

    def _select_current(self) -> None:
        list_view = self.query_one("#file-search-results", ListView)
        self._select_index(list_view.index)

    def _select_index(self, index: int | None) -> None:
        if index is None or index < 0 or index >= len(self._filtered):
            self.dismiss(None)
            return
        self.dismiss(self._filtered[index])


class GrepScreen(ModalScreen[GrepResult | None]):
    """Ctrl+Shift+F project text search."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self._results: list[GrepResult] = []

    def compose(self) -> Any:
        with Vertical(id="grep-dialog", classes="modal-dialog"):
            yield Input(placeholder="Buscar en archivos…", id="grep-input", classes="modal-input")
            yield ListView(id="grep-results", classes="modal-results")

    def on_mount(self) -> None:
        self.query_one("#grep-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "grep-input":
            await self._run_search(event.value)

    async def _run_search(self, query: str) -> None:
        self._results = []
        if not query:
            self._update_list()
            return

        cmd = self._build_command(query)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace")
            self._results = self._parse_output(text)
        except Exception as exc:
            self._results = [GrepResult(self.root, 0, f"Error: {exc}")]
        self._update_list()

    def _build_command(self, query: str) -> list[str]:
        if shutil.which("rg"):
            return ["rg", "--line-number", "--no-heading", "--color", "never", query]
        if shutil.which("grep"):
            return ["grep", "-RIn", query, "."]
        # Windows fallback.
        return ["findstr", "/S", "/N", query, "*"]

    def _parse_output(self, text: str) -> list[GrepResult]:
        results: list[GrepResult] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            # Handle ripgrep/grep -n output: path:line:text
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            try:
                path = self.root / parts[0]
                ln = int(parts[1])
                txt = parts[2]
            except (ValueError, IndexError):
                continue
            results.append(GrepResult(path, ln, txt))
        return results[:500]

    def _update_list(self) -> None:
        if not self.is_mounted:
            return
        list_view = self.query_one("#grep-results", ListView)
        list_view.clear()
        for res in self._results:
            rel = _shorten_path(res.path, self.root)
            label = f"{rel}:{res.line}: {res.text[:80]}"
            list_view.append(ListItem(Label(label, classes="grep-result-item")))
        if list_view.children:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self._results):
            self.dismiss(None)
            return
        self.dismiss(self._results[index])


class HistoryScreen(ModalScreen[Path | None]):
    """Load a previous conversation."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, conversations_dir: Path) -> None:
        super().__init__()
        self.conversations_dir = conversations_dir
        self._files: list[Path] = []

    def compose(self) -> Any:
        with Vertical(id="history-dialog", classes="modal-dialog"):
            yield Static("Historial de conversaciones", classes="panel-title")
            yield ListView(id="history-results", classes="modal-results")

    def on_mount(self) -> None:
        self._files = sorted(
            (self.conversations_dir.glob("conv_*.json") if self.conversations_dir.exists() else []),
            reverse=True,
        )
        list_view = self.query_one("#history-results", ListView)
        for fpath in self._files:
            list_view.append(ListItem(Label(fpath.stem, classes="history-item")))
        if list_view.children:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self._files):
            self.dismiss(None)
            return
        self.dismiss(self._files[index])


class RecentFilesScreen(ModalScreen[Path | None]):
    """Ctrl+E: reopen a recently opened file."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, files: list[Path], root: Path) -> None:
        super().__init__()
        self._files = files
        self.root = root

    def compose(self) -> Any:
        with Vertical(id="recent-dialog", classes="modal-dialog"):
            yield Static("Archivos recientes", classes="panel-title")
            yield ListView(id="recent-results", classes="modal-results")

    def on_mount(self) -> None:
        list_view = self.query_one("#recent-results", ListView)
        for fpath in self._files:
            rel = _shorten_path(fpath, self.root)
            list_view.append(ListItem(Label(rel, classes="history-item")))
        if list_view.children:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self._files):
            self.dismiss(None)
            return
        self.dismiss(self._files[index])


class PatchScreen(ModalScreen[str | None]):
    """Review and apply a unified diff."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, diff_text: str) -> None:
        super().__init__()
        self.diff_text = diff_text

    def compose(self) -> Any:
        with Vertical(id="patch-dialog", classes="modal-dialog"):
            yield Static("Revisar parche", classes="panel-title")
            yield TextArea(text=self.diff_text, id="patch-editor", classes="modal-textarea")
            with Horizontal(classes="modal-buttons"):
                yield Button("Aplicar", id="patch-apply", variant="success")
                yield Button("Cancelar", id="patch-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "patch-apply":
            editor = self.query_one("#patch-editor", TextArea)
            self.dismiss(editor.text)
        elif event.button.id == "patch-cancel":
            self.dismiss(None)


class AgentDiffScreen(ModalScreen[list[ProposedChange]]):
    """Review agent-proposed file changes side-by-side before applying them."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, changes: list[ProposedChange], root: Path) -> None:
        super().__init__()
        self.changes = changes
        self.root = root
        self._accepted = [False] * len(changes)
        self._labels: list[Label] = []

    def compose(self) -> Any:
        self._labels = [Label(self._label_for(i)) for i in range(len(self.changes))]
        with Vertical(id="agent-diff-dialog", classes="modal-dialog"):
            yield Static("Revisar cambios del agente", classes="panel-title")
            yield ListView(
                *[ListItem(label) for label in self._labels],
                id="agent-diff-files",
            )
            with Horizontal(classes="modal-results"):
                yield TextArea(id="agent-diff-left", read_only=True)
                yield TextArea(id="agent-diff-right", read_only=True)
            with Horizontal(classes="modal-buttons"):
                yield Button("Aceptar archivo", id="agent-diff-accept", variant="success")
                yield Button("Rechazar archivo", id="agent-diff-reject", variant="error")
                yield Button("Aceptar todos", id="agent-diff-accept-all", variant="success")
                yield Button("Rechazar todos", id="agent-diff-reject-all", variant="warning")
                yield Button("Cerrar", id="agent-diff-close", variant="primary")

    def on_mount(self) -> None:
        list_view = self.query_one("#agent-diff-files", ListView)
        if list_view.children:
            list_view.index = 0
        self._update_view()

    def _label_for(self, index: int) -> str:
        change = self.changes[index]
        mark = "[green]✓[/]" if self._accepted[index] else "[red]✗[/]"
        return f"{mark} {change.rel_path}"

    def _current_index(self) -> int:
        list_view = self.query_one("#agent-diff-files", ListView)
        return list_view.index if list_view.index is not None else 0

    def _update_view(self) -> None:
        index = self._current_index()
        if index < 0 or index >= len(self.changes):
            return
        change = self.changes[index]
        left = self.query_one("#agent-diff-left", TextArea)
        right = self.query_one("#agent-diff-right", TextArea)
        left.text = change.current
        right.text = change.proposed
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        for i, label in enumerate(self._labels):
            label.update(self._label_for(i))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._update_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "agent-diff-accept":
            self._accepted[self._current_index()] = True
            self._update_view()
        elif button_id == "agent-diff-reject":
            self._accepted[self._current_index()] = False
            self._update_view()
        elif button_id == "agent-diff-accept-all":
            self._accepted = [True] * len(self.changes)
            self._update_view()
        elif button_id == "agent-diff-reject-all":
            self._dismiss_accepted([])
        elif button_id == "agent-diff-close":
            accepted = [c for c, a in zip(self.changes, self._accepted) if a]
            self._dismiss_accepted(accepted)

    def _dismiss_accepted(self, accepted: list[ProposedChange]) -> None:
        self.dismiss(accepted)


class GitScreen(ModalScreen[None]):
    """Show git status and diff for the current file."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cerrar"),
    ]

    def __init__(self, root: Path, current_file: Path | None) -> None:
        super().__init__()
        self.root = root
        self.current_file = current_file

    def compose(self) -> Any:
        with Vertical(id="git-dialog", classes="modal-dialog"):
            yield Static("Git — Runas del tiempo", classes="panel-title")
            yield RichLog(id="git-log", classes="modal-results")
            with Horizontal(classes="modal-buttons"):
                yield Button("Hunks", id="git-hunks-button", variant="primary")
                yield Button("Log", id="git-log-button", variant="default")
                yield Button("Cerrar", id="git-close-button", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "git-hunks-button":
            self.app.action_show_git_hunks()
        elif event.button.id == "git-log-button":
            self.app.action_show_git_log()
        elif event.button.id == "git-close-button":
            self.dismiss()

    async def on_mount(self) -> None:
        log = self.query_one("#git-log", RichLog)
        try:
            # Branch.
            branch_proc = await asyncio.create_subprocess_exec(
                "git", "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            branch_out, _ = await branch_proc.communicate()
            branch = branch_out.decode("utf-8", errors="replace").strip()
            log.write(f"[bold]Rama:[/bold] {branch or 'unknown'}")

            # Recent log.
            log_proc = await asyncio.create_subprocess_exec(
                "git", "log", "--oneline", "-5",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            log_out, _ = await log_proc.communicate()
            log_text = log_out.decode("utf-8", errors="replace").strip()
            if log_text:
                log.write("\n[bold]Últimos commits:[/bold]\n" + log_text)

            # Status.
            status_proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, stderr = await status_proc.communicate()
            if stderr:
                log.write(f"[red]{stderr.decode('utf-8', errors='replace')}[/]")
            status_text = stdout.decode("utf-8", errors="replace").strip()
            if status_text:
                log.write("\n[bold]Status:[/bold]\n" + status_text)
            else:
                log.write("\n[dim]Working tree clean.[/dim]")

            if self.current_file:
                diff_proc = await asyncio.create_subprocess_exec(
                    "git", "diff", "--", str(self.current_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.root,
                )
                diff_out, _ = await diff_proc.communicate()
                diff_text = diff_out.decode("utf-8", errors="replace").strip()
                if diff_text:
                    log.write("\n[bold]Diff del archivo actual:[/bold]\n" + diff_text)
        except Exception as exc:
            log.write(f"[red]Error Git: {exc}[/]")


class GitHunkScreen(ModalScreen[tuple[str, str] | None]):
    """Stage or discard individual diff hunks for a file."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, root: Path, current_file: Path) -> None:
        super().__init__()
        self.root = root
        self.current_file = current_file
        self._hunks: list[tuple[str, str]] = []

    def compose(self) -> Any:
        with Vertical(id="git-hunk-dialog", classes="modal-dialog"):
            yield Static("Git Hunks — Elegir cambios", classes="panel-title")
            yield ListView(id="git-hunk-list", classes="modal-results")
            with Horizontal(classes="modal-buttons"):
                yield Button("Stage hunk", id="git-hunk-stage", variant="success")
                yield Button("Discard hunk", id="git-hunk-discard", variant="error")
                yield Button("Cerrar", id="git-hunk-close", variant="default")

    async def on_mount(self) -> None:
        self._hunks = await self._load_hunks()
        list_view = self.query_one("#git-hunk-list", ListView)
        list_view.clear()
        for display, _ in self._hunks:
            list_view.append(ListItem(Label(display, classes="file-search-item")))
        if list_view.children:
            list_view.index = 0

    async def _load_hunks(self) -> list[tuple[str, str]]:
        rel = _shorten_path(self.current_file, self.root)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--", rel,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, _ = await proc.communicate()
            diff_text = stdout.decode("utf-8", errors="replace")
        except Exception as exc:
            return [(f"[red]Error: {exc}[/]", "")]
        return self._parse_hunks(diff_text, rel)

    @staticmethod
    def _parse_hunks(diff_text: str, rel_path: str) -> list[tuple[str, str]]:
        lines = diff_text.splitlines()
        hunks: list[tuple[str, str]] = []
        header_lines: list[str] = []
        i = 0
        while i < len(lines) and not lines[i].startswith("@@"):
            header_lines.append(lines[i])
            i += 1
        header_block = "\n".join(header_lines)
        while i < len(lines):
            line = lines[i]
            if line.startswith("@@"):
                hunk_body: list[str] = [line]
                i += 1
                while i < len(lines) and not lines[i].startswith("@@"):
                    hunk_body.append(lines[i])
                    i += 1
                body_text = "\n".join(hunk_body)
                display = f"{rel_path}: {line}"
                patch = header_block + "\n" + body_text + "\n" if header_block else body_text + "\n"
                hunks.append((display, patch))
            else:
                i += 1
        return hunks

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "git-hunk-close":
            self.dismiss(None)
            return
        list_view = self.query_one("#git-hunk-list", ListView)
        index = list_view.index
        if index is None or index < 0 or index >= len(self._hunks):
            return
        action = "stage" if event.button.id == "git-hunk-stage" else "discard"
        self.dismiss((action, self._hunks[index][1]))


class CommitScreen(ModalScreen[str | None]):
    """Commit staged changes with a message and diff summary."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, root: Path, message: str = "") -> None:
        super().__init__()
        self.root = root
        self._message = message

    def compose(self) -> Any:
        with Vertical(id="commit-dialog", classes="modal-dialog"):
            yield Static("Commit — Forjar un nuevo commit", classes="panel-title")
            yield RichLog(id="commit-diff", classes="modal-results")
            yield Input(
                value=self._message,
                placeholder="Mensaje del commit…",
                id="commit-message",
                classes="modal-input",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Commit", id="commit-do", variant="success")
                yield Button("Cancelar", id="commit-cancel", variant="error")

    async def on_mount(self) -> None:
        log = self.query_one("#commit-diff", RichLog)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--cached", "--stat",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace").strip()
            log.write("[bold]Staged diff summary:[/]\n" + (text or "[sin cambios staged]"))
        except Exception as exc:
            log.write(f"[red]Error diff staged: {exc}[/]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "commit-do":
            self.dismiss(self.query_one("#commit-message", Input).value.strip())
        elif event.button.id == "commit-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "commit-message":
            self.dismiss(event.value.strip())


class GitLogScreen(ModalScreen[str | None]):
    """Show the recent git log and let the user pick a commit."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self._commits: list[tuple[str, str]] = []

    def compose(self) -> Any:
        with Vertical(id="git-log-dialog", classes="modal-dialog"):
            yield Static("Git Log — Runas del pasado", classes="panel-title")
            yield ListView(id="git-log-list", classes="modal-results")

    async def on_mount(self) -> None:
        list_view = self.query_one("#git-log-list", ListView)
        list_view.clear()
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "log", "--oneline", "-20",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            list_view.append(ListItem(Label(f"[red]Error: {exc}[/]", classes="file-search-item")))
            return
        self._commits = []
        for line in text.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                hash_, msg = parts
            elif len(parts) == 1:
                hash_, msg = parts[0], ""
            else:
                continue
            self._commits.append((hash_, msg))
            list_view.append(ListItem(Label(f"[dim]{hash_}[/] {msg}", classes="file-search-item")))
        if list_view.children:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self._commits):
            self.dismiss(None)
            return
        self.dismiss(self._commits[index][0])


class GitCommitScreen(ModalScreen[tuple[str, str] | None]):
    """Inspect a single commit and optionally checkout."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, root: Path, commit_hash: str) -> None:
        super().__init__()
        self.root = root
        self.commit_hash = commit_hash

    def compose(self) -> Any:
        with Vertical(id="git-commit-dialog", classes="modal-dialog"):
            yield Static(f"Commit {self.commit_hash[:8]}", classes="panel-title")
            yield RichLog(id="git-commit-diff", classes="modal-results")
            with Horizontal(classes="modal-buttons"):
                yield Button("Checkout", id="git-commit-checkout", variant="warning")
                yield Button("Cerrar", id="git-commit-close", variant="default")

    async def on_mount(self) -> None:
        log = self.query_one("#git-commit-diff", RichLog)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "show", "--no-color", "--patch-with-stat", self.commit_hash,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, stderr = await proc.communicate()
            if stderr:
                log.write(f"[red]{stderr.decode('utf-8', errors='replace')}[/]")
            text = stdout.decode("utf-8", errors="replace").strip()
            log.write(text or "[dim]Sin diff disponible.[/]")
        except Exception as exc:
            log.write(f"[red]Error: {exc}[/]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "git-commit-checkout":
            self.dismiss(("checkout", self.commit_hash))
        elif event.button.id == "git-commit-close":
            self.dismiss(None)


class ToastHistoryScreen(ModalScreen[None]):
    """Ctrl+Shift+N: show recent toast notifications."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cerrar"),
    ]

    def __init__(self, history: list[dict[str, str]]) -> None:
        super().__init__()
        self._history = history

    def compose(self) -> Any:
        with Vertical(id="toast-dialog", classes="modal-dialog"):
            yield Static("Notificaciones", classes="panel-title")
            yield RichLog(id="toast-log", classes="modal-results")

    def on_mount(self) -> None:
        log = self.query_one("#toast-log", RichLog)
        if not self._history:
            log.write("[dim]Sin notificaciones.[/dim]")
            return
        for item in reversed(self._history[-100:]):
            color = {
                "information": "cyan",
                "warning": "yellow",
                "error": "red",
            }.get(item.get("severity"), "white")
            log.write(f"[{color}]{item.get('message', '')}[/]")


class FindScreen(ModalScreen[str | None]):
    """Ctrl+F search inside the current editor."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def compose(self) -> Any:
        with Vertical(id="find-dialog", classes="modal-dialog"):
            yield Static("Buscar en archivo", classes="panel-title")
            yield Input(placeholder="Texto…", id="find-input", classes="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Siguiente", id="find-next", variant="primary")
                yield Button("Anterior", id="find-prev", variant="default")
                yield Button("Cerrar", id="find-close", variant="error")

    def on_mount(self) -> None:
        self.query_one("#find-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("find-next", "find-prev"):
            query = self.query_one("#find-input", Input).value
            self.dismiss(query)
        elif event.button.id == "find-close":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "find-input":
            self.dismiss(event.value)


class FindReplaceScreen(ModalScreen[tuple[str, str, str] | None]):
    """Ctrl+H find and replace inside the active editor."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def compose(self) -> Any:
        with Vertical(id="findreplace-dialog", classes="modal-dialog"):
            yield Static("Buscar y reemplazar", classes="panel-title")
            yield Input(placeholder="Buscar…", id="findreplace-find", classes="modal-input")
            yield Input(placeholder="Reemplazar por…", id="findreplace-replace", classes="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Reemplazar", id="findreplace-replace-one", variant="primary")
                yield Button("Reemplazar todo", id="findreplace-replace-all", variant="warning")
                yield Button("Cerrar", id="findreplace-close", variant="error")

    def on_mount(self) -> None:
        self.query_one("#findreplace-find", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "findreplace-close":
            self.dismiss(None)
        elif event.button.id in ("findreplace-replace-one", "findreplace-replace-all"):
            find_text = self.query_one("#findreplace-find", Input).value
            replace_text = self.query_one("#findreplace-replace", Input).value
            action = "one" if event.button.id == "findreplace-replace-one" else "all"
            self.dismiss((find_text, replace_text, action))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "findreplace-replace":
            find_text = self.query_one("#findreplace-find", Input).value
            replace_text = event.value
            self.dismiss((find_text, replace_text, "one"))


class ProjectFindReplaceScreen(ModalScreen[tuple[str, str] | None]):
    """Ctrl+Shift+H find and replace across project files."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def compose(self) -> Any:
        with Vertical(id="project-findreplace-dialog", classes="modal-dialog"):
            yield Static("Buscar y reemplazar en todo el proyecto", classes="panel-title")
            yield Input(placeholder="Buscar…", id="project-findreplace-find", classes="modal-input")
            yield Input(placeholder="Reemplazar por…", id="project-findreplace-replace", classes="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Reemplazar todo", id="project-findreplace-all", variant="warning")
                yield Button("Cerrar", id="project-findreplace-close", variant="error")

    def on_mount(self) -> None:
        self.query_one("#project-findreplace-find", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-findreplace-close":
            self.dismiss(None)
        elif event.button.id == "project-findreplace-all":
            find_text = self.query_one("#project-findreplace-find", Input).value
            replace_text = self.query_one("#project-findreplace-replace", Input).value
            self.dismiss((find_text, replace_text))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "project-findreplace-replace":
            find_text = self.query_one("#project-findreplace-find", Input).value
            replace_text = event.value
            self.dismiss((find_text, replace_text))


class GoToLineScreen(ModalScreen[int | None]):
    """Ctrl+G jump to a specific line number."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def compose(self) -> Any:
        with Vertical(id="goto-dialog", classes="modal-dialog"):
            yield Static("Ir a línea", classes="panel-title")
            yield Input(placeholder="Número de línea…", id="goto-input", classes="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Ir", id="goto-go", variant="primary")
                yield Button("Cancelar", id="goto-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#goto-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "goto-go":
            self._submit()
        elif event.button.id == "goto-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "goto-input":
            self._submit()

    def _submit(self) -> None:
        text = self.query_one("#goto-input", Input).value.strip()
        try:
            line = int(text)
            self.dismiss(max(1, line))
        except ValueError:
            self.dismiss(None)


class GitBlameScreen(ModalScreen[None]):
    """Show git blame for the current file."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cerrar"),
    ]

    def __init__(self, root: Path, current_file: Path | None) -> None:
        super().__init__()
        self.root = root
        self.current_file = current_file

    def compose(self) -> Any:
        with Vertical(id="blame-dialog", classes="modal-dialog"):
            yield Static("Git Blame — Runas del autor", classes="panel-title")
            yield RichLog(id="blame-log", classes="modal-results")

    async def on_mount(self) -> None:
        log = self.query_one("#blame-log", RichLog)
        if not self.current_file:
            log.write("[red]No hay archivo abierto[/]")
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "blame", "--date=short", "-L", "1,50", str(self.current_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, stderr = await proc.communicate()
            if stderr:
                log.write(f"[red]{stderr.decode('utf-8', errors='replace')}[/]")
            out = stdout.decode("utf-8", errors="replace").strip()
            if out:
                log.write(out)
            else:
                log.write("[dim]Sin blame disponible.[/dim]")
        except Exception as exc:
            log.write(f"[red]Error Git blame: {exc}[/]")


class OutlineScreen(ModalScreen[tuple[int, str] | None]):
    """Ctrl+Shift+O: show a simple symbol outline for Python files."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, path: Path | None) -> None:
        super().__init__()
        self.path = path
        self._symbols: list[tuple[int, str]] = []

    def compose(self) -> Any:
        with Vertical(id="outline-dialog", classes="modal-dialog"):
            yield Static("Outline — Símbolos", classes="panel-title")
            yield ListView(id="outline-results", classes="modal-results")

    def on_mount(self) -> None:
        self._symbols = self._parse_symbols()
        list_view = self.query_one("#outline-results", ListView)
        for line_no, name in self._symbols:
            list_view.append(ListItem(Label(f"{line_no}: {name}", classes="file-search-item")))
        if list_view.children:
            list_view.index = 0

    def _parse_symbols(self) -> list[tuple[int, str]]:
        if not self.path or not self.path.exists():
            return []
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        symbols: list[tuple[int, str]] = []
        # Simple regex-based outline for Python-like files.
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "async def ")):
                raw_name = stripped.split("(")[0].split()[-1]
                name = raw_name.rstrip(":")
                kind = "class" if stripped.startswith("class ") else "def"
                symbols.append((i, f"{kind} {name}"))
        return symbols

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self._symbols):
            self.dismiss(None)
            return
        self.dismiss(self._symbols[index])


class ConfigScreen(ModalScreen[IDEConfig | None]):
    """Ctrl+, settings editor for the IDE."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, config: IDEConfig) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> Any:
        with Vertical(id="config-dialog", classes="modal-dialog"):
            yield Static("Configuración del IDE", classes="panel-title")
            yield Input(value=str(self._config.terminal_height), placeholder="Altura terminal", id="config-terminal-height", classes="modal-input")
            yield Input(value=str(self._config.auto_reload_interval), placeholder="Intervalo auto-reload (s)", id="config-reload-interval", classes="modal-input")
            yield Select(
                [("Auto-guardar: sí", True), ("Auto-guardar: no", False)],
                value=self._config.auto_save,
                id="config-auto-save",
                classes="modal-input",
            )
            yield Input(value=self._config.run_on_save, placeholder="Comando post-guardado", id="config-run-on-save", classes="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Guardar", id="config-save", variant="success")
                yield Button("Cancelar", id="config-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config-save":
            try:
                self._config.terminal_height = int(self.query_one("#config-terminal-height", Input).value or 8)
                self._config.auto_reload_interval = float(self.query_one("#config-reload-interval", Input).value or 2.0)
                self._config.auto_save = bool(self.query_one("#config-auto-save", Select).value)
                self._config.run_on_save = self.query_one("#config-run-on-save", Input).value.strip()
            except ValueError:
                pass
            self.dismiss(self._config)
        elif event.button.id == "config-cancel":
            self.dismiss(None)


class DiffScreen(ModalScreen[None]):
    """Show a side-by-side diff of the current file against git HEAD."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cerrar"),
    ]

    def __init__(self, root: Path, current_file: Path | None) -> None:
        super().__init__()
        self.root = root
        self.current_file = current_file

    def compose(self) -> Any:
        with Vertical(id="diff-dialog", classes="modal-dialog"):
            yield Static("Diff — Runas comparadas", classes="panel-title")
            with Horizontal(classes="modal-results"):
                yield TextArea(id="diff-left", read_only=True)
                yield TextArea(id="diff-right", read_only=True)

    async def on_mount(self) -> None:
        left = self.query_one("#diff-left", TextArea)
        right = self.query_one("#diff-right", TextArea)
        if not self.current_file or not self.current_file.exists():
            left.text = "No hay archivo activo"
            return
        try:
            current = self.current_file.read_text(encoding="utf-8", errors="replace")
            proc = await asyncio.create_subprocess_exec(
                "git", "show", f"HEAD:{_shorten_path(self.current_file, self.root)}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,
            )
            stdout, _ = await proc.communicate()
            head = stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else ""
            left.text = head or "(archivo no existe en HEAD)"
            right.text = current
        except Exception as exc:
            left.text = f"Error: {exc}"


class RunestoneScreen(ModalScreen[tuple[str, str] | None]):
    """Preview a Runestone artifact with apply/save/evolve actions."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, runestone: Runestone) -> None:
        super().__init__()
        self.runestone = runestone

    def compose(self) -> Any:
        with Vertical(id="runestone-dialog", classes="modal-dialog"):
            yield Static(f"Runestone — {self.runestone.title}", classes="panel-title")
            yield TextArea(
                text=self.runestone.content,
                read_only=False,
                id="runestone-editor",
                classes="modal-textarea",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Aplicar", id="runestone-apply", variant="success")
                yield Button("Guardar como…", id="runestone-save", variant="primary")
                yield Button("Evolucionar", id="runestone-evolve", variant="warning")
                yield Button("Cerrar", id="runestone-close", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "runestone-close":
            self.dismiss(None)
            return
        if event.button.id == "runestone-apply":
            self.dismiss(("apply", self.runestone.id))
        elif event.button.id == "runestone-save":
            self.dismiss(("save", self.runestone.id))
        elif event.button.id == "runestone-evolve":
            self.dismiss(("evolve", self.runestone.id))


class CompletionScreen(ModalScreen[str | None]):
    """LSP completion picker — shows items and returns the chosen insertText."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__()
        self._items = items
        self._filtered = items

    def compose(self) -> Any:
        with Vertical(id="completion-dialog", classes="modal-dialog"):
            yield Static("Completions", classes="panel-title")
            yield Input(placeholder="Filtrar…", id="completion-input", classes="modal-input")
            yield ListView(id="completion-results", classes="modal-results")

    def on_mount(self) -> None:
        self._update_list("")
        self.query_one("#completion-input", Input).focus()

    def _update_list(self, query: str) -> None:
        query_lower = query.lower()
        self._filtered = [
            item
            for item in self._items
            if query_lower in item.get("label", "").lower()
            or query_lower in item.get("detail", "").lower()
        ]
        if not self.is_mounted:
            return
        list_view = self.query_one("#completion-results", ListView)
        list_view.clear()
        for item in self._filtered:
            label = item.get("label", "?")
            detail = item.get("detail", "")
            display = f"{label}  [dim]{detail}[/]" if detail else label
            list_view.append(ListItem(Label(display, classes="file-search-item")))
        if list_view.children:
            list_view.index = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "completion-input":
            self._update_list(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "completion-input":
            self._select_current()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._select_index(event.list_view.index)

    def _select_current(self) -> None:
        list_view = self.query_one("#completion-results", ListView)
        self._select_index(list_view.index)

    def _select_index(self, index: int | None) -> None:
        if index is None or index < 0 or index >= len(self._filtered):
            self.dismiss(None)
            return
        item = self._filtered[index]
        self.dismiss(item.get("insertText") or item.get("label", ""))


class HoverScreen(ModalScreen[None]):
    """Show LSP hover information for the current cursor position."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cerrar"),
    ]

    def __init__(self, text: str, title: str = "Hover") -> None:
        super().__init__()
        self._text = text
        self._title = title

    def compose(self) -> Any:
        with Vertical(id="hover-dialog", classes="modal-dialog"):
            yield Static(self._title, classes="panel-title")
            yield TextArea(text=self._text, read_only=True, id="hover-text", classes="modal-textarea")

    def on_mount(self) -> None:
        self.query_one("#hover-text", TextArea).focus()


class DiagnosticsScreen(ModalScreen[tuple[str, int] | None]):
    """Show LSP diagnostics for a file and allow jumping to a line."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cerrar"),
    ]

    def __init__(self, diagnostics: list[dict[str, Any]], path: Path) -> None:
        super().__init__()
        self._diagnostics = diagnostics
        self._path = path

    def compose(self) -> Any:
        with Vertical(id="diagnostics-dialog", classes="modal-dialog"):
            yield Static(f"Diagnostics — {self._path.name}", classes="panel-title")
            yield ListView(id="diagnostics-results", classes="modal-results")

    def on_mount(self) -> None:
        list_view = self.query_one("#diagnostics-results", ListView)
        for diag in self._diagnostics:
            start = diag.get("range", {}).get("start", {})
            line = start.get("line", 0) + 1
            col = start.get("character", 0) + 1
            severity = diag.get("severity", 1)
            message = diag.get("message", "").replace("\n", " ")
            severity_name = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}.get(severity, "?")
            label = f"{severity_name} L{line}:C{col} — {message[:80]}"
            list_view.append(ListItem(Label(label, classes="file-search-item")))
        if list_view.children:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self._diagnostics):
            self.dismiss(None)
            return
        diag = self._diagnostics[index]
        start = diag.get("range", {}).get("start", {})
        line = start.get("line", 0) + 1
        self.dismiss((str(self._path), line))



class YggdrasilPanelScreen(ModalScreen[None]):
    """Ctrl+Y orchestration dashboard for the Yggdrasil operator console."""

    BINDINGS = [
        Binding("escape", "dismiss_panel", "Cerrar"),
        Binding("r", "refresh", "Refrescar"),
        Binding("f5", "refresh", "Refrescar"),
        Binding("d", "dequeue", "Cancelar tarea"),
        Binding("k", "kill_spawn", "Kill spawn"),
        Binding("f1", "delegate_1", "Preset 1"),
        Binding("f2", "delegate_2", "Preset 2"),
        Binding("f3", "delegate_3", "Preset 3"),
        Binding("f4", "delegate_4", "Preset 4"),
        Binding("f6", "delegate_5", "Preset 5"),
        Binding("f7", "delegate_6", "Preset 6"),
        Binding("f8", "delegate_7", "Preset 7"),
        Binding("f9", "delegate_8", "Preset 8"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._presets: dict[str, Any] = {}
        self._queue_items: list[dict[str, Any]] = []
        self._spawn_items: list[dict[str, Any]] = []

    def compose(self) -> Any:
        with Vertical(id="yggdrasil-dialog", classes="modal-dialog"):
            yield Static("Yggdrasil — Panel de orquestación", classes="panel-title")
            with Horizontal(id="yggdrasil-columns"):
                with Vertical(classes="ygg-column"):
                    yield Static("Cola actual", classes="panel-title")
                    yield ListView(id="ygg-queue-list", classes="modal-results")
                with Vertical(classes="ygg-column"):
                    yield Static("Subagentes activos", classes="panel-title")
                    yield ListView(id="ygg-spawns-list", classes="modal-results")
                with Vertical(classes="ygg-column"):
                    yield Static("Presets disponibles", classes="panel-title")
                    yield Static("", id="ygg-presets", classes="modal-results")
            yield Static(
                "F1-F4/F6-F9 delegar último mensaje | r/F5 refrescar | d cancelar tarea | k kill spawn | Esc cerrar",
                id="ygg-help",
                classes="status-left",
            )

    def on_mount(self) -> None:
        self._refresh()
        try:
            self.query_one("#ygg-queue-list", ListView).focus()
        except Exception:
            pass

    def _app(self) -> Any:
        return self.app

    def _refresh(self) -> None:
        app = self._app()
        self._presets = app._load_subagent_presets() if hasattr(app, "_load_subagent_presets") else {}
        self._queue_items = app._yggdrasil_queue_messages() if hasattr(app, "_yggdrasil_queue_messages") else []
        self._spawn_items = app._yggdrasil_active_spawns() if hasattr(app, "_yggdrasil_active_spawns") else []

        if not self.is_mounted:
            return

        queue_list = self.query_one("#ygg-queue-list", ListView)
        queue_list.clear()
        for item in self._queue_items:
            label = f"{item['id']}: {item['task'][:40]}{'…' if len(item['task']) > 40 else ''}"
            if item.get("claimed_by"):
                label += f" → {item['claimed_by']}"
            queue_list.append(ListItem(Label(label, classes="ygg-queue-item")))
        if queue_list.children:
            queue_list.index = 0

        spawns_list = self.query_one("#ygg-spawns-list", ListView)
        spawns_list.clear()
        for item in self._spawn_items:
            label = f"{item['agent']} ({item.get('channel') or '?'})"
            task = item.get("task", "")
            if task:
                label += f" — {task[:35]}{'…' if len(task) > 35 else ''}"
            spawns_list.append(ListItem(Label(label, classes="ygg-spawn-item")))
        if spawns_list.children:
            spawns_list.index = 0

        presets_static = self.query_one("#ygg-presets", Static)
        lines: list[str] = []
        for i, (name, cfg) in enumerate(list(self._presets.items())[:8], start=1):
            provider = cfg.get("provider") or "?"
            model = cfg.get("model") or "?"
            lines.append(f"F{i}: [bold]{name}[/] — {provider}/{model}")
        if not lines:
            lines.append("[dim]No hay presets configurados.[/]")
            lines.append("[dim]Ver ~/.yggdrasil/hlidskjalf_subagents.yaml[/]")
        presets_static.update("\n".join(lines))

    def action_refresh(self) -> None:
        self._refresh()

    def action_dismiss_panel(self) -> None:
        self.dismiss(None)

    def _delegate_index(self, index: int) -> None:
        presets = list(self._presets.keys())
        if index >= len(presets):
            return
        app = self._app()
        if hasattr(app, "_delegate_to_preset"):
            app._delegate_to_preset(presets[index])
        self._refresh()

    def action_delegate_1(self) -> None:
        self._delegate_index(0)

    def action_delegate_2(self) -> None:
        self._delegate_index(1)

    def action_delegate_3(self) -> None:
        self._delegate_index(2)

    def action_delegate_4(self) -> None:
        self._delegate_index(3)

    def action_delegate_5(self) -> None:
        self._delegate_index(4)

    def action_delegate_6(self) -> None:
        self._delegate_index(5)

    def action_delegate_7(self) -> None:
        self._delegate_index(6)

    def action_delegate_8(self) -> None:
        self._delegate_index(7)

    def action_dequeue(self) -> None:
        app = self._app()
        queue_list = self.query_one("#ygg-queue-list", ListView)
        index = queue_list.index
        if index is None or index < 0 or index >= len(self._queue_items):
            return
        msg_id = self._queue_items[index]["id"]
        if hasattr(app, "_yggdrasil_cancel_queue_message"):
            app._yggdrasil_cancel_queue_message(msg_id)
        self._refresh()

    def action_kill_spawn(self) -> None:
        app = self._app()
        spawns_list = self.query_one("#ygg-spawns-list", ListView)
        index = spawns_list.index
        if index is None or index < 0 or index >= len(self._spawn_items):
            return
        agent_name = self._spawn_items[index]["agent"]
        if hasattr(app, "_yggdrasil_kill_spawn"):
            app._yggdrasil_kill_spawn(agent_name)
        self._refresh()
