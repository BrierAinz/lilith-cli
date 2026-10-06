"""Codebase navigation slash commands: /search, /tree, /map, /todos, /watch, /deps, /compare, /snippet, /explain, /whereami and /multi-file."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lilith_tools.file_walk import walk_all_files
from lilith_tools.search import (
    SearchAcrossFilesTool,
    SearchHistoryTool,
    SearchInFileTool,
)
from lilith_tools.todos import TodoAddTool, TodoDoneTool, TodoListTool, TodoRemoveTool
from lilith_tools.watcher import (
    WatchEventsTool,
    WatchFilesTool,
    WatchStatusTool,
    WatchStopTool,
)
from rich.syntax import Syntax
from rich.tree import Tree as RichTree

from ..config import CONFIG_DIR
from ..json_store import preserve_corrupt
from ..render import console, render_error
from ._shared import _print_tool_result

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime


async def run_watch_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /watch para suscribirse a eventos del sistema de archivos.

    Examples:
        /watch src
        /watch list
        /watch stop <id>
        /watch events <id>
    """
    text = args.strip()

    if not text or text.lower() in ("list", "ls", "status"):
        result = WatchStatusTool().execute()
        _print_watch_status(result)
        return

    parts = text.split()
    subcmd = parts[0].lower()

    if subcmd == "stop":
        if len(parts) < 2:
            render_error("Uso: /watch stop <id>")
            return
        result = WatchStopTool().execute(watch_id=parts[1])
        if not result.success:
            render_error(result.error or f"No se pudo detener {parts[1]}")
            return
        console.print(f"[success]✓ Watcher detenido: [bold cyan]{parts[1]}[/][/]")
        return

    if subcmd in ("events", "eventos"):
        if len(parts) < 2:
            render_error("Uso: /watch events <id>")
            return
        watch_id = parts[1]
        limit = 20
        if len(parts) >= 4 and parts[2] == "--limit":
            try:
                limit = int(parts[3])
            except ValueError:
                render_error("--limit requiere un número entero")
                return
        result = WatchEventsTool().execute(watch_id=watch_id)
        _print_watch_events(result, limit=limit)
        return

    # /watch <path>
    path = parts[0]
    patterns: list[str] = []
    ignore_patterns: list[str] = []
    i = 1
    while i < len(parts):
        token = parts[i]
        if token == "--patterns" and i + 1 < len(parts):
            patterns = [p.strip() for p in parts[i + 1].split(",") if p.strip()]
            i += 2
            continue
        if token == "--ignore" and i + 1 < len(parts):
            ignore_patterns = [p.strip() for p in parts[i + 1].split(",") if p.strip()]
            i += 2
            continue
        i += 1

    result = WatchFilesTool().execute(
        paths=[path], patterns=patterns, ignore_patterns=ignore_patterns
    )
    _print_watch_tool_result(result)


def _print_watch_tool_result(result) -> None:
    """Renderiza el resultado de una herramienta de watcher."""
    if not result.success:
        error = result.error or "Error desconocido ejecutando el watcher"
        render_error(error)
        return

    data = result.data or {}
    if "watch_id" in data:
        watch_id = data["watch_id"]
        paths = data.get("paths", [])
        console.print(f"[success]✓ Watcher iniciado: [bold cyan]{watch_id}[/][/]")
        console.print(f"  [dim]paths: {', '.join(str(p) for p in paths)}[/]")
        if data.get("patterns"):
            console.print(f"  [dim]patterns: {', '.join(data['patterns'])}[/]")
        return
    if data.get("stopped"):
        console.print(f"[success]✓ Watcher detenido: [bold cyan]{data.get('watch_id')}[/][/]")
        return

    console.print(str(data))


def _print_watch_status(result) -> None:
    """Muestra el estado de los watchers activos."""
    if not result.success:
        render_error(result.error or "Error consultando watchers")
        return

    data = result.data or {}
    watches = data.get("watches", [])
    if not watches:
        console.print("[dim]No hay watchers activos.[/]")
        return

    console.print("\n[bold realm]᛭ Watchers activos[/]")
    for w in watches:
        console.print(f"  [bold cyan]{w.get('watch_id')}[/]")
        console.print(f"    paths: {', '.join(str(p) for p in w.get('paths', []))}")
        if w.get("patterns"):
            console.print(f"    patterns: {', '.join(w['patterns'])}")
        if w.get("ignore_patterns"):
            console.print(f"    ignore: {', '.join(w['ignore_patterns'])}")
        console.print(f"    eventos: {w.get('event_count', 0)}")
    console.print()


def _print_watch_events(result, *, limit: int = 20) -> None:
    """Muestra los últimos eventos de un watcher."""
    if not result.success:
        render_error(result.error or "Error consultando eventos")
        return

    data = result.data or {}
    events = data.get("events", [])
    if not events:
        console.print("[dim]No hay eventos.[/]")
        return

    console.print(f"\n[bold realm]᛭ Eventos de {data.get('watch_id', '?')}[/]")
    for e in events[-limit:]:
        console.print(
            f"  [cyan]{e.get('event_type')}[/] {e.get('path')} "
            f"[dim]({e.get('timestamp', 0):.3f})[/]"
        )
    console.print()


async def run_todos_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /todos [add|done|remove|list|due|clear] usando las herramientas de todo.

    Examples:
        /todos
        /todos add comprar leche
        /todos done 1
        /todos remove 2
        /todos due
    """
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        result = TodoListTool().execute()
        if not result.success:
            render_error(result.error or "No se pudo listar las tareas")
            return
        data = result.data
        todos = data.get("todos", []) if isinstance(data, dict) else data
        _render_todos_table(todos if isinstance(todos, list) else [])
        return

    if text.lower() in ("due", "overdue"):
        result = TodoListTool().execute()
        if not result.success:
            render_error(result.error or "No se pudo listar las tareas")
            return
        data = result.data
        todos = data.get("todos", []) if isinstance(data, dict) else data
        _render_todos_due_table(todos if isinstance(todos, list) else [])
        return

    if text.lower() == "clear":
        from lilith_tools.todos import TodoManager

        count = TodoManager().clear()
        console.print(f"[success]✓ Lista de tareas limpiada ({count} eliminadas).[/]")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "add":
        if not rest:
            render_error("Uso: /todos add <texto>")
            return
        result = TodoAddTool().execute(text=rest)
    elif subcmd in ("done", "complete"):
        try:
            index = int(rest)
        except ValueError:
            render_error("Uso: /todos done <número>")
            return
        result = TodoDoneTool().execute(index=index)
    elif subcmd in ("remove", "rm", "delete"):
        try:
            index = int(rest)
        except ValueError:
            render_error("Uso: /todos remove <número>")
            return
        result = TodoRemoveTool().execute(index=index)
    else:
        render_error(f"Subcomando de /todos desconocido: {subcmd}")
        return

    _print_tool_result(result)


async def run_search_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /search para buscar en historial, un archivo o varios archivos.

    Examples:
        /search <query>
        /search in <path> <query>
        /search across <pattern> [path]
    """
    text = args.strip()
    if not text:
        _render_search_usage()
        return

    tokens = text.split(maxsplit=2)
    subcmd = tokens[0].lower()

    if subcmd == "in":
        if len(tokens) < 3:
            render_error("Uso: /search in <archivo> <consulta>")
            return
        path = tokens[1]
        query = tokens[2]
        result = SearchInFileTool().execute(path=path, query=query)
        _render_search_panel(result, kind="in_file", path=path, query=query)
        return

    if subcmd == "across":
        if len(tokens) < 2:
            render_error("Uso: /search across <patrón> [directorio]")
            return
        pattern = tokens[1]
        directory = tokens[2] if len(tokens) > 2 else "."
        result = SearchAcrossFilesTool().execute(pattern=pattern, path=directory)
        _render_search_panel(
            result, kind="across_files", pattern=pattern, directory=directory
        )
        return

    if subcmd in ("history", "hist"):
        query = tokens[1] if len(tokens) > 1 else ""
        result = SearchHistoryTool().execute(query=query)
        _render_search_panel(result, kind="history", query=query)
        return

    # default: search history
    result = SearchHistoryTool().execute(query=text)
    _render_search_panel(result, kind="history", query=text)


def _render_search_usage() -> None:
    """Muestra la ayuda de /search."""
    console.print("\n[bold realm]᛭ Uso de /search[/]")
    console.print("  [cyan]/search <consulta>[/]         — buscar en historial")
    console.print("  [cyan]/search in <archivo> <consulta>[/]")
    console.print("  [cyan]/search across <patrón> [dir][/] — búsqueda en archivos")
    console.print("  [cyan]/search history <consulta>[/]   — alias explícito de historial")
    console.print()


def _render_todos_table(todos: list) -> None:
    """Render a list of todos as a Rich Table with checkbox icons."""
    from rich.table import Table

    if not todos:
        console.print("[dim]No hay tareas pendientes.[/dim]")
        return

    table = Table(
        title="[bold realm]᛭ Tareas pendientes[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
        caption=f"[dim]{len(todos)} tarea(s)[/dim]",
    )
    table.add_column("#", style="bold cyan", justify="right", no_wrap=True, width=4)
    table.add_column("Estado", justify="center", width=8)
    table.add_column("Tarea", style="white")

    for i, todo in enumerate(todos, start=1):
        if isinstance(todo, dict):
            content = str(todo.get("text", todo.get("content", todo.get("task", str(todo)))))
            status = str(todo.get("status", "done" if todo.get("done") else "pending"))
        else:
            content = str(todo)
            status = "pending"
        status_lower = status.lower()
        if status_lower in ("done", "completed", "complete"):
            mark = "[bold green]✓[/bold green]"
        elif status_lower in ("in_progress", "active", "working"):
            mark = "[bold yellow]●[/bold yellow]"
        else:
            mark = "[dim]○[/dim]"
        table.add_row(str(i), mark, content)

    console.print(table)
    console.print()


def _render_todos_due_table(todos: list) -> None:
    """Render tasks due today or earlier, with a legacy-storage fallback."""
    from rich.table import Table

    today = datetime.now().astimezone().date()
    has_due_dates = any(
        isinstance(todo, dict) and (todo.get("due_date") or todo.get("due"))
        for todo in todos
    )

    if has_due_dates:
        visible = []
        for todo in todos:
            if not isinstance(todo, dict) or todo.get("done") is True:
                continue
            raw_due = todo.get("due_date") or todo.get("due")
            if not raw_due:
                continue
            try:
                due = datetime.fromisoformat(str(raw_due).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if due <= today:
                visible.append((todo, str(raw_due)[:10]))
        empty_message = "No hay tareas vencidas ni con vencimiento hoy."
        date_heading = "Vence"
    else:
        visible = [
            (todo, str(todo.get("created_at", "—"))[:10])
            for todo in todos
            if isinstance(todo, dict) and todo.get("done") is not True
        ]
        empty_message = "No hay tareas para revisar."
        date_heading = "Creada"
        if visible:
            console.print(
                "[warning]El almacenamiento actual no admite fechas de vencimiento; "
                "se muestran todas las tareas para revisión manual.[/]"
            )

    if not visible:
        console.print(f"[dim]{empty_message}[/]")
        return

    table = Table(
        title="[bold realm]᛭ Tareas por revisar[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("Tarea")
    table.add_column(date_heading, style="dim", no_wrap=True)
    for fallback_index, (todo, date_text) in enumerate(visible, start=1):
        index = todo.get("index", fallback_index)
        text = todo.get("text", todo.get("content", str(todo)))
        table.add_row(str(index), str(text), date_text or "—")
    console.print(table)
    console.print()


# ── Tree command ─────────────────────────────────────────────────────


_TREE_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", ".egg-info", "dist", "build", ".tox", ".mypy_cache", ".ruff_cache"}
_TREE_IGNORED_FILES = {".DS_Store", "Thumbs.db"}


def _format_tree_size(size: int) -> str:
    """Devuelve un tamaño legible con unidades."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.2f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.2f} TB"


def _build_tree(
    root: Path,
    tree: RichTree,
    depth: int,
    max_depth: int,
) -> tuple[int, int]:
    """Recorre *root* recursivamente y agrega ramas a *tree*.

    Returns (files_count, dirs_count).
    """
    files_count = 0
    dirs_count = 0
    if depth >= max_depth:
        return files_count, dirs_count

    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        tree.add("[dim]└─ permiso denegado[/]")
        return files_count, dirs_count
    except OSError:
        return files_count, dirs_count

    for entry in entries:
        if entry.is_dir():
            if entry.name in _TREE_IGNORED_DIRS:
                continue
            dirs_count += 1
            label = f"[bold cyan]📁 {entry.name}[/]"
            branch = tree.add(label)
            sub_files, sub_dirs = _build_tree(entry, branch, depth + 1, max_depth)
            files_count += sub_files
            dirs_count += sub_dirs
            if depth + 1 >= max_depth and any(entry.iterdir()):
                branch.add("[dim]└─ ...[/]")
            continue

        if entry.name in _TREE_IGNORED_FILES:
            continue

        files_count += 1
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        size_str = _format_tree_size(size)
        tree.add(f"[tool.result]📄 {entry.name}[/] [dim]({size_str})[/]")

    return files_count, dirs_count


def _repo_map_entries(root: Path) -> list[tuple[str, str, int]]:
    """Collecta archivos Python con sus símbolos principales para /map."""
    entries: list[tuple[str, str, int]] = []
    # Antes esto era rglob("*.py") y luego descartaba por _TREE_IGNORED_DIRS,
    # o sea que ya habia bajado a .venv y a node_modules antes de descartarlos:
    # el coste estaba pagado. Podar reutiliza la misma lista de ignorados.
    candidatos = walk_all_files(
        root, "*.py", exclude_dirs=frozenset(_TREE_IGNORED_DIRS)
    )
    for path in sorted(candidatos, key=lambda item: str(item).lower()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        symbols = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.name.startswith("_")
        ]
        entries.append((str(path.relative_to(root)), ", ".join(symbols[:8]), len(symbols)))
    return entries


async def run_map_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Muestra un mapa conciso de módulos Python y símbolos públicos.

    Uso: /map [directorio] [--json]
    """
    text = args.strip()
    tokens = text.split()
    json_output = "--json" in tokens
    tokens = [token for token in tokens if token != "--json"]
    if len(tokens) > 1:
        render_error("Uso: /map [directorio] [--json]")
        return
    root = Path(tokens[0]).expanduser() if tokens else Path.cwd()

    if not root.exists():
        render_error(f"Ruta no encontrada: {root}")
        return
    if not root.is_dir():
        render_error(f"La ruta no es un directorio: {root}")
        return

    entries = _repo_map_entries(root)
    if json_output:
        from ..render import print_json

        print_json(
            [
                {"archivo": path, "símbolos": symbols, "cantidad": count}
                for path, symbols, count in entries
            ],
            indent=2,
        )
        return

    if not entries:
        console.print("[dim]No se encontraron módulos Python en la ruta indicada.[/]")
        return

    console.print(f"\n[bold realm]᛭ Mapa del código[/] [dim]{root.resolve()}[/]")
    for path, symbols, count in entries:
        symbol_text = symbols or "(sin símbolos públicos)"
        suffix = f" · {count} símbolos" if count else ""
        console.print(f"  [bold cyan]{path}[/]{suffix} — [dim]{symbol_text}[/]")
    console.print(f"\n[dim]Módulos: {len(entries)}[/]")


async def run_tree_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /tree para mostrar el árbol de archivos del directorio actual.

    Diferente a system_info.directory_list: muestra una jerarquía visual con
    iconos y tamaños, limitada por profundidad.

    Examples:
        /tree                       — árbol del directorio actual (profundidad 3)
        /tree src                   — árbol del directorio indicado
        /tree src depth=2           — profundidad personalizada
    """
    text = args.strip()
    if text:
        first = text.split(maxsplit=1)[0].lower()
        if first in ("symbols", "map"):
            await run_map_command(session, text[len(first):].strip())
            return

    target = Path.cwd()
    max_depth = 3

    if text:
        parts = text.split()
        # Primer argumento posicional es el path si no parece depth=N.
        if parts and not parts[0].lower().startswith("depth="):
            target = Path(parts[0]).expanduser()
            parts = parts[1:]

        for part in parts:
            if part.lower().startswith("depth="):
                try:
                    max_depth = int(part.split("=", 1)[1])
                except ValueError:
                    render_error("depth debe ser un número entero")
                    return

    if not target.exists():
        render_error(f"Ruta no encontrada: {target}")
        return
    if not target.is_dir():
        render_error(f"La ruta no es un directorio: {target}")
        return

    tree = RichTree(f"[bold realm]📂 {target.resolve()}[/]")
    files_count = dirs_count = 0
    try:
        files_count, dirs_count = _build_tree(target, tree, 0, max_depth)
    except PermissionError:
        render_error(f"Permiso denegado al recorrer: {target.resolve()}")
        return

    console.print(f"\n[bold realm]᛭ Árbol de archivos[/]")
    console.print(tree)
    console.print(f"\n[dim]Directorios: {dirs_count} | Archivos: {files_count} | Profundidad: {max_depth}[/]")


# ── /multi-file (atomic multi-file edit transaction) ────────────────────────


def _parse_multi_file_spec(text: str) -> list[dict]:
    """Parse ``[file] old -> new ; [file2] old2 -> new2`` into edits."""
    parts = [p.strip() for p in text.split(";") if p.strip()]
    edits: list[dict] = []
    for part in parts:
        if not part.startswith("["):
            return []
        # find matching ]
        close = part.find("]")
        if close < 0:
            return []
        path = part[1:close].strip()
        rest = part[close + 1:].strip()
        if "->" not in rest:
            return []
        old, new = rest.split("->", 1)
        edits.append({"path": path, "old_string": old.strip(), "new_string": new.strip()})
    return edits


async def run_multi_file_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Atomic multi-file edit (/multi-file '[file] old -> new ; ...')."""
    from lilith_tools.filesystem import BatchEditTool

    text = args.strip()
    if not text:
        console.print(
            "[dim]Uso: /multi-file \\[archivo] viejo -> nuevo ; \\[archivo2] viejo -> nuevo[/]"
        )
        return

    edits = _parse_multi_file_spec(text)
    if not edits:
        console.print(
            "[error]Formato inválido. Usa: \\[archivo] viejo -> nuevo ; \\[archivo2] viejo -> nuevo[/]"
        )
        return

    result = BatchEditTool().execute(edits=edits, preview=False)
    if not result.success:
        console.print(f"[error]{result.error or 'Error aplicando ediciones'}[/error]")
        return

    data = result.data or {}
    edits_done = data.get("edits", []) if isinstance(data, dict) else []
    for edit in edits_done:
        path = edit.get("path", "?") if isinstance(edit, dict) else "?"
        repls = edit.get("replacements", 1) if isinstance(edit, dict) else 1
        console.print(f"[success]✓ Editado {path} ({repls} reemplazo(s))[/]")
    if not edits_done:
        console.print(f"[success]✓ {len(edits)} edición(es) aplicadas[/success]")
    console.print()

# ── /explain command ───────────────────────────


async def run_explain_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Explain a file or a Lilith feature (/explain [path] | --feature <name>)."""
    text = args.strip()
    feature: str | None = None
    path: str | None = None
    depth: str = "deep"

    if text:
        tokens = text.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--feature" and i + 1 < len(tokens):
                feature = tokens[i + 1]
                i += 2
            elif tok.startswith("--feature="):
                feature = tok.split("=", 1)[1]
                i += 1
            elif tok in ("--depth",):
                if i + 1 < len(tokens):
                    depth = tokens[i + 1]
                    i += 2
                else:
                    i += 1
            else:
                path = path or tok
                i += 1

    if depth not in ("shallow", "deep"):
        render_error("Uso: /explain --depth {shallow|deep}")
        return

    # Feature lookup path
    if feature is not None:
        from lilith_cli import _FEATURE_DOCS
        doc = _FEATURE_DOCS.get(feature)
        if not doc:
            known = ", ".join(sorted(_FEATURE_DOCS.keys()))
            render_error(f"Feature desconocida: {feature}. Conocidas: {known}")
            return
        console.print(f"[info]Feature:[/info] [bold cyan]{feature}[/bold cyan]")
        if depth == "shallow":
            sentences = doc.split(". ")
            short = ". ".join(sentences[:2])
            if not short.endswith("."):
                short += "."
            console.print(f"[dim](shallow)[/dim] {short}")
        else:
            console.print(doc)
        return

    # File path explanation
    if path is None:
        console.print(
            "[dim]Uso: /explain [archivo] | /explain --feature <nombre> [--depth shallow|deep][/dim]"
        )
        return

    target = Path(path).expanduser()
    if not target.exists():
        render_error(f"Archivo no encontrado: {target}")
        return
    if not target.is_file():
        render_error(f"No es un archivo: {target}")
        return

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {target}: {exc}")
        return

    if depth == "shallow":
        truncated = content[:500]
        console.print(f"[info]Resumen shallow de[/info] [bold cyan]{target}[/bold cyan] (primeros 500 chars):")
        console.print(truncated)
    else:
        console.print(f"[info]Contenido de[/info] [bold cyan]{target}[/bold cyan] ({len(content)} chars):")
        console.print(content)

# ── /whereami command ───────────────────────


async def run_whereami_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Show project context with a Rich panel (/whereami)."""
    import platform as _platform
    import sys as _sys

    from rich.panel import Panel
    from rich.table import Table

    cwd = Path.cwd()

    # Info grid
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Working dir", str(cwd))
    grid.add_row("Python", f"{_platform.python_implementation()} {_sys.version.split()[0]}")
    grid.add_row("Platform", f"{_platform.system()} {_platform.release()} ({_platform.machine()})")

    # Git branch + last commit
    import subprocess as _sp
    try:
        branch_proc = _sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, cwd=cwd,
        )
        branch = branch_proc.stdout.strip() or "(detached HEAD)"
        last_proc = _sp.run(
            ["git", "log", "-1", "--oneline"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, cwd=cwd,
        )
        last = last_proc.stdout.strip() or "(no commits)"
        grid.add_row("Git branch", f"[bold cyan]{branch}[/bold cyan]")
        grid.add_row("Last commit", f"[dim]{last}[/dim]")
    except (FileNotFoundError, _sp.TimeoutExpired):
        grid.add_row("Git", "[dim](not available)[/dim]")

    # Lilith version
    try:
        from lilith_cli import __version__
        grid.add_row("Lilith version", f"[bold cyan]v{__version__}[/bold cyan]")
    except ImportError:
        grid.add_row("Lilith version", "[dim](unknown)[/dim]")

    # Pyproject summary if available
    pyproject = cwd / "pyproject.toml"
    if pyproject.exists():
        grid.add_row("Project", f"[dim]{pyproject.name}[/dim]" if hasattr(pyproject, "name") else "[dim]pyproject.toml present[/dim]")
    else:
        grid.add_row("Project", "[dim](no pyproject.toml)[/dim]")

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Whereami[/]",
        subtitle=f"[dim]{cwd.name}[/dim]",
        border_style="cyan",
        expand=False,
    ))
    console.print()
"""Source for /deps slash command block. Appended to extra_commands.py by _deps_section.py."""


def _deps_parse_pep508(spec: str) -> tuple[str, str]:
    """Parse 'name[extra]>=version' -> ('name', 'version')."""
    spec = spec.strip().strip("\"'")
    if ";" in spec:
        spec = spec.split(";", 1)[0].strip()
    m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?\s*([\^~>=<!\s,\d\.\*\w\-]+)?", spec)
    if not m:
        return (spec or "?", "?")
    name = m.group(1)
    ver = (m.group(2) or "").strip()
    if not ver:
        return (name, "?")
    parts = ver.split(",")[0].strip()
    return (name, parts)


def _deps_read_pyproject(path: Path) -> list[tuple[str, str, str]]:
    """Return [(name, version, source)] from pyproject.toml [project] + [dependency-groups].

    Handles PEP 621 inline arrays (`deps = ["a>=1", "b>=2"]`), multi-line arrays
    (`deps = [\n  "a>=1",\n]`), and PEP 735 dependency-group tables.
    """
    out: list[tuple[str, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    section: str | None = None
    buf: list[str] = []
    in_array = False

    def flush() -> None:
        nonlocal buf
        if not buf or section is None:
            buf = []
            return
        if section == "project" or section.startswith("dependency-group"):
            # PEP 621 inline arrays may have multiple deps on one line; split on
            # commas and whitespace. Trailing commas are stripped per chunk before joining.
            cleaned = [b.strip().rstrip(",").strip() for b in buf if b.strip().rstrip(",").strip()]
            joined = " ".join(cleaned).strip().strip("[]")
            for piece in re.split(r"[,\s]+", joined):
                piece = piece.strip()
                if not piece:
                    continue
                out.append(_deps_parse_pep508(piece) + ("pyproject",))
        buf = []
    def consume_array_close(line: str) -> bool:
        """If line contains a closing `]`, consume up to it and return True."""
        idx = line.find("]")
        if idx == -1:
            return False
        before = line[:idx].strip()
        if before:
            buf.append(before)
        return True

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Section header
        if line.startswith("[") and line.endswith("]"):
            flush()
            section = line[1:-1].strip()
            in_array = False
            continue
        # Skip if not in a relevant section
        if section is None or not (
            section == "project" or section.startswith("dependency-group")
        ):
            continue
        # Inline / multi-line array assignment: key = [ ... ] or key = [...]
        if "=" in line and "[" in line and "]" not in line.split("[", 1)[1]:
            # Multi-line opening: key = [
            buf.append(line.split("[", 1)[1].strip())
            in_array = True
            continue
        if "=" in line and "[" in line:
            # Single-line: key = [ ... ]
            inside = line.split("[", 1)[1]
            inside = inside.rsplit("]", 1)[0]
            buf.append(inside.strip())
            flush()
            in_array = False
            continue
        if in_array:
            if consume_array_close(line):
                flush()
                in_array = False
            else:
                buf.append(line.strip().rstrip(","))
            continue
        # Plain key = value with deps in array form already handled above
        # Skip non-array assignments in [project] (name, version, etc.)
        if "=" in line:
            continue
        # Bare dep line (rare in pyproject but supported)
        buf.append(line.strip("'\""))
    flush()
    return out


def _deps_read_requirements(path: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        name, ver = _deps_parse_pep508(line)
        if name and name != "?":
            out.append((name, ver, "requirements"))
    return out


def _deps_read_package_json(path: Path) -> list[tuple[str, str, str]]:
    import json

    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[tuple[str, str, str]] = []
    for section in ("dependencies", "devDependencies"):
        deps = obj.get(section) or {}
        if not isinstance(deps, dict):
            continue
        for name, ver in deps.items():
            out.append((name, str(ver), "npm"))
    return out


def _deps_read_uv_lock(path: Path) -> dict[str, str]:
    """Parse uv.lock -> {pkg_name: license}. Best-effort."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    pkgs: dict[str, str] = {}
    # Sin ``re.MULTILINE``, y cortando con ``\Z`` en vez de ``$``: con
    # MULTILINE el ``$`` del lookahead coincide con el fin de la PRIMERA
    # línea, y como ``[\s\S]*?`` es lazy cada bloque se cerraba en
    # ``[[package]]`` a secas. El cuerpo nunca se leía, así que esta
    # función devolvía siempre un dict vacío.
    for m in re.finditer(r"\[\[package\]\][\s\S]*?(?=\[\[|\Z)", text):
        block = m.group(0)
        nm = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if not nm:
            continue
        name = nm.group(1)
        lic = re.search(r'^license\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if lic:
            pkgs[name] = lic.group(1)
            continue
        lic_tbl = re.search(r"text\s*=\s*\"([^\"]+)\"", block)
        if lic_tbl:
            pkgs[name] = lic_tbl.group(1)
    return pkgs


def _deps_collect(target: Path) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    deps: list[tuple[str, str, str]] = []
    licenses: dict[str, str] = {}
    pyproject = target / "pyproject.toml"
    if pyproject.exists():
        deps.extend(_deps_read_pyproject(pyproject))
    req = target / "requirements.txt"
    if req.exists():
        deps.extend(_deps_read_requirements(req))
    pkg = target / "package.json"
    if pkg.exists():
        deps.extend(_deps_read_package_json(pkg))
    uv_lock = target / "uv.lock"
    if uv_lock.exists():
        licenses = _deps_read_uv_lock(uv_lock)
    return deps, licenses


def _deps_render_table(deps, licenses) -> None:
    from rich.table import Table

    table = Table(
        title="[bold realm]\u16ed Dependencias[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=True,
    )
    table.add_column("Paquete", style="bold white", no_wrap=True)
    table.add_column("Versión", style="cyan")
    table.add_column("Origen", style="magenta")
    table.add_column("Licencia", style="green")
    if not deps:
        console.print("[dim]No se encontraron dependencias en manifiestos conocidos.[/dim]")
        return
    for name, ver, source in deps:
        lic = licenses.get(name, "?")
        table.add_row(name, ver, source, lic)
    console.print(table)
    console.print()


def _deps_render_outdated(deps) -> None:
    py_only = [(n, v) for n, v, s in deps if s in ("pyproject", "requirements")]
    if not py_only:
        console.print("[dim]No hay dependencias Python para chequear.[/dim]")
        return
    console.print(f"[info]Chequeando {len(py_only)} paquetes Python...[/info]")
    pip = shutil.which("pip") or shutil.which("pip3")
    if not pip:
        console.print("[dim]pip no disponible — chequeo omitido.[/dim]")
        return
    for name, declared in py_only:
        try:
            proc = subprocess.run(
                [pip, "index", "versions", name],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
        except (subprocess.TimeoutExpired, OSError):
            console.print(f"  [dim]{name}: chequeo omitido (red/límite)[/dim]")
            continue
        m = re.search(r"(\d+\.\d+\.\d+(?:[a-zA-Z0-9_.+-]*)?)", out)
        latest = m.group(1) if m else "?"
        if latest == "?":
            console.print(f"  [dim]{name}: no se pudo determinar — chequeo omitido.[/dim]")
            continue
        clean_decl = declared.lstrip("^~>=<! ")
        if clean_decl.startswith(latest):
            status = "[green]✓ al día[/green]"
        elif declared == "?":
            status = f"[dim]{latest}[/dim]"
        else:
            status = f"[yellow]actualizar a {latest}[/yellow]"
        console.print(
            f"  [bold cyan]{name}[/bold cyan]: declarado=[dim]{declared}[/dim]  →  {status}"
        )


def _deps_render_licenses(licenses) -> None:
    if not licenses:
        console.print("[dim]No se encontró archivo de bloqueo (uv.lock).[/dim]")
        return
    from rich.table import Table

    table = Table(
        title="[bold realm]\u16ed Licencias (uv.lock)[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=True,
    )
    table.add_column("Paquete", style="bold white", no_wrap=True)
    table.add_column("Licencia", style="green")
    for name, lic in sorted(licenses.items()):
        table.add_row(name, lic)
    console.print(table)
    console.print()


async def run_deps_command(session: SessionRuntime, args: str) -> None:
    """Manage project dependencies from common manifests.

    Usage:
        /deps [path]            -> Show deps from pyproject/requirements/package.json
        /deps outdated [path]   -> Best-effort newer version check (Python pkgs)
        /deps licenses [path]   -> List licenses from uv.lock when present
        /deps help              -> Show usage

    Flags:
        --json                  -> Emit machine-readable JSON instead of the
                                  Rich table (works with all subcommands and
                                  with the default listing).
    """
    args_clean = (args or "").strip()
    tokens = args_clean.split()
    as_json = "--json" in tokens
    # ``--json`` no es un subcomando ni un path — lo descartamos para no
    # contaminar el resto del parseo (especialmente cuando aparece después
    # del subcomando, p.ej. ``/deps outdated --json``).
    tokens = [t for t in tokens if t != "--json"]

    if not tokens:
        target = Path.cwd()
        deps, licenses = _deps_collect(target)
        if as_json:
            _deps_emit_json(target, deps, licenses, mode="list")
            return
        console.print(f"[info]\u16ed Dependencias en:[/info] [bold cyan]{target}[/bold cyan]")
        _deps_render_table(deps, licenses)
        return

    sub = tokens[0].lower()
    rest = tokens[1:]

    if sub in ("help", "--help", "-h", "?"):
        console.print("[bold realm]\u16ed /deps — Gestión de dependencias[/]")
        console.print()
        console.print("  [bold cyan]/deps [path][/bold cyan]            → Lista dependencias detectadas")
        console.print("  [bold cyan]/deps outdated [path][/bold cyan]   → Chequeo de versiones más nuevas")
        console.print("  [bold cyan]/deps licenses [path][/bold cyan]   → Licencias desde uv.lock")
        console.print("  [bold cyan]/deps help[/bold cyan]              → Esta ayuda")
        console.print()
        console.print(
            "  [dim]Manifiestos soportados:[/dim] [green]pyproject.toml[/], [green]requirements.txt[/], [green]package.json[/]"
        )
        console.print("  [dim]Bloqueo de licencias:[/dim] [green]uv.lock[/]")
        console.print()
        console.print(
            "  [dim]Flag:[/dim] [bold cyan]--json[/bold cyan]              → salida machine-readable (todos los subcomandos)"
        )
        console.print()
        return

    if sub == "outdated":
        target = Path(rest[0]).expanduser().resolve() if rest else Path.cwd()
        if not target.exists() or not target.is_dir():
            render_error(f"Ruta no encontrada o no es directorio: {target}")
            return
        deps, _ = _deps_collect(target)
        if as_json:
            # La verificación de versiones es online y lenta; en modo JSON
            # emitimos el snapshot declarado (sin red) para que pipelines y
            # tests no queden esperando al subprocess de ``pip index``.
            _deps_emit_json(target, deps, {}, mode="outdated")
            return
        console.print(f"[info]\u16ed Versiones en:[/info] [bold cyan]{target}[/bold cyan]")
        _deps_render_outdated(deps)
        return

    if sub == "licenses":
        target = Path(rest[0]).expanduser().resolve() if rest else Path.cwd()
        if not target.exists() or not target.is_dir():
            render_error(f"Ruta no encontrada o no es directorio: {target}")
            return
        deps, licenses = _deps_collect(target)
        if not licenses:
            if as_json:
                # En modo JSON emitimos un payload vacío en vez de error,
                # para que los pipelines downstream puedan parsear sin
                # branching especial por el caso ``sin uv.lock``.
                _deps_emit_json(target, deps, {}, mode="licenses")
                return
            render_error("No se encontró uv.lock para licencias")
            return
        if as_json:
            _deps_emit_json(target, deps, licenses, mode="licenses")
            return
        console.print(f"[info]\u16ed Licencias en:[/info] [bold cyan]{target}[/bold cyan]")
        _deps_render_licenses(licenses)
        return

    target = Path(sub).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        render_error(f"Ruta no encontrada o no es directorio: {sub}")
        return
    deps, licenses = _deps_collect(target)
    if as_json:
        _deps_emit_json(target, deps, licenses, mode="list")
        return
    console.print(f"[info]\u16ed Dependencias en:[/info] [bold cyan]{target}[/bold cyan]")
    _deps_render_table(deps, licenses)


def _deps_emit_json(
    target: Path,
    deps: list[tuple[str, str, str]],
    licenses: dict[str, str],
    *,
    mode: str,
) -> None:
    """Emit a stable, machine-readable snapshot of the /deps payload.

    ``mode`` is one of ``"list"``, ``"outdated"`` or ``"licenses"`` so
    pipelines downstream can distinguish which subcommand produced the
    payload without having to re-parse Rich output. The output is one
    JSON object per ``/deps`` invocation; ``--json`` is intended to be
    pipe-friendly (``jq``, redirección a archivo, etc.).
    """
    payload: dict[str, object] = {
        "target": str(target),
        "mode": mode,
        "deps": [
            {
                "name": name,
                "version": version,
                "source": source,
                "license": licenses.get(name, ""),
            }
            for name, version, source in deps
        ],
        "licenses": dict(licenses),
        "count": len(deps),
    }
    # IMPORTANT: emit via ``sys.stdout`` (not Rich ``console.print``) so
    # the JSON stays on a single, unwrapped line and pipelines can
    # ``jq`` it directly. Rich would otherwise word-wrap long lines,
    # injecting newlines into the middle of values like the target path.
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    sys.stdout.write("\n")


# ── /compare command ───────────────────────────────────────────────────


_COMPARE_CACHE_FILE = CONFIG_DIR / "compare_last.json"


def _compare_cache_save(payload: dict[str, Any]) -> None:
    """Persist the last comparison payload to disk; never raises."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _COMPARE_CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        # Storage is optional, swallow.
        pass


def _compare_diff_text(text_a: str, text_b: str, label_a: str, label_b: str) -> str:
    import difflib

    a_lines = text_a.splitlines(keepends=True)
    b_lines = text_b.splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=label_a,
        tofile=label_b,
        lineterm="",
    )
    return "".join(diff)


def _compare_diff_files(path_a: Path, path_b: Path) -> None:
    try:
        text_a = path_a.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {path_a}: {exc}")
        return
    try:
        text_b = path_b.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {path_b}: {exc}")
        return

    label_a = path_a.name or str(path_a)
    label_b = path_b.name or str(path_b)
    diff_text = _compare_diff_text(text_a, text_b, label_a, label_b)

    _compare_cache_save(
        {
            "mode": "files",
            "a": str(path_a),
            "b": str(path_b),
            "changed": bool(diff_text.strip()),
        }
    )

    if not diff_text.strip():
        console.print(f"[success]\u16ed Sin diferencias entre {label_a} y {label_b}[/success]")
        return

    console.print(f"[info]\u16ed Diff unificado:[/info] [bold cyan]{label_a}[/bold cyan] → [bold cyan]{label_b}[/bold cyan]")
    console.print(Syntax(diff_text, "diff", theme="monokai", word_wrap=True))


def _compare_json_walk(prefix: str, a: Any, b: Any, lines: list[str]) -> None:
    """Recursively diff two JSON-like values, appending flat lines for display.

    Reports added / removed / changed keys at every nesting level.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        keys = sorted(set(a.keys()) | set(b.keys()))
        for key in keys:
            child = f"{prefix}.{key}" if prefix else key
            in_a = key in a
            in_b = key in b
            if in_a and not in_b:
                lines.append(f"  a.{child} = {a[key]!r}  |  (ausente en b)  → eliminado")
            elif in_b and not in_a:
                lines.append(f"  (ausente en a)  |  b.{child} = {b[key]!r}  → añadido")
            else:
                _compare_json_walk(child, a[key], b[key], lines)
    elif isinstance(a, list) and isinstance(b, list):
        if a == b:
            return
        if len(a) != len(b):
            lines.append(
                f"  a.{prefix} = {a!r}  |  b.{prefix} = {b!r}  → cambiado (listas de distinto tamaño)"
            )
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _compare_json_walk(f"{prefix}[{i}]", x, y, lines)
    else:
        if a == b:
            lines.append(f"  a.{prefix} = {a!r}  |  b.{prefix} = {b!r}  → igual")
        else:
            lines.append(f"  a.{prefix} = {a!r}  |  b.{prefix} = {b!r}  → cambiado")


def _compare_json_files(path_a: Path, path_b: Path) -> None:
    try:
        raw_a = path_a.read_text(encoding="utf-8")
    except OSError as exc:
        render_error(f"No se pudo leer {path_a}: {exc}")
        return
    try:
        raw_b = path_b.read_text(encoding="utf-8")
    except OSError as exc:
        render_error(f"No se pudo leer {path_b}: {exc}")
        return

    try:
        data_a = json.loads(raw_a)
    except json.JSONDecodeError as exc:
        render_error(f"JSON inválido en {path_a}: {exc}")
        return
    try:
        data_b = json.loads(raw_b)
    except json.JSONDecodeError as exc:
        render_error(f"JSON inválido en {path_b}: {exc}")
        return

    lines: list[str] = []
    _compare_json_walk("", data_a, data_b, lines)

    changed = [ln for ln in lines if "→ cambiado" in ln or "eliminado" in ln or "añadido" in ln]
    equal = [ln for ln in lines if "→ igual" in ln]

    label_a = path_a.name or str(path_a)
    label_b = path_b.name or str(path_b)
    console.print(
        f"[info]\u16ed Comparación JSON:[/info] [bold cyan]{label_a}[/bold cyan] vs [bold cyan]{label_b}[/bold cyan]"
    )

    if not lines:
        console.print("[success]\u16ed Sin diferencias detectadas[/success]")
    else:
        for ln in lines:
            if "→ cambiado" in ln or "eliminado" in ln or "añadido" in ln:
                console.print(f"[yellow]{ln}[/yellow]")
            else:
                console.print(f"[dim]{ln}[/dim]")

    console.print(
        f"  [dim]Resumen: {len(changed)} cambiado(s) / añadido(s) / eliminado(s), {len(equal)} igual(es)[/dim]"
    )

    _compare_cache_save(
        {
            "mode": "json",
            "a": str(path_a),
            "b": str(path_b),
            "changed_count": len(changed),
            "equal_count": len(equal),
        }
    )


def _compare_text_stats(path_a: Path, path_b: Path) -> None:
    try:
        text_a = path_a.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {path_a}: {exc}")
        return
    try:
        text_b = path_b.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {path_b}: {exc}")
        return

    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()
    words_a = text_a.split()
    words_b = text_b.split()
    set_a = set(lines_a)
    set_b = set(lines_b)

    common = sorted(set_a & set_b)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)

    label_a = path_a.name or str(path_a)
    label_b = path_b.name or str(path_b)

    from rich.table import Table

    table = Table(
        title=f"[bold realm]\u16ed Estadísticas: {label_a} vs {label_b}[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=True,
    )
    table.add_column("Métrica", style="bold white", no_wrap=True)
    table.add_column(label_a, justify="right", style="cyan")
    table.add_column(label_b, justify="right", style="cyan")
    table.add_column("Común / único", justify="right", style="dim")

    table.add_row("Líneas", str(len(lines_a)), str(len(lines_b)), str(len(common)))
    table.add_row("Caracteres", str(len(text_a)), str(len(text_b)), "—")
    table.add_row("Palabras", str(len(words_a)), str(len(words_b)), "—")
    table.add_row(
        "Líneas únicas",
        str(len(only_a)),
        str(len(only_b)),
        f"compartidas={len(common)}",
    )

    console.print(table)
    if only_a or only_b:
        console.print(
            f"  [dim]Únicas en a: {len(only_a)} · únicas en b: {len(only_b)}[/dim]"
        )

    _compare_cache_save(
        {
            "mode": "text",
            "a": str(path_a),
            "b": str(path_b),
            "lines_a": len(lines_a),
            "lines_b": len(lines_b),
            "common_lines": len(common),
        }
    )


def _compare_print_help() -> None:
    console.print("[bold realm]\u16ed /compare — Comparar archivos[/]")
    console.print()
    console.print("  [bold cyan]/compare files <a> <b>[/bold cyan]  → diff unificado entre dos archivos")
    console.print("  [bold cyan]/compare json <a> <b>[/bold cyan]   → diff estructural entre dos JSON")
    console.print("  [bold cyan]/compare text <a> <b>[/bold cyan]   → estadísticas de líneas / palabras / caracteres")
    console.print()
    console.print("  [dim]La última comparación se guarda en:[/dim] [green]" + str(_COMPARE_CACHE_FILE) + "[/green]")


async def run_compare_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /compare para comparar archivos en tres modos: files, json, text.

    Examples:
        /compare files ruta/a.py ruta/b.py
        /compare json config_a.json config_b.json
        /compare text notas_a.md notas_b.md
        /compare recent text         — compara los últimos 2 archivos editados
    """
    text = (args or "").strip()
    if not text:
        _compare_print_help()
        return

    parts = text.split()
    sub = parts[0].lower()
    rest = parts[1:]
    paths = [p for p in rest if not p.startswith("--")]
    # Allow -- como separador; lo descartamos si aparece al final.
    paths = [p for p in paths if p != "--"]

    if sub in ("help", "--help", "-h", "?"):
        _compare_print_help()
        return

    # /compare recent <mode> — pick the last two files from session history.
    if sub == "recent":
        mode = rest[0].lower() if rest and rest[0] in ("files", "json", "text") else "text"
        recent_paths = _compare_recent_paths(session, count=2)
        if len(recent_paths) < 2:
            render_error(
                f"Se necesitan al menos 2 archivos editados en la sesión; "
                f"hay {len(recent_paths)}."
            )
            return
        path_a = Path(recent_paths[0])
        path_b = Path(recent_paths[1])
        if mode == "files":
            _compare_diff_files(path_a, path_b)
        elif mode == "json":
            _compare_json_files(path_a, path_b)
        else:
            _compare_text_stats(path_a, path_b)
        return

    if sub not in ("files", "json", "text"):
        render_error(
            f"Subcomando desconocido: {sub}. Use: files | json | text | recent (o /compare help)"
        )
        _compare_print_help()
        return

    if len(paths) < 2:
        render_error(
            f"Faltan rutas: /compare {sub} <archivo_a> <archivo_b>"
        )
        return

    path_a = Path(paths[0]).expanduser()
    path_b = Path(paths[1]).expanduser()

    if not path_a.exists() or not path_a.is_file():
        render_error(f"No existe o no es archivo: {path_a}")
        return
    if not path_b.exists() or not path_b.is_file():
        render_error(f"No existe o no es archivo: {path_b}")
        return

    if sub == "files":
        _compare_diff_files(path_a, path_b)
    elif sub == "json":
        _compare_json_files(path_a, path_b)
    else:  # text
        _compare_text_stats(path_a, path_b)


def _compare_recent_paths(session: SessionRuntime, count: int = 2) -> list[str]:
    """Return up to ``count`` distinct paths from the session's
    _file_edit_history, most-recent-first.

    Reads ``session._file_edit_history`` (populated by agent.py when
    file_write / file_edit tools succeed). Mirrors the dedup logic
    used by /recent so the same path counted multiple times only
    contributes one entry. Falls back to empty list when telemetry
    is not active.
    """
    history = getattr(session, "_file_edit_history", None)
    if history is None:
        return []
    seen: dict[str, None] = {}
    for entry in reversed(history):
        path = entry.get("path", "")
        if path and path not in seen:
            seen[path] = None
        if len(seen) >= count:
            break
    return list(seen.keys())

"""Visual upgrades for /metrics, /tokens and /usage slash commands.

Added in 2026-07-11 round N. Replicates the Rich Panel+Table.grid pattern
used by /whereami, /system_info and /cost. Migrated from legacy classes
MetricsCommand / TokensCommand / UsageCommand in commands.py to async
run_X_command functions so slash_router picks them up before the
CommandRegistry fallback.
"""


# ── Helpers ────────────────────────────────────────────────────────────














# ── /tokens ─────────────────────────────────────────────────────────────




# ── /metrics ────────────────────────────────────────────────────────────
















# ── /usage ──────────────────────────────────────────────────────────────




"""Visual upgrades for /search and /macro recorder status.

Added in 2026-07-11 (last visual-upgrade cycle). /search now renders a
Rich Panel with a Table.grid header (label / value) and a Rich Table for
the actual matches, mirroring the /tokens /metrics /whereami pattern.
/macro recorder adds a clear status indicator (recording / stopped) before
delegating to MacroCommand.
"""


HISTORY_ICON = b"\xe2\x8c\xab"  # \xe2\x8c\xab
FILE_ICON = b"\xf0\x9f\x93\x84"  # \xf0\x9f\x93\x84
FOLDER_ICON = b"\xf0\x9f\x93\x81"  # \xf0\x9f\x93\x81
SEARCH_ICON = b"\xe2\x8c\x95"  # \xe2\x8c\x95


ROLE_COLORS = {
    "user": "green",
    "assistant": "blue",
    "system": "yellow",
    "tool": "magenta",
    "function": "cyan",
    "error": "red",
}
def _render_search_panel(result, *, kind: str, **meta) -> None:
    """Render a search ToolResult as a Rich Panel with Table.grid + matches Table."""
    if not result.success:
        _print_tool_result(result)
        return

    data = result.data or {}
    matches = data.get("matches", []) if isinstance(data, dict) else []
    count = data.get("count", len(matches)) if isinstance(data, dict) else 0

    try:
        from rich.panel import Panel
        from rich.table import Table
    except Exception:
        _print_tool_result(result)
        return

    icons = {
        "history": ("history", "Historial"),
        "in_file": ("file", "En archivo"),
        "across_files": ("folder", "En archivos"),
    }
    icon_kind, kind_label = icons.get(kind, ("search", "Busqueda"))

    if icon_kind == "history":
        icon_str = HISTORY_ICON.decode("utf-8")
        title = "[bold realm]\u16ed Historial[/]"
    elif icon_kind == "file":
        icon_str = FILE_ICON.decode("utf-8")
        title = "[bold realm]\u16ed En archivo[/]"
    else:
        icon_str = FOLDER_ICON.decode("utf-8")
        title = "[bold realm]\u16ed En archivos[/]"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    if kind == "history":
        grid.add_row("Consulta", str(meta.get("query", "")))
    elif kind == "in_file":
        grid.add_row("Archivo", str(meta.get("path", "")))
        grid.add_row("Consulta", str(meta.get("query", "")))
    else:
        grid.add_row("Patron", str(meta.get("pattern", "")))
        grid.add_row("Directorio", str(meta.get("directory", ".")))

    count_color = "green" if count > 0 else "dim"
    grid.add_row("Coincidencias", f"[{count_color}]{count}[/{count_color}]")

    table = Table(show_header=True, header_style="bold cyan", expand=False)
    if kind == "history":
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Rol", style="bold", width=12)
        table.add_column("Contenido", style="white")
        for m in matches:
            role = m.get("role", "?")
            color = ROLE_COLORS.get(role, "white")
            table.add_row(str(m.get("index", "?")), f"[{color}]{role}[/{color}]", m.get("content", "") or "")
    elif kind == "in_file":
        table.add_column("Linea", justify="right", style="cyan", width=6)
        table.add_column("Texto", style="white")
        for m in matches:
            table.add_row(str(m.get("line_number", "?")), m.get("line_text", ""))
    else:
        table.add_column("Archivo", style="cyan")
        table.add_column("Linea", justify="right", style="bold", width=6)
        table.add_column("Texto", style="white")
        for m in matches:
            file_path = m.get("file", "")
            try:
                from pathlib import Path as _P
                fp = _P(file_path)
                short = f"{fp.parent.name}/{fp.name}" if fp.parent.name else fp.name
            except Exception:
                short = file_path
            table.add_row(short, str(m.get("line_number", "?")), m.get("line_text", ""))

    if matches:
        panel_content = Table.grid(padding=(0, 1))
        panel_content.add_column()
        panel_content.add_row(grid)
        panel_content.add_row(table)
        console.print(Panel(
            panel_content,
            title=title,
            subtitle=f"[dim]{icon_str} {kind_label} \u2014 {count} coincidencia(s)[/dim]",
            border_style="cyan",
            expand=False,
        ))
    else:
        console.print(Panel(
            grid,
            title=title,
            subtitle=f"[dim]{icon_str} {kind_label} \u2014 sin coincidencias[/dim]",
            border_style="cyan",
            expand=False,
        ))
    console.print()


# ── /snippet command ─────────────────────────────────────────────────


_SNIPPET_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _strip_surrounding_quotes(text: str) -> str:
    """Elimina comillas rectas o curvas que envuelvan el texto."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"', "\u2018", "\u2019", "\u201c", "\u201d"):
        return text[1:-1]
    return text


def _snippet_lexer_or_none(lang: str):
    """Devuelve el lexer de pygments para ``lang`` o ``None`` si no existe."""
    if not lang or not lang.strip():
        return None
    try:
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound
    except Exception:
        return None
    try:
        return get_lexer_by_name(lang)
    except ClassNotFound:
        return None


def _render_snippet_table(rows: list[tuple[str, str, str, int, str]]) -> None:
    """Imprime una tabla con las columnas NAME, LANG, TAGS, SIZE, CREATED."""
    from rich.table import Table

    table = Table(show_header=True, header_style="bold cyan", expand=False)
    table.add_column("NAME", style="bold cyan")
    table.add_column("LANG", style="yellow")
    table.add_column("TAGS", style="magenta")
    table.add_column("SIZE", justify="right", style="dim")
    table.add_column("CREATED", style="dim")
    for name, lang, tags, size, created in rows:
        table.add_row(name, lang or "text", tags or "-", str(size), created)
    console.print(table)


async def run_snippet_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /snippet para guardar, listar, recuperar y buscar fragmentos de codigo.

    Examples:
        /snippet
        /snippet list
        /snippet add <nombre> <lenguaje> <contenido...>
        /snippet add <nombre> -- <contenido...>
        /snippet get <nombre>
        /snippet delete <nombre>
        /snippet search <query>
        /snippet clear
        /snippet help
    """
    text = args.strip()

    # Listado: sin argumentos o list/ls
    if not text or text.lower() in ("list", "ls"):
        snippets = _load_snippets()
        if not snippets:
            console.print("[dim]No hay snippets guardados.[/]")
            return
        rows = []
        for name in sorted(snippets):
            entry = snippets[name]
            content = str(entry.get("content", ""))
            lang = str(entry.get("lang", "text"))
            tags = entry.get("tags") or []
            tags_str = ",".join(str(t) for t in tags) if tags else "-"
            created = str(entry.get("created", ""))
            rows.append((name, lang, tags_str, len(content), created))
        _render_snippet_table(rows)
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    # Ayuda
    if subcmd == "help":
        console.print(
            "[bold realm]\u16ed /snippet[/] \u2014 fragmentos de codigo guardados\n\n"
            "  [bold cyan]/snippet[/]                     Lista todos los snippets\n"
            "  [bold cyan]/snippet add <n> <lang> <c>[/]   Guarda un snippet (multilinea)\n"
            "  [bold cyan]/snippet add <n> -- <c>[/]       Guarda sin especificar lenguaje\n"
            "  [bold cyan]/snippet get <n>[/]              Muestra el contenido resaltado\n"
            "  [bold cyan]/snippet delete <n>[/]           Elimina un snippet\n"
            "  [bold cyan]/snippet search <q>[/]           Busca por nombre, contenido o tags\n"
            "  [bold cyan]/snippet clear[/]               Borra todos los snippets\n"
            "  [bold cyan]/snippet help[/]                Muestra esta ayuda"
        )
        return

    # Add
    if subcmd == "add":
        if not rest:
            render_error("Uso: /snippet add <nombre> <lenguaje> <contenido...>")
            return
        head, _, tail = rest.partition(" ")
        if not head:
            render_error("Uso: /snippet add <nombre> <lenguaje> <contenido...>")
            return
        name = head.strip()
        if not name or not _SNIPPET_NAME_RE.match(name):
            render_error(
                "Nombre de snippet invalido. Use solo letras, numeros, '.', '_' o '-'."
            )
            return
        body = tail
        if body.startswith("--"):
            lang = "text"
            content = body[2:].lstrip()
        else:
            lang_part, _, content = body.partition(" ")
            lang = (lang_part or "text").strip() or "text"
        content = _strip_surrounding_quotes(content)
        if not content:
            render_error("El contenido del snippet no puede estar vacio.")
            return
        snippets = _load_snippets()
        snippets[name] = {
            "content": content,
            "lang": lang,
            "tags": [],
            "created": datetime.now(UTC).isoformat(),
        }
        _save_snippets(snippets)
        console.print(
            f"[success]\u2713 Snippet guardado: {name} ({lang}, {len(content)} chars)[/]"
        )
        return

    # Get
    if subcmd == "get":
        if not rest:
            render_error("Uso: /snippet get <nombre>")
            return
        name = rest.strip()
        snippets = _load_snippets()
        if name not in snippets:
            render_error(f"Snippet no encontrado: {name}")
            return
        entry = snippets[name]
        content = str(entry.get("content", ""))
        lang = str(entry.get("lang", "text"))
        lexer = _snippet_lexer_or_none(lang)
        if lexer is not None:
            try:
                syntax = Syntax(content, lexer.name, theme="monokai", line_numbers=True)
                console.print(syntax)
                return
            except Exception:
                pass
        console.print(content)
        return

    # Delete / rm
    if subcmd in ("delete", "rm"):
        if not rest:
            render_error("Uso: /snippet delete <nombre>")
            return
        name = rest.strip()
        snippets = _load_snippets()
        if name not in snippets:
            render_error(f"Snippet no encontrado: {name}")
            return
        del snippets[name]
        _save_snippets(snippets)
        console.print(f"[success]\u2713 Snippet eliminado: {name}[/]")
        return

    # Search
    if subcmd == "search":
        if not rest:
            render_error("Uso: /snippet search <query>")
            return
        query = rest.strip().lower()
        snippets = _load_snippets()
        if not snippets:
            console.print("[dim]No hay snippets guardados.[/]")
            return
        matches: list[tuple[str, str, str, int, str]] = []
        for name, entry in snippets.items():
            content = str(entry.get("content", ""))
            tags = entry.get("tags") or []
            haystack_parts = [name.lower(), content.lower()] + [str(t).lower() for t in tags]
            if any(query in part for part in haystack_parts):
                lang = str(entry.get("lang", "text"))
                tags_str = ",".join(str(t) for t in tags) if tags else "-"
                created = str(entry.get("created", ""))
                matches.append((name, lang, tags_str, len(content), created))
        if not matches:
            console.print(f"[dim]Sin coincidencias para: {rest.strip()}[/]")
            return
        _render_snippet_table(sorted(matches))
        return

    # Clear
    if subcmd == "clear":
        snippets = _load_snippets()
        count = len(snippets)
        if count > 5:
            try:
                from rich.prompt import Prompt
                answer = Prompt.ask(
                    f"Hay {count} snippets. \u00bfBorrarlos todos?",
                    choices=["s", "n"],
                    default="n",
                )
            except Exception:
                answer = "n"
            if answer.lower() != "s":
                console.print("[dim]Operacion cancelada.[/]")
                return
        snippets.clear()
        _save_snippets(snippets)
        console.print("[success]\u2713 Todos los snippets eliminados.[/]")
        return

    render_error(
        "Subcomando desconocido. Use: list | add | get | delete | search | clear | help"
    )


# ── Snippet storage helpers ──────────────────────────────────────────


_SNIPPETS_PATH = CONFIG_DIR / "snippets.json"


def _load_snippets() -> dict[str, dict]:
    """Carga snippets desde ``CONFIG_DIR/snippets.json``.

    Tolera archivos corruptos devolviendo ``{}`` y registrando un aviso.
    """
    if not _SNIPPETS_PATH.exists():
        return {}
    try:
        data = json.loads(_SNIPPETS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("se esperaba un dict JSON")
    except Exception as exc:
        preserve_corrupt(_SNIPPETS_PATH, exc)
    return {}


def _save_snippets(snippets: dict[str, dict]) -> None:
    """Guarda snippets en ``CONFIG_DIR/snippets.json``."""
    _SNIPPETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SNIPPETS_PATH.write_text(
        json.dumps(snippets, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
