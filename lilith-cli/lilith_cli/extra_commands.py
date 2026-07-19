"""Comandos de barra adicionales para Lilith CLI.

Implementa /env, /git, /todos, /search, /bench, /template, /tour, /diff-staged y /tree como funciones simples que
se integran sin modificar commands.py.

Estas funciones deben registrarse en el REPL de lilith_cli.repl
antes de la logica de despacho normal del CommandRegistry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lilith_tools.coding_tools import RunLinterTool, RunTestTool
from lilith_tools.git_tools import GitOperationTool
from lilith_tools.registry import ToolRegistry
from lilith_tools.base import BaseTool, ToolResult
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

from .config import CONFIG_DIR
from .render import console, get_theme, render_error, set_theme
from rich.syntax import Syntax
from rich.tree import Tree as RichTree

if TYPE_CHECKING:
    from .agent import AgentSession



# -- Error UX helper ----------------------------------------------------


_ERROR_TIPS = {
    FileNotFoundError: "check the path exists and you have read permissions",
    PermissionError: "try a different file or check permissions",
    IsADirectoryError: "expected a file path, not a directory",
    NotADirectoryError: "expected a directory path, not a file",
    TimeoutError: "the operation took too long, try increasing the timeout",
    ConnectionError: "check your network connection and try again",
    ValueError: "verify the argument format and value range",
    KeyError: "check spelling or see the help text for valid options",
    subprocess.CalledProcessError: "the underlying command failed, check its output above",
    OSError: "check filesystem state and permissions",
}


def _print_error(context, err):
    """Print an error with an optional actionable tip based on the exception type.

    Args:
        context: Short description of what was being attempted.
        err: The exception (or string) that was raised.
    """
    console.print("[error]" + str(context) + ": " + str(err) + "[/error]")
    if isinstance(err, BaseException):
        for exc_type, tip in _ERROR_TIPS.items():
            if isinstance(err, exc_type):
                console.print("[dim]tip: " + tip + "[/dim]")
                break

EDITOR_CONFIG_FILE = CONFIG_DIR / "editor.json"
_FROZEN_EDITOR: str | None = None


def _get_editor() -> str | None:
    """Return the preferred editor command.

    Order of precedence:
    1. Runtime override set via /editor set.
    2. EDITOR environment variable.
    3. Fallback editors (vim, vi, nano, notepad).
    """
    if _FROZEN_EDITOR is not None:
        return _FROZEN_EDITOR
    if os.environ.get("EDITOR"):
        return os.environ.get("EDITOR")
    for candidate in ("vim", "vi", "nano", "notepad"):
        if shutil.which(candidate):
            return candidate
    return None


def _set_editor(command: str) -> None:
    """Persist the preferred editor to disk and update the in-memory value."""
    global _FROZEN_EDITOR
    _FROZEN_EDITOR = command
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    EDITOR_CONFIG_FILE.write_text(json.dumps({"command": command}, ensure_ascii=False), encoding="utf-8")


def _load_editor() -> None:
    """Load a previously persisted editor override from disk."""
    global _FROZEN_EDITOR
    if EDITOR_CONFIG_FILE.exists():
        try:
            data = json.loads(EDITOR_CONFIG_FILE.read_text(encoding="utf-8"))
            command = data.get("command", "")
            if command:
                _FROZEN_EDITOR = command
        except (json.JSONDecodeError, OSError):
            pass


# Load persisted editor override on module import.
_load_editor()


# ---------------------------------------------------------------------------
# Redaction helper for /redact: replace sensitive substrings with [REDACTED].
# Builtin patterns cover common credentials, contacts, and PII.
# ---------------------------------------------------------------------------
_REDACT_PATTERNS: dict[str, tuple[str, ...]] = {
    "api_key": (r"(?i)api[_-]?key\s*[=:]\s*\S+",),
    "password": (r"(?i)password\s*[=:]\s*\S+",),
    "secret": (r"(?i)secret\s*[=:]\s*\S+", r"(?i)token\s*[=:]\s*\S+"),
    "email": (r"[\w.+-]+@[\w-]+\.[\w.-]+",),
    "ssn": (r"\b\d{3}-\d{2}-\d{4}\b",),
    "credit_card": (r"\b\d{4}\s\d{4}\s\d{4}\s\d{4}\b",),
}


def _redact_text(text: str, patterns: list[str] | None = None) -> str:
    """Replace sensitive substrings in *text* with ``[REDACTED]``.

    When *patterns* is None (default), every builtin pattern is applied.
    When *patterns* is a list of names from :data:`_REDACT_PATTERNS`, only
    those patterns are applied. Unknown names are silently ignored.
    """
    import re

    if patterns is None:
        selected = _REDACT_PATTERNS.values()
    else:
        selected = (_REDACT_PATTERNS[name] for name in patterns if name in _REDACT_PATTERNS)

    redacted = text
    for pattern_tuple in selected:
        for pattern in pattern_tuple:
            redacted = re.sub(pattern, "[REDACTED]", redacted)
    return redacted



async def run_redact_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /redact para ocultar información sensible.

    Examples:
        /redact <archivo>
        /redact <archivo> --out <salida>
        /redact <archivo> --patterns api_key,password
    """
    import argparse as _argparse
    import shlex as _shlex

    text = args.strip()
    if not text:
        render_error("Uso: /redact <archivo> [--out <salida>] [--patterns <p1,p2,...>]")
        return

    parser = _argparse.ArgumentParser(prog="/redact", add_help=False)
    parser.add_argument("file", nargs="?")
    parser.add_argument("--out", dest="out")
    parser.add_argument("--patterns", dest="patterns", default="")
    try:
        parsed, _ = parser.parse_known_args(_shlex.split(text))
    except Exception as exc:
        render_error(f"Error parseando argumentos: {exc}")
        return

    if not parsed.file:
        render_error("Uso: /redact <archivo> [--out <salida>] [--patterns <p1,p2,...>]")
        return

    path = Path(parsed.file)
    if not path.exists():
        render_error(f"Archivo no encontrado: {path}")
        return

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        render_error(f"Error leyendo {path}: {exc}")
        return

    selected_patterns = [p.strip() for p in parsed.patterns.split(",") if p.strip()] or None
    redacted = _redact_text(content, selected_patterns)

    if parsed.out:
        try:
            out_path = Path(parsed.out)
            out_path.write_text(redacted, encoding="utf-8")
            console.print(f"[success]✓ Redactado guardado en {out_path}[/]")
        except Exception as exc:
            render_error(f"Error escribiendo {parsed.out}: {exc}")
        return

    console.print(redacted)


async def run_watch_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
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


async def run_macro_command(session: AgentSession, args: str) -> None:
    """Ejecuta /macro [record|stop|play|list|delete].

    Las macros se guardan en ~/.yggdrasil/macros.json como secuencias de
    comandos de barra. El REPL se encarga de almacenar los comandos mientras
    se graba; esta función simplemente delega a MacroCommand para las
    operaciones manuales de control/playback.
    """
    # Visual status indicator BEFORE delegating (record/stop show a clear
    # panel; play/list/delete are unchanged). The actual recording / playback
    # is still performed by MacroCommand.
    text = args.strip()
    if text:
        parts = text.split(maxsplit=1)
        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if subcmd == "record":
            _render_macro_status("record", rest.strip())
        elif subcmd == "stop":
            _render_macro_status("stop")

    from .commands import MacroCommand

    cmd = MacroCommand(session)
    await cmd.execute(args)


def _print_env_json(result) -> None:
    """Print EnvListTool result as JSON via sys.stdout (bypasses Rich markup)."""
    import json as _json
    import sys as _sys

    if hasattr(result, "data") and result.data is not None:
        payload = result.data
    elif hasattr(result, "output"):
        payload = {"output": result.output}
    else:
        payload = result
    _sys.stdout.write(_json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    _sys.stdout.flush()


async def run_env_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /env [name|info|prefix <PREFIX>|unset <name>|snapshot|diff] [--json].

    Examples:
        /env PATH
        /env info
        /env prefix PYTHON
        /env unset FOO
        /env snapshot                       — capture current env to disk
        /env diff                           — show changes since snapshot
        /env --json                         — all env vars as JSON
        /env prefix PYTHON --json           — filtered JSON output
    """

    text = args.strip()

    # /env --json alone means list all as JSON
    if text.lower() == "--json":
        tool = EnvListTool()
        result = tool.execute()
        _print_env_json(result)
        return

    if not text or text.lower() in ("list", "ls", "all"):
        tool = EnvListTool()
        result = tool.execute()
        if "--json" in text.lower().split():
            _print_env_json(result)
            return
        _print_env_list(result)
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "info":
        tool = SysInfoTool()
        result = tool.execute()
        _print_sys_info(result)
        return

    if subcmd == "prefix":
        if not rest:
            render_error("Uso: /env prefix <PREFIX>")
            return
        tool = EnvListTool()
        # Check for --json after prefix value: /env prefix X --json
        json_mode = False
        prefix_value = rest
        if " --json" in rest:
            parts = rest.split(" --json")
            prefix_value = parts[0].strip()
            json_mode = True
        result = tool.execute(prefix=prefix_value)
        if json_mode:
            _print_env_json(result)
        else:
            _print_env_list(result)
        return

    if subcmd == "unset":
        name = rest.strip()
        if not name:
            render_error("Uso: /env unset <name>")
            return
        console.print(f"[warning]⚠ Simulación: eliminaría {name}={os.environ.get(name, '')!r}[/]")
        console.print("[dim]No se realiza ningún cambio por seguridad.[/]")
        return

    if subcmd == "snapshot":
        _env_snapshot_save()
        return

    if subcmd == "diff":
        await _env_diff_snapshot(_print_env_diff)
        return

    # /env <name> (single env var)
    tool = EnvGetTool()
    result = tool.execute(name=text)
    if not result.success:
        render_error(result.error or f"No se pudo leer {text}")
        return
    _print_env_get(result)


# Path to the persisted env snapshot used by /env snapshot + /env diff.
_ENV_SNAPSHOT_PATH = CONFIG_DIR / "env_snapshot.json"


def _env_snapshot_save() -> None:
    """/env snapshot — capture the current process env to
    ~/.yggdrasil/env_snapshot.json. Used as the baseline for /env diff.

    Only the keys that already have values are written (no empties);
    the file is JSON-encoded for readability and round-trip parity.
    """
    import json as _json

    payload = {k: v for k, v in os.environ.items()}
    try:
        _ENV_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENV_SNAPSHOT_PATH.write_text(
            _json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(
            f"[success]✓ Snapshot guardado:[/] {len(payload)} variable(s) en "
            f"[tool.result]{_ENV_SNAPSHOT_PATH}[/]"
        )
    except Exception as exc:
        render_error(f"No pude guardar el snapshot: {exc}")


async def _env_diff_snapshot(renderer) -> None:
    """/env diff — show what's changed in os.environ since the last snapshot.

    Compares the live process env against the file written by
    /env snapshot. Reports three buckets:

    - Added: variables that exist now but weren't in the snapshot.
    - Removed: variables that were in the snapshot but no longer set.
    - Changed: variables whose value differs from the snapshot.

    Returns the diff to the renderer (which formats as a Rich Table).
    If no snapshot exists yet, instructs the user to run
    /env snapshot first.
    """
    import json as _json

    if not _ENV_SNAPSHOT_PATH.exists():
        render_error(
            f"No hay snapshot previo. Ejecutá /env snapshot primero "
            f"(guardaría {_ENV_SNAPSHOT_PATH})."
        )
        return

    try:
        snapshot = _json.loads(_ENV_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot no es un dict")
    except Exception as exc:
        render_error(
            f"Snapshot corrupto en {_ENV_SNAPSHOT_PATH}: {exc}. "
            f"Borrá el archivo o ejecutá /env snapshot de nuevo."
        )
        return

    live = dict(os.environ)
    snapshot_keys = set(snapshot.keys())
    live_keys = set(live.keys())

    added = sorted(live_keys - snapshot_keys)
    removed = sorted(snapshot_keys - live_keys)
    common = snapshot_keys & live_keys
    changed = sorted(k for k in common if snapshot[k] != live[k])

    renderer(added=added, removed=removed, changed=changed, snapshot=snapshot, live=live)


def _print_env_diff(*, added: list, removed: list, changed: list, snapshot: dict, live: dict) -> None:
    """Render the /env diff output as three grouped Rich tables."""
    from rich.table import Table

    if not (added or removed or changed):
        console.print("[dim]Sin cambios respecto al snapshot.[/]")
        return

    def _truncate(s: str, n: int = 80) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"

    if added:
        t = Table(
            title=f"[bold green]+ Añadidas ({len(added)})[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="green",
            expand=False,
        )
        t.add_column("Variable", style="tool.name")
        t.add_column("Valor actual", style="green")
        for k in added:
            t.add_row(k, _truncate(live[k]))
        console.print(t)
        console.print()

    if removed:
        t = Table(
            title=f"[bold red]- Eliminadas ({len(removed)})[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="red",
            expand=False,
        )
        t.add_column("Variable", style="tool.name")
        t.add_column("Valor anterior", style="red")
        for k in removed:
            t.add_row(k, _truncate(snapshot[k]))
        console.print(t)
        console.print()

    if changed:
        t = Table(
            title=f"[bold yellow]~ Cambiadas ({len(changed)})[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="yellow",
            expand=False,
        )
        t.add_column("Variable", style="tool.name")
        t.add_column("Anterior", style="red")
        t.add_column("Actual", style="green")
        for k in changed:
            t.add_row(k, _truncate(snapshot[k]), _truncate(live[k]))
        console.print(t)
        console.print()


def _print_env_get(result) -> None:
    """Renderiza el resultado de env_get."""
    if isinstance(result.data, dict):
        for name, value in result.data.items():
            console.print(f"[tool.name]{name}[/]=[tool.result]{value}[/]")
    else:
        console.print(str(result.data))


def _print_env_list(result) -> None:
    """Renderiza el resultado de env_list."""
    if not result.success:
        render_error(result.error or "Error listando variables de entorno")
        return

    data = result.data or {}
    variables = data.get("variables", {})
    total = data.get("total", len(variables))
    returned = data.get("returned", len(variables))
    prefix = data.get("prefix", "")
    limit = data.get("limit", 50)

    if not variables:
        console.print("[dim]No hay variables de entorno" + (f" con prefijo '{prefix}'" if prefix else "") + ".[/]")
        return

    console.print(f"\n[bold realm]᛭ Variables de entorno[/]" + (f" — prefijo '{prefix}'" if prefix else ""))
    for name, value in sorted(variables.items()):
        console.print(f"  [tool.name]{name}[/]=[tool.result]{value!r}[/]")
    if total > returned:
        console.print(f"[dim](mostrando {returned} de {total}; límite={limit})[/]")
    console.print()


def _print_sys_info(result) -> None:
    """Renderiza el resultado de sys_info."""
    if not result.success:
        render_error(result.error or "Error obteniendo información del sistema")
        return

    data = result.data or {}
    disk = data.get("disk", {})

    from rich.table import Table

    table = Table(
        title="[bold realm]᛭ Información del sistema[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Propiedad", style="tool.name")
    table.add_column("Valor", style="tool.result")

    table.add_row("Python", f"{data.get('python_version', '?')} ({data.get('python_implementation', '?')})")
    table.add_row("Sistema operativo", str(data.get("os", "?")))
    table.add_row("Versión OS", str(data.get("os_version", "?")))
    table.add_row("Máquina", str(data.get("machine", "?")))
    table.add_row("Procesador", str(data.get("processor", "?")))
    table.add_row("Plataforma", str(data.get("platform", "?")))
    table.add_row("Nodo", str(data.get("node", "?")))

    if isinstance(disk, dict) and "error" not in disk:
        table.add_row(
            "Disco (libre / total)",
            f"{disk.get('free_gb', '?')} GB / {disk.get('total_gb', '?')} GB",
        )
    elif isinstance(disk, dict):
        table.add_row("Disco", f"[error]Error: {disk.get('error')}[/]")

    console.print(table)


async def run_git_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /git <subcomando> [args] usando GitOperationTool.

    Examples:
        /git status
        /git log --oneline -5
    """
    text = args.strip()
    if not text:
        render_error("Uso: /git <subcomando> [args] — por ejemplo /git status")
        return

    parts = text.split(maxsplit=1)
    op = parts[0]
    git_args = parts[1] if len(parts) > 1 else ""

    tool = GitOperationTool()
    result = tool.execute(op=op, args=git_args)
    _print_tool_result(result)


async def run_diff_staged_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /diff-staged para mostrar cambios preparados en git.

    Examples:
        /diff-staged              — patch completo
        /diff-staged stats        — tabla con archivos + +/- counts
        /diff-staged <archivo>    — diff del archivo preparado
    """
    text = args.strip()

    # `--numstat` is the machine-friendly form of `--stat`: one row per
    # file with added/removed counts (or '-' for binary). We use it for
    # both the default and explicit `stats` rendering — then parse it
    # into a Rich Table so the user sees aligned columns instead of the
    # raw `git diff --stat` ASCII output.
    use_stats = text.lower() == "stats"
    if use_stats:
        cmd = ["git", "diff", "--cached", "--numstat"]
    elif text:
        cmd = ["git", "diff", "--cached", "--", text]
    else:
        # Default still runs full diff (preserves the historical behavior);
        # `stats` is the explicit fast path.
        cmd = ["git", "diff", "--cached"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        render_error(f"Error ejecutando git diff --cached: {exc}")
        return

    if result.returncode != 0:
        error = result.stderr.strip() or "Error desconocido ejecutando git diff --cached"
        render_error(error)
        return

    output = result.stdout.strip()
    if not output:
        console.print("[dim]No hay cambios preparados.[/]")
        return

    console.print(
        "\n[bold realm]᛭ Cambios preparadas[/]"
        + (f" — {text}" if text and not use_stats else "")
    )

    if use_stats:
        _render_diff_staged_stats(output)
    else:
        console.print(output, markup=False, highlight=False)
    console.print()


def _render_diff_staged_stats(numstat_output: str) -> None:
    """Render the `--numstat` output as a Rich table with file, +, -.

    `git diff --cached --numstat` returns rows like ``12  3 src/foo.py``
    or ``-\t-\timg.png`` for binary files. We split on tabs, accumulate
    totals, and render a table that survives wrapping in an 80-column
    terminal better than the raw git output.
    """
    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Archivo", style="tool.name")
    table.add_column("+", justify="right", style="green", width=6)
    table.add_column("-", justify="right", style="red", width=6)

    total_add = 0
    total_del = 0
    rows = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, removed_raw, path = parts[0], parts[1], "\t".join(parts[2:])
        # Binary files report '-' for both counts.
        if added_raw == "-" and removed_raw == "-":
            added = "-"
            removed = "-"
        else:
            try:
                added = int(added_raw)
                removed = int(removed_raw)
                total_add += added
                total_del += removed
            except ValueError:
                added = added_raw
                removed = removed_raw
        rows += 1
        table.add_row(path, str(added), str(removed))

    console.print(table)
    if rows:
        console.print(
            f"[dim]{rows} archivo(s) preparado(s) · "
            f"+{total_add} -{total_del} líneas[/]"
        )


async def run_diff_unstaged_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Show unstaged working-tree changes (/diff-unstaged).

    The mirror of /diff-staged but for changes that are NOT yet
    `git add`-ed. Useful when the user has been editing files and wants
    to see what they have pending without confusing it with what's
    already staged for the next commit.

    Examples:
        /diff-unstaged              — full patch
        /diff-unstaged stats        — table with archivos + +/- counts
        /diff-unstaged <archivo>    — diff del archivo no-staged
    """
    text = args.strip()
    use_stats = text.lower() == "stats"

    if use_stats:
        cmd = ["git", "diff", "--numstat"]
    elif text:
        cmd = ["git", "diff", "--", text]
    else:
        cmd = ["git", "diff"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        render_error(f"Error ejecutando git diff: {exc}")
        return

    if result.returncode != 0:
        error = result.stderr.strip() or "Error desconocido ejecutando git diff"
        render_error(error)
        return

    output = result.stdout.strip()
    if not output:
        console.print("[dim]No hay cambios sin preparar.[/]")
        return

    console.print(
        "\n[bold realm]᛭ Cambios sin preparar[/]"
        + (f" — {text}" if text and not use_stats else "")
    )

    if use_stats:
        _render_diff_staged_stats(output)  # same renderer; format is identical
    else:
        console.print(output, markup=False, highlight=False)
    console.print()


async def run_todos_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /todos [add|done|remove|list|clear] usando las herramientas de todo.

    Examples:
        /todos
        /todos add comprar leche
        /todos done 1
        /todos remove 2
    """
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        tool = TodoListTool()
        result = tool.execute()
        if not result.success:
            render_error(result.error or "No se pudo listar las tareas")
            return
        todos = result.data if isinstance(result.data, list) else []
        _render_todos_table(todos)
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
        tool = TodoAddTool()
        result = tool.execute(text=rest)
    elif subcmd in ("done", "complete"):
        try:
            index = int(rest)
        except ValueError:
            render_error("Uso: /todos done <número>")
            return
        tool = TodoDoneTool()
        result = tool.execute(index=index)
    elif subcmd in ("remove", "rm", "delete"):
        try:
            index = int(rest)
        except ValueError:
            render_error("Uso: /todos remove <número>")
            return
        tool = TodoRemoveTool()
        result = tool.execute(index=index)
    else:
        render_error(f"Subcomando de /todos desconocido: {subcmd}")
        return

    _print_tool_result(result)


async def run_search_command(session: AgentSession, args: str) -> None:
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


async def run_bench_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /bench para medir latencias del proveedor actual.

    Examples:
        /bench
        /bench --turns 3
        /bench --provider openai --model gpt-4o
    """
    import argparse

    parser = argparse.ArgumentParser(prog="/bench", add_help=False)
    parser.add_argument("--turns", type=int, default=1)
    parser.add_argument("--provider", default=session.config.provider)
    parser.add_argument("--model", default=session.config.model)
    parser.add_argument("--prompt", default="Explain quantum computing in one sentence.")
    try:
        parsed = parser.parse_args(shlex.split(args) if args.strip() else [])
    except SystemExit:
        return

    from .providers import create_provider

    cfg = session.config
    bench_config = cfg
    if parsed.provider != cfg.provider or parsed.model != cfg.model:
        # clone config and override provider/model for the benchmark run
        bench_config = cfg.model_copy() if hasattr(cfg, "model_copy") else cfg
        bench_config.provider = parsed.provider
        bench_config.model = parsed.model

    provider = create_provider(bench_config)
    latencies: list[float] = []
    ttft_values: list[float] = []
    total_tokens = 0

    console.print(f"\n[bold realm]᛭ Benchmark[/] {parsed.model} @ {parsed.provider} ({parsed.turns} turnos)")
    for i in range(1, parsed.turns + 1):
        timer_start = time.perf_counter()
        first_token_time: float | None = None
        token_count = 0
        try:
            async for event in provider.stream(
                [{"role": "user", "content": parsed.prompt}],
                model=parsed.model,
            ):
                if event.get("content"):
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    token_count += len(event.get("content", "").split())
                if event.get("finish_reason"):
                    break
        except Exception as exc:
            render_error(f"Benchmark falló en turno {i}: {exc}")
            return
        elapsed = time.perf_counter() - timer_start
        latencies.append(elapsed)
        ttft = (first_token_time - timer_start) if first_token_time else None
        if ttft is not None:
            ttft_values.append(ttft)
        total_tokens += token_count
        console.print(f"  Turno {i}: {elapsed:.3f}s" + (f" (TTFT {ttft:.3f}s)" if ttft else ""))

    await provider.close()

    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    avg_ttft = sum(ttft_values) / len(ttft_values) if ttft_values else None
    console.print(f"\n[bold]Resumen:[/]")
    console.print(f"  Latencia promedio: {avg_latency:.3f}s")
    if avg_ttft is not None:
        console.print(f"  TTFT promedio: {avg_ttft:.3f}s")
    if avg_latency > 0 and total_tokens > 0:
        console.print(f"  Tokens/segundo: {total_tokens / sum(latencies):.2f}")
    console.print()


async def run_lint_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /lint [path] para analizar el código con el linter integrado.

    Examples:
        /lint
        /lint src/foo.py
        /lint --tool <tool>
        /lint staged
    """
    import subprocess

    text = args.strip()
    parts = text.split(maxsplit=1)

    if parts and parts[0].lower() == "staged":
        try:
            staged_proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            render_error("git no está disponible en PATH")
            return
        if staged_proc.returncode != 0:
            render_error(f"No se pudo leer archivos staged: {staged_proc.stderr.strip()}")
            return
        staged_files = [f for f in staged_proc.stdout.splitlines() if f.strip()]
        if not staged_files:
            console.print("[dim]No hay archivos staged para lintar.[/dim]")
            return
        target = " ".join(staged_files)
    elif parts and parts[0] == "--tool":
        tool_name = parts[1] if len(parts) > 1 else "ruff"
        target = "."
        linter = tool_name
        result = RunLinterTool().execute(path=target, linter=linter)
        console.print("[bold realm]\u16ed Lint:[/] [dim]" + str(result.data.get("command", linter) if result.success else linter) + "[/dim]")
        _print_tool_result(result)
        return
    else:
        target = text or "."

    result = RunLinterTool().execute(path=target)
    command_str = (result.data or {}).get("command", "") if result.success else ""
    header = "[bold realm]\u16ed Lint:[/] [dim]" + command_str + "[/dim]"
    console.print(header)
    _print_tool_result(result)


async def run_review_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /review para revisar el diff de un PR o rama.

    Examples:
        /review
        /review --files
        /review --staged
    """
    try:
        from lilith_tools.git_tools import ReviewPullRequestTool as _ReviewPT
        result = _ReviewPT().execute(args=args)
    except ImportError:
        from lilith_tools.git_tools import GitOperationTool
        # Map the documented /review flags onto allowed git operations.
        sub = args.strip().lstrip("-") or "diff"
        op, op_args = {
            "diff": ("diff", ""),
            "staged": ("diff", "--cached"),
            "files": ("diff", "--name-only"),
        }.get(sub, (sub, ""))
        result = GitOperationTool().execute(op=op, args=op_args)
    _print_tool_result(result)


async def run_template_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /template para listar y aplicar plantillas de prompts.

    Examples:
        /template
        /template list
        /template apply <nombre>
    """
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        templates = _list_templates()
        if not templates:
            console.print("[dim]No hay plantillas definidas.[/]")
            return
        console.print("\n[bold realm]᛭ Plantillas disponibles[/]")
        for name in templates:
            console.print(f"  [bold cyan]{name}[/]")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("apply", "use"):
        if not rest:
            render_error("Uso: /template apply <nombre>")
            return
        template = _get_template(rest)
        if template is None:
            render_error(f"Plantilla no encontrada: {rest}")
            return
        console.print(f"[success]✓ Plantilla aplicada: {rest}[/]")
        console.print(f"[dim]{template}[/]")
        return

    if subcmd in ("show", "view"):
        if not rest:
            render_error("Uso: /template show <nombre>")
            return
        template = _get_template(rest)
        if template is None:
            render_error(f"Plantilla no encontrada: {rest}")
            return
        console.print(f"\n[bold realm]᛭ Plantilla {rest}[/]")
        console.print(template)
        console.print()
        return

    render_error("Uso: /template [list|apply <nombre>|show <nombre>]")

def _compact_messages(messages: list) -> str:
    """Compact a list of messages into a brief summary string.

    Naive implementation: concatenate first 80 chars of each message content.
    Used by /compact to produce a placeholder summary that can be replaced by
    an LLM-generated one in the future.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?") if isinstance(msg, dict) else "?"
        content = str(msg.get("content", "")) if isinstance(msg, dict) else str(msg)
        snippet = content[:80].replace("\n", " ").strip()
        parts.append(f"{role}: {snippet}")
    summary = " | ".join(parts)
    if len(summary) > 500:
        summary = summary[:500] + "..."
    return summary or "(empty)"



# ── Template storage helpers ──────────────────────────────────────────

_TEMPLATES_DIR = CONFIG_DIR / "templates"


def _list_templates() -> list[str]:
    """Lista nombres de plantillas guardadas."""
    if not _TEMPLATES_DIR.exists():
        return []
    return sorted(p.stem for p in _TEMPLATES_DIR.glob("*.txt"))


def _get_template(name: str) -> str | None:
    """Carga una plantilla por nombre."""
    path = _TEMPLATES_DIR / f"{name}.txt"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


async def run_compact_command(session: AgentSession, args: str) -> None:
    """Compact history (/compact [n] [--dry-run] [--force] [--keep-last N])."""
    tokens = args.split()
    dry_run = "--dry-run" in tokens
    force = "--force" in tokens or "-f" in tokens
    keep_last = 0
    keep_last_set = False
    remaining: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--keep-last" and i + 1 < len(tokens):
            try:
                keep_last = int(tokens[i + 1])
                keep_last_set = True
                i += 2
                continue
            except ValueError:
                render_error("--keep-last requires an integer N")
                return
        if tok.startswith("--keep-last="):
            try:
                keep_last = int(tok.split("=", 1)[1])
                keep_last_set = True
                i += 1
                continue
            except ValueError:
                render_error("--keep-last requires an integer N")
                return
        if tok in ("--dry-run", "--force", "-f"):
            i += 1
            continue
        remaining.append(tok)
        i += 1
    text = " ".join(remaining)

    if not text:
        n = 0
    else:
        try:
            n = int(text)
        except ValueError:
            render_error("Uso: /compact [n] [--dry-run] [--force] [--keep-last N]")
            return

    if not session.history:
        console.print("[dim]No hay historial para compactar.[/dim]")
        return

    total = len(session.history)

    # Resolve n and keep_last consistently:
    # - If keep_last is set but n is not (n==0), compact everything except last N
    # - Otherwise n is the explicit count to compact, keep_last clamps to remaining
    if keep_last_set and n <= 0:
        n = max(0, total - keep_last)
    elif n <= 0:
        n = total

    # Clamp keep_last to total - n (cannot keep more than what remains after compaction)
    max_keep = max(0, total - n)
    if keep_last > max_keep:
        keep_last = max_keep

    to_summarize_count = n
    to_summarize = session.history[:to_summarize_count]
    keep = session.history[total - keep_last:] if keep_last > 0 else []

    if not to_summarize:
        console.print("[dim]Nada que compactar.[/dim]")
        return

    summary = _compact_messages(to_summarize)

    if dry_run:
        console.print(f"[info]Dry-run:[/info] se compactarían {to_summarize_count} mensajes en un resumen de ~{len(summary)} chars.")
        if keep_last > 0:
            console.print(f"[dim]Se conservarán los últimos {keep_last} mensajes sin resumir.[/dim]")
        console.print(f"[dim]Resumen tentativo:[/dim] {summary[:200]}{'...' if len(summary) > 200 else ''}")
        console.print("[dim]Pasá sin --dry-run para aplicar.[/dim]")
        return

    if not force and to_summarize_count >= total // 2:
        console.print(f"[warn]Vas a compactar {to_summarize_count} de {total} mensajes ({100 * to_summarize_count // total}% del historial).[/warn]")
        console.print("[dim]Pasá --force para confirmar o usá un número menor.[/dim]")
        return

    # Apply: replace to_summarize with summary, keep last N as-is
    session.history.clear()
    session.history.append({"role": "system", "content": f"Resumen de la conversación: {summary}"})
    session.history.extend(keep)
    if keep_last > 0:
        console.print(f"[success]✓ {to_summarize_count} mensajes compactados + {keep_last} conservados (total: {len(session.history)}).[/success]")
    else:
        console.print(f"[success]✓ {to_summarize_count} mensajes compactados en un resumen.[/success]")

# ── Replay helpers ───────────────────────────────────────────────────

_REPLAY_DIR = CONFIG_DIR / "replays"


async def run_replay_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /replay para repetir una secuencia de comandos guardada.

    Examples:
        /replay
        /replay <id>
        /replay save <nombre>
    """
    _REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        replays = sorted(_REPLAY_DIR.glob("*.json"))
        if not replays:
            console.print("[dim]No hay replays guardados.[/]")
            return
        console.print("\n[bold realm]᛭ Replays guardados[/]")
        for r in replays:
            console.print(f"  [bold cyan]{r.stem}[/]")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("save", "store"):
        if not rest:
            render_error("Uso: /replay save <nombre>")
            return
        if not session.history:
            render_error("No hay historial para guardar como replay.")
            return
        path = _REPLAY_DIR / f"{rest}.json"
        path.write_text(json.dumps(session.history, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[success]✓ Replay guardado: {rest}[/]")
        return

    if subcmd in ("load", "play"):
        name = rest if rest else subcmd
        path = _REPLAY_DIR / f"{name}.json"
        if not path.exists():
            render_error(f"Replay no encontrado: {name}")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            render_error(f"Error cargando replay: {exc}")
            return
        if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
            render_error(f"Formato de replay inválido: {name}")
            return
        session.history = data
        console.print(f"[success]✓ Replay cargado: {name} ({len(data)} mensajes)[/]")
        return

    # /replay <id>
    path = _REPLAY_DIR / f"{text}.json"
    if not path.exists():
        render_error(f"Replay no encontrado: {text}")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        render_error(f"Error cargando replay: {exc}")
        return
    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
        render_error(f"Formato de replay inválido: {text}")
        return
    session.history = data
    console.print(f"[success]✓ Replay cargado: {text} ({len(data)} mensajes)[/]")


# ── Changelog helpers ───────────────────────────────────────────────

CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
_CHANGELOG_PATH = CHANGELOG_PATH  # back-compat alias


def _parse_changelog_entries(text: str) -> list[dict]:
    """Parse Keep-a-Changelog markdown into list of {version, lines} entries."""
    import re as _re
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = _re.match(r"^##\s*\[([^\]]+)\](?:\s*-\s*\d{4}-\d{2}-\d{2})?", line)
        if m:
            if current is not None:
                entries.append(current)
            current = {"version": m.group(1).strip(), "lines": []}
        elif current is not None and line.strip():
            current["lines"].append(line)
    if current is not None:
        entries.append(current)
    return entries


async def run_changelog_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Show changelog history (/changelog [version|--list])."""
    text = args.strip()

    if not CHANGELOG_PATH.exists():
        render_error("No se encontró CHANGELOG.md")
        return

    try:
        content = CHANGELOG_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        render_error(f"Error leyendo changelog: {exc}")
        return

    entries = _parse_changelog_entries(content)

    if text.lower() in ("--list", "list"):
        if not entries:
            console.print("[dim]No hay entradas en el changelog.[/dim]")
            return
        console.print("[info]Versiones disponibles:[/info]")
        for entry in entries:
            console.print(f"  [bold cyan]v{entry['version']}[/bold cyan]")
        console.print()
        return

    if text:
        target = text
        match = next((e for e in entries if e["version"] == target), None)
        if not match:
            # Show available versions to help the user
            available = [entry["version"] for entry in entries]
            avail_preview = ", ".join(available[:5]) + ("..." if len(available) > 5 else "")
            render_error(f"No se encontró la versión {target}. Disponibles: {avail_preview}")
            return
        console.print(f"\n[bold realm]᛭ v{match['version']}[/]")
        for line in match["lines"]:
            console.print(line)
        console.print()
        return

    console.print(f"\n[bold realm]᛭ Changelog[/]")
    for entry in entries:
        console.print(f"\n[bold cyan]v{entry['version']}[/]")
        for line in entry["lines"]:
            console.print(line)
    console.print()

# ── Secret / env helpers ────────────────────────────────────────────

_SECRET_KEY = ""


async def run_secret_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /secret para gestionar variables secretas de la sesión.

    Examples:
        /secret
        /secret set <nombre> <valor>
        /secret get <nombre>
        /secret list
        /secret clear
    """
    global _SECRET_KEY  # noqa: PLW0603

    text = args.strip()
    secrets: dict[str, str] = getattr(session, "_secrets", None)
    if secrets is None:
        secrets = {}
        session._secrets = secrets

    if not text or text.lower() in ("list", "ls"):
        if not secrets:
            console.print("[dim]No hay secretos configurados.[/]")
            return
        console.print("\n[bold realm]᛭ Secretos configurados[/]")
        for name in sorted(secrets.keys()):
            console.print(f"  [bold cyan]{name}[/]: [dim]••••••••[/]")
        console.print()
        return

    parts = text.split(maxsplit=2)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("set", "add"):
        if len(parts) < 3:
            render_error("Uso: /secret set <nombre> <valor>")
            return
        name, value = parts[1], parts[2]
        secrets[name] = value
        console.print(f"[success]✓ Secreto configurado: {name}[/]")
        return

    if subcmd in ("get", "show"):
        if not rest:
            render_error("Uso: /secret get <nombre>")
            return
        value = secrets.get(rest)
        if value is None:
            render_error(f"Secreto no encontrado: {rest}")
            return
        console.print(f"[tool.name]{rest}[/]=[tool.result]{value}[/]")
        return

    if subcmd in ("clear", "reset"):
        secrets.clear()
        console.print("[success]✓ Secretos eliminados.[/]")
        return

    render_error("Uso: /secret [set <nombre> <valor>|get <nombre>|list|clear]")


# ── Tip helpers ──────────────────────────────────────────────────────

_DEFAULT_TIPS: list[str] = [
    "Usá /theme nord para cambiar al tema inspirado en el Ártico.",
    "Con /env prefix PYTHON listás todas las variables de entorno que empiezan con PYTHON.",
    "/compact resumí los últimos mensajes de la conversación para ahorrar contexto.",
    "Grabá macros con /macro record y ejecutalas con /macro play <nombre>.",
    "/status muestra el estado general de la sesión, incluyendo tokens y herramientas.",
    "Usá /search across para buscar patrones en todos los archivos del proyecto.",
    "Exportá la conversación con /export y cargala después con /load.",
    "El comando /watch vigila cambios de archivos y los reporta en tiempo real.",
    "Con /pin fijás mensajes importantes para que no se pierdan al compactar.",
    "/bench mide latencias del proveedor actual para comparar configuraciones.",
]
_TIPS_PATH = CONFIG_DIR / "tips.json"

# LILITH_TIPS starts as the bundled defaults; user-added tips are loaded
# from disk on first access via _ensure_tips_loaded() and persisted on
# /tip add so they survive across REPL restarts (previously they lived
# only in-process and leaked across sessions in the same run).
LILITH_TIPS: list[str] = list(_DEFAULT_TIPS)
_TIPS = LILITH_TIPS  # back-compat alias
_TIPS_LOADED = False


def _ensure_tips_loaded() -> None:
    """Lazy-load user tips from ~/.yggdrasil/tips.json on first access."""
    global _TIPS_LOADED
    if _TIPS_LOADED:
        return
    _TIPS_LOADED = True
    try:
        if _TIPS_PATH.exists():
            data = json.loads(_TIPS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Append user tips after the bundled defaults, skipping any
                # that exactly match a default (idempotent across sessions).
                for tip in data:
                    if isinstance(tip, str) and tip not in _DEFAULT_TIPS:
                        LILITH_TIPS.append(tip)
    except Exception as exc:
        console.print(f"[warning]tips.json ilegible ({exc}); usando defaults.[/]")


def _save_user_tips() -> None:
    """Persist only the user-added tips (not the bundled defaults)."""
    user_tips = [t for t in LILITH_TIPS if t not in _DEFAULT_TIPS]
    try:
        _TIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TIPS_PATH.write_text(
            json.dumps(user_tips, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(
            f"[warning]No pude persistir el consejo en {_TIPS_PATH.name}: {exc}[/]"
        )


async def run_tip_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Show, list, count, or add tips (/tip [n|list|count|add <texto>])."""
    _ensure_tips_loaded()
    raw = args.strip()
    text = raw.lower()

    if text in ("list", "ls"):
        console.print("\n[bold realm]᛭ Consejos disponibles[/]")
        for i, tip in enumerate(LILITH_TIPS, start=1):
            console.print(f"  [bold cyan]{i}.[/] {tip}")
        console.print()
        return

    if text == "count":
        console.print(f"[info]Hay[/info] [bold cyan]{len(LILITH_TIPS)}[/] [info]consejos en LILITH_TIPS.[/info]")
        return

    if text.startswith("add "):
        new_tip = raw[4:].strip()
        if not new_tip:
            render_error("Uso: /tip add <texto del consejo>")
            return
        LILITH_TIPS.append(new_tip)
        _save_user_tips()
        console.print(f"[success]✓ Consejo añadido (total: {len(LILITH_TIPS)})[/success]")
        return

    if text:
        try:
            index = int(text)
            if index < 1 or index > len(LILITH_TIPS):
                render_error(f"Índice fuera de rango: {index}")
                return
            tip = LILITH_TIPS[index - 1]
        except ValueError:
            render_error("Uso: /tip [número|list|count|add <texto>]")
            return
    else:
        tip = random.choice(LILITH_TIPS)

    console.print(f"\n[bold realm]᛭ Consejo[/]\n[tool.result]{tip}[/]\n")

# ── Alias helpers ────────────────────────────────────────────────────

_ALIAS_FILE = CONFIG_DIR / "aliases.json"


async def run_alias_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /alias para crear atajos de comandos de barra.

    Examples:
        /alias
        /alias set <nombre> <comando>
        /alias remove <nombre>
    """
    text = args.strip()

    aliases: dict[str, str] = _load_aliases()

    if not text or text.lower() in ("list", "ls"):
        if not aliases:
            console.print("[dim]No hay alias definidos.[/]")
            return
        console.print("\n[bold realm]᛭ Alias definidos[/]")
        for name, cmd in sorted(aliases.items()):
            console.print(f"  [bold cyan]/{name}[/] → {cmd}")
        console.print()
        return

    parts = text.split(maxsplit=2)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("set", "add"):
        if len(parts) < 3:
            render_error("Uso: /alias set <nombre> <comando>")
            return
        name, cmd = parts[1], parts[2]
        aliases[name] = cmd
        _save_aliases(aliases)
        console.print(f"[success]✓ Alias guardado: [bold cyan]/{name}[/] → {cmd}[/]")
        return

    if subcmd in ("get", "show"):
        if not rest:
            render_error("Uso: /alias get <nombre>")
            return
        cmd = aliases.get(rest)
        if cmd is None:
            render_error(f"Alias no encontrado: {rest}")
            return
        console.print(f"[bold cyan]/{rest}[/] \u2192 {cmd}")
        return

    if subcmd in ("remove", "rm", "delete"):
        if not rest:
            render_error("Uso: /alias remove <nombre>")
            return
        if rest not in aliases:
            render_error(f"Alias no encontrado: {rest}")
            return
        del aliases[rest]
        _save_aliases(aliases)
        console.print(f"[success]✓ Alias eliminado: {rest}[/]")
        return

    render_error("Uso: /alias [set|get|remove|list]")


def _load_aliases() -> dict[str, str]:
    """Carga alias desde ~/.yggdrasil/aliases.json."""
    if not _ALIAS_FILE.exists():
        return {}
    try:
        data = json.loads(_ALIAS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # pragma: no cover
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando alias: %s", exc)
    return {}


def _save_aliases(aliases: dict[str, str]) -> None:
    """Guarda alias en ~/.yggdrasil/aliases.json."""
    _ALIAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ALIAS_FILE.write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Stream helpers ──────────────────────────────────────────────────

_STREAM_CONFIG_FILE = CONFIG_DIR / "stream_config.json"


async def run_stream_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /stream para mostrar o cambiar opciones de streaming.

    Examples:
        /stream
        /stream on
        /stream off
    """
    text = args.strip()
    if not text or text.lower() in ("show", "status"):
        mode = _load_stream_config().get("enabled", True)
        console.print(f"[info]Streaming: [model]{'activado' if mode else 'desactivado'}[/]")
        return

    if text.lower() in ("on", "true", "1"):
        _save_stream_config({"enabled": True})
        console.print("[success]✓ Streaming activado.[/]")
    elif text.lower() in ("off", "false", "0"):
        _save_stream_config({"enabled": False})
        console.print("[success]✓ Streaming desactivado.[/]")
    else:
        render_error("Uso: /stream [on|off]")


def _load_stream_config() -> dict[str, Any]:
    """Carga configuración de streaming."""
    if not _STREAM_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(_STREAM_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # pragma: no cover
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando stream config: %s", exc)
    return {}


def _save_stream_config(config: dict[str, Any]) -> None:
    """Guarda configuración de streaming."""
    _STREAM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STREAM_CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── /auto command ────────────────────────────────────────────────────


async def run_auto_command(session: AgentSession, args: str) -> None:
    """Ejecuta /auto [on|off] para activar el modo auto (ejecución automática de herramientas).

    Examples:
        /auto
        /auto on
        /auto off
    """
    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        enabled = getattr(session, "auto_mode", False)
        console.print(f"[info]Modo auto: [model]{'activado' if enabled else 'desactivado'}[/]")
        return

    if text.lower() in ("on", "true", "1"):
        session.auto_mode = True
        console.print("[success]✓ Modo auto activado.[/]")
    elif text.lower() in ("off", "false", "0"):
        session.auto_mode = False
        console.print("[success]✓ Modo auto desactivado.[/]")
    else:
        render_error("Uso: /auto [on|off]")


# ── /file command ────────────────────────────────────────────────────


async def run_file_command(session: AgentSession, args: str) -> None:
    """Ejecuta /file para añadir el contenido de un archivo al contexto del usuario.

    Examples:
        /file src/main.py
        /file --list
    """
    text = args.strip()

    if not text or text.lower() in ("list", "ls", "--list"):
        files: list[str] = getattr(session, "_user_files", [])
        if not files:
            console.print("[dim]No hay archivos adjuntos en el contexto.[/]")
            return
        console.print("\n[bold realm]᛭ Archivos en el contexto[/]")
        for f in files:
            console.print(f"  [bold cyan]{f}[/]")
        console.print()
        return

    if text.lower() in ("clear", "reset"):
        session._user_files = []
        console.print("[success]✓ Archivos adjuntos eliminados.[/]")
        return

    path = Path(text)
    if not path.exists():
        render_error(f"Archivo no encontrado: {text}")
        return
    if not path.is_file():
        render_error(f"La ruta no es un archivo: {text}")
        return

    files = getattr(session, "_user_files", None)
    if files is None:
        session._user_files = []
        files = session._user_files

    files.append(text)
    console.print(f"[success]✓ Archivo añadido al contexto: {text}[/]")


# ── /export command ──────────────────────────────────────────────────


async def run_export_command(session: AgentSession, args: str) -> None:
    """Export conversation (/export [name] [--format json|md] [--output <path>])."""
    # Manual parser to preserve paths with backslashes/spaces
    # argparse + shlex destroys Windows paths, so we parse flags manually.
    name: str | None = None
    fmt = "json"
    output_path: str | None = None
    tokens = args.split()
    positional: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--format" and i + 1 < len(tokens):
            fmt = tokens[i + 1]
            i += 2
        elif tok.startswith("--format="):
            fmt = tok.split("=", 1)[1]
            i += 1
        elif tok == "--output" and i + 1 < len(tokens):
            output_path = tokens[i + 1]
            i += 2
        elif tok.startswith("--output="):
            output_path = tok.split("=", 1)[1]
            i += 1
        else:
            positional.append(tok)
            i += 1
    if positional:
        name = positional[0]

    if fmt not in ("json", "md"):
        render_error("Formato inv\u00e1lido. Use: json o md")
        return

    # Determine output path
    conversations_dir = CONFIG_DIR / "conversations"
    if output_path:
        filepath = Path(output_path).expanduser()
    else:
        conversations_dir.mkdir(parents=True, exist_ok=True)
        name = name or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        ext = "md" if fmt == "md" else "json"
        filepath = conversations_dir / f"{name}.{ext}"

    # Build content based on format
    if fmt == "md":
        lines_md = []
        lines_md.append(f"# Conversación exportada {datetime.now(UTC).isoformat()}")
        lines_md.append(f"\n**Model:** {session.config.model}  ")
        lines_md.append(f"**Provider:** {session.config.provider}\n")
        for msg in session.history:
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            lines_md.append(f"## {role}\n")
            lines_md.append(str(content))
            lines_md.append("")
        content_str = "\n".join(lines_md)
    else:
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "model": session.config.model,
            "provider": session.config.provider,
            "messages": session.history,
            "usage": session.total_usage,
        }
        content_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content_str, encoding="utf-8")
    except OSError as exc:
        render_error(f"No se pudo escribir {filepath}: {exc}")
        return

    console.print(f"[success]✓ Conversación exportada:[/success] [bold cyan]{filepath}[/bold cyan]  [dim]({fmt})[/dim]")


# ── /capture command ─────────────────────────────────────────────────


def _capture_usage() -> str:
    """Devuelve la línea de uso de /capture en español."""
    return "Uso: /capture [nombre] [--output <ruta>] [--include-tools] [--no-usage] [--tags <tags>] [--exclude-system] [--first N | --last N]"


def _capture_parse_args(args: str) -> tuple[str | None, str | None, bool, bool, list[str], bool, int | None, int | None] | None:
    """Parsea argumentos de /capture sin shlex para preservar rutas Windows.

    Returns (name, output_path, include_tools, include_usage, tags, exclude_system,
    first_n, last_n). ``first_n`` and ``last_n`` are mutually exclusive:
    ``--first N`` keeps the first N messages, ``--last N`` keeps the
    last N. ``None`` means "no limit".
    """
    output_path: str | None = None
    include_tools = False
    include_usage = True
    tags: list[str] = []
    exclude_system = False
    first_n: int | None = None
    last_n: int | None = None
    positional: list[str] = []
    tokens = (args or "").split()
    flags = {
        "--output", "--include-tools", "--no-usage", "--tags",
        "--exclude-system", "--first", "--last",
    }

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--include-tools":
            include_tools = True
            i += 1
        elif tok == "--no-usage":
            include_usage = False
            i += 1
        elif tok == "--exclude-system":
            exclude_system = True
            i += 1
        elif tok == "--first":
            i += 1
            if i >= len(tokens) or tokens[i] in flags:
                render_error(_capture_usage())
                return None
            try:
                first_n = int(tokens[i])
                if first_n < 1:
                    raise ValueError
            except ValueError:
                render_error(f"--first debe ser entero positivo, recibí: {tokens[i]!r}")
                return None
            i += 1
        elif tok == "--last":
            i += 1
            if i >= len(tokens) or tokens[i] in flags:
                render_error(_capture_usage())
                return None
            try:
                last_n = int(tokens[i])
                if last_n < 1:
                    raise ValueError
            except ValueError:
                render_error(f"--last debe ser entero positivo, recibí: {tokens[i]!r}")
                return None
            i += 1
        elif tok == "--tags":
            i += 1
            tag_parts: list[str] = []
            while i < len(tokens) and tokens[i] not in flags:
                tag_parts.append(tokens[i])
                i += 1
            flat: list[str] = []
            for p in tag_parts:
                flat.extend(p.split(","))
            tags = [t.lstrip("#").strip(",").strip() for t in flat]
            tags = [t for t in tags if t]
            if not tags:
                render_error(_capture_usage())
                return None
        elif tok.startswith("--tags="):
            value = tok.split("=", 1)[1]
            tags = [t.lstrip("#").strip(",").strip() for t in value.split(",")]
            tags = [t for t in tags if t]
            if not tags:
                render_error(_capture_usage())
                return None
            i += 1
        elif tok == "--output":
            i += 1
            path_parts: list[str] = []
            while i < len(tokens) and tokens[i] not in flags:
                path_parts.append(tokens[i])
                i += 1
            output_path = " ".join(path_parts).strip()
            if not output_path:
                render_error(_capture_usage())
                return None
        elif tok.startswith("--output="):
            output_path = tok.split("=", 1)[1].strip()
            if not output_path:
                render_error(_capture_usage())
                return None
            i += 1
        elif tok.startswith("--"):
            render_error(f"Opción desconocida: {tok}. {_capture_usage()}")
            return None
        else:
            positional.append(tok)
            i += 1

    name = " ".join(positional).strip() or None
    return name, output_path, include_tools, include_usage, tags, exclude_system, first_n, last_n


def _capture_usage_dict(session: AgentSession) -> dict[str, Any]:
    """Devuelve el uso total de la sesión como diccionario seguro."""
    usage = getattr(session, "total_usage", {}) or {}
    return dict(usage) if isinstance(usage, dict) else {"uso": usage}


def _capture_message_text(content: Any) -> str:
    """Convierte contenido de mensaje a texto legible para Markdown."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, (dict, list, tuple)):
        return json.dumps(content, ensure_ascii=False, indent=2, default=str)
    return str(content)


def _capture_role_heading(role: str) -> str:
    """Mapea roles internos a encabezados humanos en español."""
    role_key = role.lower()
    if role_key == "user":
        return "👤 Usuario"
    if role_key == "assistant":
        return "🤖 Lilith"
    if role_key == "tool":
        return "🔧 Herramienta"
    if role_key == "system":
        return "⚙️ Sistema"
    return f"📝 {role or 'Mensaje'}"


def _capture_tool_args_preview(entry: dict[str, Any]) -> str:
    """Devuelve una vista previa compacta de argumentos de herramienta."""
    raw_args = entry.get("arguments", entry.get("args", entry.get("input", "")))
    if isinstance(raw_args, str):
        preview = raw_args
    else:
        preview = json.dumps(raw_args, ensure_ascii=False, default=str)
    preview = " ".join(preview.split())
    return preview if len(preview) <= 80 else preview[:77] + "..."


async def run_capture_command(session: AgentSession, args: str) -> None:
    """Guarda una transcripción Markdown limpia de la sesión activa."""
    theme = get_theme()
    text = (args or "").strip()

    if text.lower() in ("help", "--help", "-h", "?"):
        console.print(
            f"\n[bold realm]{theme.prompt_prefix} /capture[/bold realm] "
            "[dim]— transcripción Markdown de la sesión[/dim]"
        )
        console.print(_capture_usage())
        console.print("  [cyan]/capture[/]                         [dim]# nombre automático[/dim]")
        console.print("  [cyan]/capture sesión[/]                  [dim]# ~/.yggdrasil/transcripts/sesión.md[/dim]")
        console.print("  [cyan]/capture --output <ruta>[/]         [dim]# ruta exacta[/dim]")
        console.print("  [cyan]/capture --include-tools[/]         [dim]# incluye herramientas[/dim]")
        console.print("  [cyan]/capture --no-usage[/]              [dim]# omite uso de tokens[/dim]")
        console.print("  [cyan]/capture --tags <tags>[/]          [dim]# ej. --tags work,urgent[/dim]")
        console.print("  [cyan]/capture --exclude-system[/]       [dim]# omite mensajes system/tool[/dim]")
        console.print("  [cyan]/capture --first N | --last N[/]   [dim]# limita a N mensajes[/dim]")
        return

    history = getattr(session, "history", None) or []
    if not history:
        render_error("No hay conversación para capturar todavía.")
        return

    parsed = _capture_parse_args(text)
    if parsed is None:
        return
    name, output_path, include_tools, include_usage, tags, exclude_system, first_n, last_n = parsed

    transcripts_dir = CONFIG_DIR / "transcripts"
    if output_path:
        filepath = Path(output_path).expanduser()
    else:
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        name = name or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filepath = transcripts_dir / f"{name}.md"

    timestamp = datetime.now(UTC).isoformat()
    usage = _capture_usage_dict(session)
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens) or 0

    lines: list[str] = [
        f"# Lilith transcript — {timestamp}",
        "",
        f"- **Modelo:** {getattr(session.config, 'model', '?')}",
        f"- **Proveedor:** {getattr(session.config, 'provider', '?')}",
        f"- **Mensajes:** {len(history)}",
        f"- **Tokens:** {total_tokens} (prompt {prompt_tokens} + completion {completion_tokens})",
    ]
    if tags:
        lines.append(f"- **Tags:** {', '.join('#' + t for t in tags)}")
    lines.extend(["", "---", ""])

    messages_to_render = history
    if exclude_system:
        messages_to_render = [
            m for m in history
            if (m.get("role") if isinstance(m, dict) else "") not in ("system", "tool")
        ]
    if first_n is not None:
        messages_to_render = messages_to_render[:first_n]
    elif last_n is not None:
        messages_to_render = messages_to_render[-last_n:]
    for msg in messages_to_render:
        if isinstance(msg, dict):
            role = str(msg.get("role", "Mensaje"))
            content = msg.get("content", "")
        else:
            role = "Mensaje"
            content = msg
        lines.append(f"## {_capture_role_heading(role)}")
        lines.append("")
        lines.append(_capture_message_text(content))
        lines.append("")

    if include_tools:
        lines.append("## 🔧 Herramientas llamadas")
        lines.append("")
        tool_history = getattr(session, "_tool_call_history", None) or []
        if tool_history:
            for entry in tool_history:
                name_tool = str(entry.get("name", "herramienta"))
                duration = entry.get("duration", 0) or 0
                try:
                    duration_text = f"{float(duration):.3f}"
                except (TypeError, ValueError):
                    duration_text = str(duration)
                preview = _capture_tool_args_preview(entry)
                lines.append(f"- **{name_tool}** — {duration_text}s — {preview}")
        else:
            lines.append("_No hubo herramientas llamadas._")
        lines.append("")

    if include_usage:
        lines.append("## 📊 Uso")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(usage, ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")

    content_str = "\n".join(lines).rstrip() + "\n"

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content_str, encoding="utf-8")
    except OSError as exc:
        render_error(f"No se pudo escribir {filepath}: {exc}")
        return

    console.print(
        f"[success]✓ Transcripción capturada:[/success] "
        f"[bold frost]{filepath}[/bold frost]  [dim]({theme.name})[/dim]"
    )

# ── /history command ─────────────────────────────────────────────────


async def run_history_command(session: AgentSession, args: str) -> None:
    """Ejecuta /history para mostrar los últimos mensajes de la conversación.

    Examples:
        /history
        /history 10
        /history --tool file_read
        /history 20 --tool file_read
    """
    # ── Parse args: optional <limit> and optional --tool <name> ──────────
    text = args.strip()
    limit: int | None = None
    tool_filter: str | None = None

    if text:
        tokens = text.split()
        remaining: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--tool" and i + 1 < len(tokens):
                tool_filter = tokens[i + 1]
                i += 2
            elif tok.startswith("--tool="):
                tool_filter = tok.split("=", 1)[1]
                i += 1
            else:
                remaining.append(tok)
                i += 1

        if remaining:
            try:
                limit = int(remaining[0])
                if limit < 1:
                    raise ValueError
            except ValueError:
                render_error("Uso: /history [número] [--tool <nombre>]")
                return

    history = session.history or []

    # ── --tool filter: pull matching tool calls from session._tool_call_history ──
    if tool_filter:
        tool_history: list[dict[str, Any]] = (
            getattr(session, "_tool_call_history", []) or []
        )
        matching = [h for h in tool_history if h.get("name") == tool_filter]
        if not matching:
            console.print(
                f"[dim]No hay llamadas registradas para ‘{tool_filter}’.[/dim]"
            )
            return
        if limit is None:
            limit = len(matching)
        else:
            limit = min(limit, len(matching))

        console.print(
            f"[info]Historial (filtrado por: {tool_filter})[/info]"
        )
        for entry in matching[-limit:]:
            ts = _format_history_timestamp(entry.get("timestamp"))
            name = entry.get("name", "?")
            arguments = entry.get("arguments", {})
            try:
                arg_preview = json.dumps(arguments, ensure_ascii=False, default=str)
            except Exception:
                arg_preview = str(arguments)
            if len(arg_preview) > 100:
                arg_preview = arg_preview[:100] + "…"
            console.print(
                f"[dim]{ts}[/dim] [bold cyan]✦[/bold cyan] {name}({arg_preview})"
            )
        return

    # ── Default: show conversation history with role colors and icons ──
    if limit is None:
        limit = 10

    if not history:
        console.print("[dim]No hay historial para mostrar.[/dim]")
        return

    role_colors = {
        "user": "green",
        "assistant": "blue",
        "system": "yellow",
        "tool": "magenta",
        "function": "cyan",
        "error": "red",
    }
    role_icons = {
        "user": "❯",
        "assistant": "○",
        "system": "⚙",
        "tool": "⚒",
        "function": "∫",
        "error": "✗",
    }

    console.print("[info]᛭ Historial[/info]")
    for i, msg in enumerate(history[-limit:], start=len(history) - limit + 1):
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:200]
        if len(content) == 200:
            content += "…"
        ts = _format_history_timestamp(msg.get("timestamp"))
        color = role_colors.get(role, "white")
        icon = role_icons.get(role, "•")
        console.print(
            f"[dim]{ts}[/dim] [{color}]{icon} {role}:[/{color}] {content}"
        )


def _format_history_timestamp(ts: Any) -> str:
    """Return [HH:MM:SS] from an ISO timestamp or [dim]--:--:--[/dim] placeholder.

    Used by /history to render the timestamp prefix. Falls back gracefully
    on malformed or missing timestamps so the command never crashes on bad data.
    """
    if not ts:
        return "--:--:--"
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            raw = str(ts).strip()
            # Tolerate trailing Z.
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return "--:--:--"


# ── /last-tool command ──────────────────────────────────────────────


async def run_last_tool_command(session: AgentSession, args: str) -> None:
    """Ejecuta /last-tool para mostrar detalles de la última llamada a herramienta.

    Examples:
        /last-tool
        /last-tool 2
        /last-tool file_read
    """
    text = args.strip()
    history: list[dict[str, Any]] = getattr(session, "_tool_call_history", []) or []

    if not history:
        console.print("[dim]No hay llamadas a herramientas en esta sesión.[/]")
        return

    if not text:
        entry = history[-1]
    elif text.isdigit():
        n = int(text)
        if n < 1 or n > len(history):
            render_error(f"Índice fuera de rango: {n} (1-{len(history)})")
            return
        entry = history[-n]
    else:
        # Buscar la llamada más reciente a la herramienta indicada.
        matches = [h for h in history if h.get("name") == text]
        if not matches:
            console.print(f"[dim]No hay llamadas registradas para '{text}'.[/]")
            return
        entry = matches[-1]

    name = entry.get("name", "desconocida")
    arguments = entry.get("arguments", {})
    duration = entry.get("duration")
    timestamp = entry.get("timestamp", "?")
    success = entry.get("success")

    console.print(f"[bold cyan]Herramienta:[/] [bold]{name}[/]")
    console.print(f"[dim]Timestamp:[/] {timestamp}")
    if duration is not None:
        console.print(f"[dim]Duración:[/] {duration:.4f}s")
    if success is not None:
        status = "[success]éxito[/]" if success else "[error]error[/]"
        console.print(f"[dim]Estado:[/] {status}")
    console.print("[bold cyan]Argumentos:[/]")
    try:
        args_text = json.dumps(arguments, ensure_ascii=False, indent=2, default=str)
    except Exception:
        args_text = str(arguments)
    console.print(Syntax(args_text, "json", theme="monokai", word_wrap=True))


# ── /load command ───────────────────────────────────────────────────


# ── /theme command ──────────────────────────────────────────────────


async def run_theme_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /theme [name|current|preview <name>] para cambiar o inspeccionar el tema.

    Examples:
        /theme                      — listar temas disponibles
        /theme cyberpunk            — cambiar al tema
        /theme current              — mostrar el tema activo + atributos
        /theme preview cyberpunk    — muestra cómo se ve un tema sin aplicarlo
        /theme list                 — alias explícito de listar
    """
    from .render import get_theme, list_themes, set_theme

    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        themes = list_themes()
        console.print("\n[bold realm]᛭ Temas disponibles[/]")
        for theme in themes:
            console.print(f"  [bold cyan]{theme.name}[/] — {theme.description}")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "current":
        active = get_theme()
        console.print(
            f"\n[bold realm]᛭ Tema activo[/] "
            f"[bold cyan]{active.name}[/]\n"
        )
        console.print(f"  [info]Label:[/]            [bold]{active.label}[/]")
        console.print(f"  [info]Prefijo prompt:[/]    [bold]{active.prompt_prefix}[/]")
        console.print(f"  [info]Bordes:[/]           [bold {active.border_style}]{active.border_style}[/]")
        console.print(f"  [info]Descripción:[/]      {active.description}")
        console.print()
        return

    if subcmd == "preview":
        if not rest:
            render_error("Uso: /theme preview <nombre>")
            return
        try:
            target = get_theme(rest)
        except KeyError:
            render_error(f"Tema desconocido: {rest}. Usá /theme list para ver los disponibles.")
            return
        # Render a sample panel with the target theme's colors without
        # mutating the live theme. We use a temp console with the
        # target theme's style to show what /help banners would look
        # like. Does NOT call set_theme(), so the user's current theme
        # is untouched.
        from rich.console import Console
        from rich.panel import Panel

        preview_console = Console(theme=None, record=False, force_terminal=True)
        # We can't easily swap themes mid-console; instead, just print
        # the theme's attributes so the user sees what they'd get.
        console.print(
            f"\n[bold realm]᛭ Preview de '{rest}' (sin aplicar)[/]\n"
        )
        console.print(f"  [info]Label:[/]            [bold]{target.label}[/]")
        console.print(f"  [info]Prefijo prompt:[/]    [bold]{target.prompt_prefix}[/]")
        console.print(f"  [info]Bordes:[/]           [bold {target.border_style}]{target.border_style}[/]")
        console.print(f"  [info]Descripción:[/]      {target.description}")
        # Sample a panel in the target border color so the user sees
        # the actual styling, not just metadata.
        sample = Panel(
            f"Prompt prefix: [bold]{target.prompt_prefix}[/]\n"
            f"Border: {target.border_style}\n"
            f"Label: {target.label}",
            title=f"[bold {target.border_style}]{target.label}[/]",
            border_style=target.border_style,
            expand=False,
        )
        console.print(sample)
        console.print()
        return

    try:
        set_theme(text)
        console.print(f"[success]✓ Tema cambiado a: {text}[/]")
    except Exception as exc:
        render_error(f"Error cambiando tema: {exc}")


# ── /config command ───────────────────────────────────────────────────


async def run_config_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /config para mostrar o editar la configuración de la sesión.

    Examples:
        /config
        /config model gpt-4o
        /config provider openai
    """
    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        cfg = session.config
        console.print(f"[info]Modelo: [model]{cfg.model}[/]")
        console.print(f"[info]Proveedor: [model]{cfg.provider}[/]")
        console.print(f"[info]Base URL: [model]{cfg.base_url}[/]")
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        render_error("Uso: /config <clave> <valor>")
        return
    key, value = parts[0], parts[1]
    if not hasattr(session.config, key):
        render_error(f"Clave de configuración desconocida: {key}")
        return
    setattr(session.config, key, value)
    console.print(f"[success]✓ {key} = {value}[/]")


# ── /cost command ─────────────────────────────────────────────────────


# ── /plan command ─────────────────────────────────────────────────────


async def run_plan_command(session: AgentSession, args: str) -> None:
    """Ejecuta /plan [create|show|done|clear|list]."""
    text = args.strip()
    if not text or text.lower() in ("show", "status"):
        _get_plan = getattr(session, "get_plan", None)
        plan = _get_plan() if _get_plan else None
        if not plan:
            console.print("[dim]No hay un plan activo. Creá uno con /plan create <tema>.[/]")
            return
        from rich.panel import Panel

        lines = [f"[bold]{plan.title}[/]"]
        for i, step in enumerate(plan.steps, start=1):
            mark = "[success]✓[/]" if step.done else "[dim]○[/]"
            lines.append(f"{mark} {i}. {step.description}")
        console.print(Panel("\n".join(lines), title="[bold realm]᛭ Plan[/]", expand=False))
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("create", "new"):
        if not rest:
            render_error("Uso: /plan create <descripción>")
            return
        _create_plan = getattr(session, "create_plan", None)
        if _create_plan is not None:
            await _create_plan(rest)
        else:
            render_error("/plan create: create_plan no disponible en esta sesión")
            return
        console.print(f"[success]✓ Plan creado: {rest}[/]")
    elif subcmd in ("done", "complete"):
        try:
            step = int(rest)
        except ValueError:
            render_error("Uso: /plan done <número>")
            return
        _m = getattr(session, "mark_plan_done", None)
        if _m is not None:
            await _m(step)
        else:
            render_error("/plan done: mark_plan_done no disponible en esta sesión")
            return
    elif subcmd in ("clear", "reset"):
        _cp = getattr(session, "clear_plan", None)
        if _cp is None:
            render_error("/plan clear: clear_plan no disponible en esta sesión")
            return
        _cp()
        console.print("[success]✓ Plan eliminado.[/]")
    elif subcmd in ("list", "ls"):
        plans = session.list_plans() if hasattr(session, "list_plans") else []
        if not plans:
            console.print("[dim]No hay planes guardados.[/]")
            return
        console.print("\n[bold realm]᛭ Planes guardados[/]")
        for p in plans:
            console.print(f"  [bold cyan]{p.id}[/] {p.title}")
        console.print()
    else:
        # Treat as a new plan description
        _create_plan2 = getattr(session, "create_plan", None)
        if _create_plan2 is not None:
            await _create_plan2(text)
        else:
            render_error("/plan: create_plan no disponible en esta sesión")
            return
        console.print(f"[success]✓ Plan creado: {text}[/]")


# ── /bookmark command ─────────────────────────────────────────────────


async def run_bookmark_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /bookmark para guardar y reutilizar fragmentos de texto.

    Examples:
        /bookmark <clave> <valor>
        /bookmark list
        /bookmark get <clave>
        /bookmark clear
    """
    text = args.strip()
    bookmarks: dict[str, str] = _load_bookmarks()

    if not text or text.lower() in ("list", "ls"):
        if not bookmarks:
            console.print("[dim]No hay bookmarks guardados.[/]")
            return
        console.print("\n[bold realm]᛭ Bookmarks[/]")
        for name, value in sorted(bookmarks.items()):
            console.print(f"  [bold cyan]{name}[/]: {value}")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("set", "add"):
        if len(parts) < 2:
            render_error("Uso: /bookmark set <clave> <valor>")
            return
        key, value = parts[0], parts[1]
        # fix: the subcmd is parts[0], so key is rest when splitting once
        key, value = rest.split(maxsplit=1) if " " in rest else (rest, "")
        bookmarks[key] = value
        _save_bookmarks(bookmarks)
        console.print(f"[success]✓ Bookmark guardado: {key}[/]")
        return

    if subcmd in ("get", "show"):
        if not rest:
            render_error("Uso: /bookmark get <clave>")
            return
        if rest not in bookmarks:
            render_error(f"Bookmark no encontrado: {rest}")
            return
        console.print(f"[tool.name]{rest}[/]: [tool.result]{bookmarks[rest]}[/]")
        return

    if subcmd in ("clear", "reset"):
        bookmarks.clear()
        _save_bookmarks(bookmarks)
        console.print("[success]✓ Bookmarks eliminados.[/]")
        return

    # /bookmark <clave> <valor>
    try:
        key, value = text.split(maxsplit=1)
    except ValueError:
        render_error("Uso: /bookmark <clave> <valor>|get <clave>|list|clear")
        return
    bookmarks[key] = value
    _save_bookmarks(bookmarks)
    console.print(f"[success]✓ Bookmark guardado: {key}[/]")


# ── Bookmark storage helpers ─────────────────────────────────────────

_BOOKMARKS_PATH = CONFIG_DIR / "bookmarks.json"


def _load_bookmarks() -> dict[str, str]:
    """Carga bookmarks desde ~/.yggdrasil/bookmarks.json."""
    if not _BOOKMARKS_PATH.exists():
        return {}
    try:
        data = json.loads(_BOOKMARKS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # pragma: no cover
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando bookmarks: %s", exc)
    return {}


def _save_bookmarks(bookmarks: dict[str, str]) -> None:
    """Guarda bookmarks en ~/.yggdrasil/bookmarks.json."""
    _BOOKMARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BOOKMARKS_PATH.write_text(
        json.dumps(bookmarks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── /agent command ───────────────────────────────────────────────────


async def run_agent_command(session: AgentSession, args: str) -> None:
    """Ejecuta /agent [mode|start|stop|status] para controlar el agente.

    Examples:
        /agent
        /agent mode <modo>
        /agent start
        /agent stop
    """
    from .agent_modes import apply_agent_mode, get_agent_mode, list_agent_modes

    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        current = getattr(session, "agent_mode", "default")
        console.print(f"[info]Modo agente: [model]{current}[/]")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "mode":
        if not rest:
            render_error("Uso: /agent mode <modo>")
            return
        available = [m.name for m in list_agent_modes()]
        if rest not in available:
            render_error(f"Modo desconocido: {rest}. Opciones: {', '.join(available)}")
            return
        mode_obj = next((m for m in list_agent_modes() if m.name == rest), None)
        if mode_obj is None:
            render_error(f"Modo desconocido: {rest}")
            return
        apply_agent_mode(session, mode_obj)
        console.print(f"[success]✓ Modo agente cambiado a: {rest}[/]")
        return

    if subcmd in ("start", "on"):
        session.agent_mode = getattr(session, "agent_mode", "default")
        console.print(f"[success]✓ Agente activado: {session.agent_mode}[/]")
        return

    if subcmd in ("stop", "off"):
        console.print("[success]✓ Agente detenido.[/]")
        return

    render_error("Uso: /agent [mode <modo>|start|stop|status]")


# ── /redo command ────────────────────────────────────────────────────


async def _stream_agent_reply(session: AgentSession, text: str) -> None:
    """Consume process_message_stream y renderiza los chunks de texto."""
    async for event in session.process_message_stream(text):
        if event.get("type") == "text":
            chunk = event.get("content", "")
            if chunk:
                console.print(chunk, end="")
    console.print()


async def run_redo_command(session: AgentSession, args: str) -> None:
    """Ejecuta /redo para reenviar el último mensaje del usuario.

    Examples:
        /redo
    """
    text = args.strip()
    if text:
        render_error("Uso: /redo")
        return
    last = getattr(session, "_last_user_message", None)
    if not last:
        render_error("No hay un mensaje previo para reenviar.")
        return

    await _stream_agent_reply(session, last)


# ── /continue command ─────────────────────────────────────────────────


async def run_continue_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /continue para pedir al modelo que siga su última respuesta.

    Examples:
        /continue
        /continue <texto adicional>
    """
    text = args.strip()
    prompt = "Continuá la respuesta anterior."
    if text:
        prompt += f"\n{text}"


    await _stream_agent_reply(session, prompt)


# ── /summary command ──────────────────────────────────────────────────


async def run_summary_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /summary para resumir toda la conversación.

    Examples:
        /summary
    """
    text = args.strip()
    if text:
        render_error("Uso: /summary")
        return


    await _stream_agent_reply(session, "Resumí la conversación hasta ahora de forma concisa.")


# ── /recap command ───────────────────────────────────────────────────


async def run_recap_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /recap [n] para resumir las últimas n rondas de la conversación.

    Examples:
        /recap
        /recap 5
    """
    text = args.strip()
    if not text:
        n = 5
    else:
        try:
            n = int(text)
        except ValueError:
            render_error("Uso: /recap [número]")
            return


    prompt = f"Resumí las últimas {n} rondas de la conversación de forma concisa."
    await _stream_agent_reply(session, prompt)


# ── /copy command ─────────────────────────────────────────────────────


async def run_copy_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /copy para copiar el último mensaje del asistente al portapapeles.

    Examples:
        /copy
        /copy last
    """
    text = args.strip()
    if text and text.lower() != "last":
        render_error("Uso: /copy [last]")
        return

    last_assistant = ""
    for msg in reversed(session.history or []):
        if msg.get("role") == "assistant":
            last_assistant = str(msg.get("content", ""))
            break

    if not last_assistant:
        render_error("No hay un mensaje del asistente para copiar.")
        return

    # Try to copy via platform utilities
    copied = False
    try:
        import subprocess

        if os.name == "nt":
            subprocess.run(["clip"], input=last_assistant.encode("utf-8"), check=True, capture_output=True)
            copied = True
        else:
            for cmd in ["xclip", "xsel", "pbcopy"]:
                try:
                    subprocess.run([cmd], input=last_assistant.encode("utf-8"), check=True, capture_output=True)
                    copied = True
                    break
                except Exception:
                    continue
    except Exception:
        copied = False

    if copied:
        console.print("[success]✓ Última respuesta copiada al portapapeles.[/]")
    else:
        console.print("[warning]No se pudo copiar al portapapeles. Última respuesta:[/]")
        console.print(last_assistant)


# ── /status command ───────────────────────────────────────────────────


async def run_status_command(session: AgentSession, args: str) -> None:
    """Show session status with color-coded usage levels (/status)."""
    from rich.table import Table

    text = args.strip()
    if text:
        render_error("Uso: /status")
        return

    total = session.total_usage or {}
    prompt = total.get("prompt_tokens", 0) or 0
    completion = total.get("completion_tokens", 0) or 0
    tokens_total = prompt + completion
    history = session.history or []

    # Color-code token usage: green < 4k, yellow < 16k, red >= 16k
    if tokens_total < 4000:
        usage_style = "green"
    elif tokens_total < 16000:
        usage_style = "yellow"
    else:
        usage_style = "red"

    table = Table(
        title="[bold realm]᛭ Estado de la sesión[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
        caption=f"[dim]{len(history)} mensajes en historial[/dim]",
    )
    table.add_column("Propiedad", style="tool.name")
    table.add_column("Valor", style="tool.result")
    table.add_row("Modelo", str(session.config.model))
    table.add_row("Proveedor", str(session.config.provider))
    table.add_row("Prompt tokens", f"[{usage_style}]{prompt}[/{usage_style}]")
    table.add_row("Completion tokens", f"[{usage_style}]{completion}[/{usage_style}]")
    table.add_row("Total tokens", f"[bold {usage_style}]{tokens_total}[/bold {usage_style}]")
    table.add_row("Mensajes", str(len(history)))

    # Session start time if available
    start_time = getattr(session, "_start_time", None)
    if start_time:
        import time as _time
        elapsed = int(_time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            uptime = f"{hours}h {mins}m {secs}s"
        elif mins:
            uptime = f"{mins}m {secs}s"
        else:
            uptime = f"{secs}s"
        table.add_row("Uptime", f"[dim]{uptime}[/dim]")

    # Last command if available
    last_cmd = getattr(session, "_last_command", None)
    if last_cmd:
        table.add_row("Último comando", f"[dim]/{last_cmd}[/dim]")

    console.print(table)
    console.print()

# ── /profile command ─────────────────────────────────────────────────


async def run_profile_command(session: AgentSession, args: str) -> None:
    """Gestiona perfiles de configuración (/profile [list|save|show|load|delete])."""
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        profiles = _load_profiles()
        if not profiles:
            console.print("[dim]No hay perfiles guardados.[/dim]")
            return
        console.print("\n[bold realm]᛭ Perfiles de agente[/]\n")
        for name, profile in sorted(profiles.items()):
            desc = profile.get("description", "")
            console.print(
                f"  [bold cyan]{name}[/]"
                f"{f' — [dim]{desc}[/]' if desc else ''}"
            )
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    profiles = _load_profiles()

    if subcmd == "save":
        if not rest:
            render_error("Uso: /profile save <nombre>")
            return
        profiles[rest] = {
            "provider": session.config.provider,
            "model": session.config.model,
            "description": f"Basado en {session.config.provider}/{session.config.model}",
        }
        _save_profiles(profiles)
        console.print(f"[success]✓ Perfil guardado: {rest}[/]")
        return

    if subcmd == "show":
        if not rest:
            render_error("Uso: /profile show <nombre>")
            return
        if rest not in profiles:
            render_error(f"Perfil no encontrado: {rest}")
            return
        console.print(
            f"[dim]{json.dumps({rest: profiles[rest]}, indent=2, ensure_ascii=False)}[/]"
        )
        return

    if subcmd == "load":
        if not rest:
            render_error("Uso: /profile load <nombre>")
            return
        if rest not in profiles:
            render_error(f"Perfil no encontrado: {rest}")
            return
        profile = profiles[rest]
        if hasattr(session.config, "provider"):
            session.config.provider = profile.get("provider", session.config.provider)
        if hasattr(session.config, "model"):
            session.config.model = profile.get("model", session.config.model)
        console.print(f"[success]✓ Perfil cargado: {rest}[/]")
        return

    if subcmd == "delete":
        if not rest:
            render_error("Uso: /profile delete <nombre>")
            return
        if rest not in profiles:
            render_error(f"Perfil no encontrado: {rest}")
            return
        del profiles[rest]
        _save_profiles(profiles)
        console.print(f"[warning]✗ Perfil eliminado: {rest}[/]")
        return

    render_error(
        "Uso: /profile [list|save <nombre>|show <nombre>|load <nombre>|delete <nombre>]"
    )





# ── /tour command ───────────────────────────────────────────────────────

# Tour steps. Names + bodies reference real slash commands so the tour
# doesn't drift from what the registry actually exposes; if a command
# disappears, /tour will say so on next launch instead of staying stale.
_TOUR_STEPS: list[tuple[str, str]] = [
    (
        "Bienvenido a Lilith",
        "Lilith es el agente CLI de Yggdrasil. Este recorrido te muestra las funciones principales en 5 pasos.\n"
        "Usá /tour step N para saltar a un paso, o /tour skip para salir.",
    ),
    (
        "Seguridad: confirm_write y undo",
        "Antes de escribir o editar archivos, Lilith puede mostrar un diff para confirmar.\n"
        "Si algo sale mal, /undo deshace el último cambio de archivo automáticamente.",
    ),
    (
        "Herramientas principales",
        "read_file, write_file y patch manejan archivos; /test ejecuta pruebas; "
        "/git cubre git, /search busca en archivos.",
    ),
    (
        "Comandos de barra",
        "/help lista todos los comandos; /tools habilita/deshabilita herramientas; "
        "/cost y /metrics muestran uso de tokens y costos.",
    ),
    (
        "Funciones avanzadas",
        "/export guarda la conversación, /load la restaura; "
        "/bookmark marca puntos de interés; /compact resume el historial.",
    ),
]


async def run_tour_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /tour para iniciar un recorrido interactivo por Lilith.

    Examples:
        /tour
        /tour step 2
        /tour skip
    """
    text = args.strip().lower()

    if text == "skip":
        console.print("\n[dim]Recorrido cancelado.[/]\n")
        return

    if text.startswith("step"):
        rest = text[4:].strip()
        try:
            step = int(rest)
        except ValueError:
            render_error("Uso: /tour step <número>")
            return
        if step < 1 or step > len(_TOUR_STEPS):
            render_error(f"Paso inválido: {step}. El recorrido tiene 1-{len(_TOUR_STEPS)}.")
            return
        _render_tour_step(step)
        return

    if text:
        render_error("Uso: /tour [step N|skip]")
        return

    console.print("\n[bold realm]᛭ Recorrido interactivo de Lilith[/]")
    for i in range(1, len(_TOUR_STEPS) + 1):
        _render_tour_step(i)
    console.print("[dim]Recorrido completado. Escribí /tour skip para salir o /tour step N para repetir un paso.[/]\n")


def _render_tour_step(step: int) -> None:
    """Renderiza un paso del recorrido en la consola."""
    title, body = _TOUR_STEPS[step - 1]
    console.print(f"\n[bold cyan]Paso {step}/{len(_TOUR_STEPS)}: {title}[/]")
    console.print(f"[tool.result]{body}[/]")
    if step < len(_TOUR_STEPS):
        console.print("[dim]Escribí /tour para continuar con el recorrido completo.[/]")
    console.print()


# ── /pin command ─────────────────────────────────────────────────────────


async def run_pin_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Fija un mensaje importante en la conversación actual.

    Examples:
        /pin                 — fija el último mensaje del asistente
        /pin <n>             — fija el n-ésimo mensaje del historial (1-based)
        /pin list | --list   — lista los mensajes fijados
        /pin remove <n>      — elimina el fijado en el índice n
        /pin clear | --clear — elimina todos los fijados
    """
    from lilith_tools import ToolRegistry

    text = args.strip()
    pins = _load_pin_entries(session)

    # Parse simple flag-style arguments. Accept both --flag and bare subcommand
    # forms (``list``/``clear``/``remove N``) so the REPL UX stays simple.
    parts = text.split()

    def _persist() -> None:
        """Mirror pins into session attribute and persist to disk."""
        # Keep the in-memory mirror (used by session serialization / fork / QR).
        session._pinned_messages = list(pins)
        _save_pin_entries(session, pins)

    if not parts:
        result = _pin_default_message(session, pins)
        if not result.get("ok"):
            render_error(result.get("error", "Error fijando mensaje"))
            return
        _persist()
        _print_pin_result(result, pins)
        _register_pin_tool()
        ToolRegistry.get("pin_message")  # ensure import side-effect only
        return

    head = parts[0].lower()
    if head in ("--list", "-l", "list", "ls"):
        _print_pinned_messages(pins)
        _register_pin_tool()
        return

    if head in ("--clear", "clear", "reset"):
        count = len(pins)
        pins.clear()
        _persist()
        console.print(f"[success]✓ {count} mensaje(s) pineado(s) eliminado(s).[/]")
        _register_pin_tool()
        return

    if head in ("--unpin", "-u", "remove", "rm", "unpin"):
        if len(parts) < 2:
            render_error("Uso: /pin remove <índice>")
            return
        try:
            index = int(parts[1])
        except ValueError:
            render_error("remove requiere un índice entero")
            return
        if not pins:
            render_error("No hay mensajes pineados.")
            return
        if index < 1 or index > len(pins):
            render_error(f"Índice fuera de rango: {index} (hay {len(pins)} pineados)")
            return
        removed = pins.pop(index - 1)
        _persist()
        role = removed.get("role", "?")
        content_preview = str(removed.get("content") or removed.get("text", ""))[:40]
        if len(content_preview) == 40:
            content_preview += "…"
        console.print(f"[warning]✗ Despineado [#{index}] {role}: {content_preview}[/]")
        _register_pin_tool()
        return

    # Optional positional <index>: pin that 1-based message in history.
    try:
        index = int(parts[0])
    except ValueError:
        render_error(
            "Uso: /pin | /pin <n> | /pin list | /pin remove <n> | /pin clear"
        )
        return

    result = _pin_message_at_index(session, pins, index)
    if not result.get("ok"):
        render_error(result.get("error", "Error fijando mensaje"))
        return
    _persist()
    msg = result["entry"]
    preview = str(msg.get("content") or msg.get("text", ""))[:80]
    if len(preview) == 80:
        preview += "…"
    console.print(f"📌 Mensaje pineado en el índice {index}: {preview}")
    _register_pin_tool()


def _print_pinned_messages(pinned: list[dict[str, Any]]) -> None:
    """Renderiza los mensajes pineados con su índice."""
    if not pinned:
        console.print("[dim]No hay mensajes pineados.[/]")
        return

    console.print("\n[bold realm]᛭ Mensajes pineados[/]")
    for i, msg in enumerate(pinned, start=1):
        role = msg.get("role", "?")
        content = str(msg.get("content") or msg.get("text", ""))
        preview = content[:80]
        if len(preview) == 80:
            preview += "…"
        console.print(f"  [bold cyan]{i}.[/] [{role}] {preview}")
    console.print()



# ---------------------------------------------------------------------------
# /pin storage + helpers + tool (pin_message)
# ---------------------------------------------------------------------------
import uuid as _uuid


def _pin_storage_path() -> Path:
    """Return the path to ``~/.lilith/pins.json`` (created lazily)."""
    base = Path.home() / ".lilith"
    base.mkdir(parents=True, exist_ok=True)
    return base / "pins.json"


def _get_session_id(session: AgentSession) -> str:
    """Return a stable session id, generating a uuid if missing."""
    sid = getattr(session, "session_id", None)
    if not sid:
        sid = getattr(session, "_session_id", None)
    if not sid:
        sid = str(_uuid.uuid4())
        try:
            session._session_id = sid
        except Exception:
            pass
    return sid


def _load_pin_entries(session: AgentSession) -> list[dict[str, Any]]:
    """Load pinned-message entries for the current session.

    Prefers the in-memory ``session._pinned_messages`` mirror (used by session
    serialization / fork / QR) and falls back to the on-disk JSON store
    when no in-memory copy exists yet.
    """
    mirror = getattr(session, "_pinned_messages", None)
    if isinstance(mirror, list) and mirror:
        return [dict(e) for e in mirror if isinstance(e, dict)]
    path = _pin_storage_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return []
    sid = _get_session_id(session)
    entries = payload.get(sid) or []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _save_pin_entries(session: AgentSession, entries: list[dict[str, Any]]) -> None:
    """Persist pinned-message entries for the current session to disk."""
    path = _pin_storage_path()
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(existing, dict):
                payload = existing
        except (json.JSONDecodeError, OSError):
            payload = {}
    sid = _get_session_id(session)
    payload[sid] = list(entries)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_pin_entry(message: dict[str, Any], index: int) -> dict[str, Any]:
    """Build a pin entry dict from a session message and its 1-based index."""
    text = str(message.get("content", message.get("text", "")))
    role = str(message.get("role", "assistant"))
    return {
        "index": int(index),
        "content": text,
        "text": text,  # backward-compat alias for older readers
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "role": role,
    }


def _resolve_message_by_index(session: AgentSession, index: int) -> dict[str, Any] | None:
    """Resolve a 1-based index against the session history (most recent = 1)."""
    history = getattr(session, "history", None) or []
    if not history:
        return None
    if index < 1 or index > len(history):
        return None
    return history[-index]


def _resolve_last_assistant_message(session: AgentSession) -> dict[str, Any] | None:
    """Return the most recent assistant message in the history, if any."""
    history = getattr(session, "history", None) or []
    for msg in reversed(history):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return msg
    return None


def _pin_message_at_index(
    session: AgentSession,
    pins: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    """Pin the n-th most recent message and append it to *pins* (in-memory)."""
    msg = _resolve_message_by_index(session, index)
    if msg is None:
        return {"ok": False, "error": f"Índice fuera de rango: {index}"}
    entry = _make_pin_entry(msg, index)
    pins.append(entry)
    return {"ok": True, "entry": entry}


def _pin_default_message(
    session: AgentSession, pins: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pin the most recent assistant message (or last history message)."""
    msg = _resolve_last_assistant_message(session)
    history = getattr(session, "history", None) or []
    if msg is None:
        if not history:
            return {"ok": False, "error": "No hay mensajes en el historial para fijar."}
        msg = history[-1]
    index = len(history) if history else 1
    entry = _make_pin_entry(msg, index)
    pins.append(entry)
    return {"ok": True, "entry": entry, "index": index}


def _print_pin_result(result: dict[str, Any], pins: list[dict[str, Any]]) -> None:
    """Render the result of a default ``/pin`` invocation."""
    if not result.get("ok"):
        render_error(result.get("error", "Error fijando mensaje"))
        return
    entry = result["entry"]
    index = result.get("index", entry.get("index", len(pins)))
    text = str(entry.get("text", ""))
    preview = text[:80]
    if len(preview) == 80:
        preview += "…"
    _save_pin_entries(session, pins)
    console.print(
        f"📌 Mensaje fijado en el índice {index}: {preview}"
    )


@ToolRegistry.register
class PinMessageTool(BaseTool):
    """Fija (pin) un mensaje de la conversación para tenerlo siempre a mano.

    Esta herramienta es el equivalente invocable por el agente del comando
    ``/pin`` del REPL. Permite fijar el último mensaje del asistente
    (sin argumentos), un mensaje concreto por índice 1-based, listar
    los mensajes fijados, desfijar uno por índice o limpiar todos.
    """

    name = "pin_message"
    description = (
        "Fija un mensaje importante de la conversación para consultarlo "
        "después. Por defecto fija el último mensaje del asistente."
    )
    parameters = {
        "index": {
            "type": "integer",
            "required": False,
            "default": 0,
            "description": (
                "Índice 1-based del mensaje a fijar (0 = último del asistente)"
            ),
        },
        "list": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Si es True, lista los mensajes fijados.",
        },
        "unpin": {
            "type": "integer",
            "required": False,
            "default": -1,
            "description": "Índice 1-based del mensaje a desfijar (-1 = ninguno).",
        },
        "clear": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Si es True, elimina todos los mensajes fijados.",
        },
    }

    def execute(
        self,
        session: AgentSession,
        index: int = 0,
        list: bool = False,
        unpin: int = -1,
        clear: bool = False,
        **_: Any,
    ) -> ToolResult:
        """Ejecuta la operación de pin solicitada."""
        try:
            pins = _load_pin_entries(session)
        except Exception as exc:
            return ToolResult(success=False, data=None, error=str(exc))

        if clear:
            count = len(pins)
            try:
                _save_pin_entries(session, [])
            except Exception as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            return ToolResult(
                success=True,
                data={"action": "clear", "removed": count},
            )

        if list:
            return ToolResult(success=True, data={"action": "list", "pins": pins})

        if unpin and unpin > 0:
            if unpin < 1 or unpin > len(pins):
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"Índice fuera de rango: {unpin}",
                )
            removed = pins.pop(unpin - 1)
            try:
                _save_pin_entries(session, pins)
            except Exception as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            return ToolResult(
                success=True,
                data={"action": "unpin", "removed": removed, "index": unpin},
            )

        if index and index > 0:
            result = _pin_message_at_index(session, pins, index)
            if not result.get("ok"):
                return ToolResult(
                    success=False,
                    data=None,
                    error=str(result.get("error", "Error fijando mensaje")),
                )
            try:
                _save_pin_entries(session, pins)
            except Exception as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            return ToolResult(
                success=True,
                data={"action": "pin", "entry": result["entry"]},
            )

        # Default: pin the most recent assistant message.
        result = _pin_default_message(session, pins)
        if not result.get("ok"):
            return ToolResult(
                success=False,
                data=None,
                error=str(result.get("error", "Error fijando mensaje")),
            )
        try:
            _save_pin_entries(session, pins)
        except Exception as exc:
            return ToolResult(success=False, data=None, error=str(exc))
        return ToolResult(
            success=True,
            data={"action": "pin", "entry": result["entry"]},
        )


_PIN_TOOL_REGISTERED = False


def _register_pin_tool() -> None:
    """Idempotently register :class:`PinMessageTool` in the global registry."""
    global _PIN_TOOL_REGISTERED
    if _PIN_TOOL_REGISTERED:
        return
    if ToolRegistry.get("pin_message") is not None:
        _PIN_TOOL_REGISTERED = True
        return
    _PIN_TOOL_REGISTERED = True


# Ensure the tool is registered when this module is imported.
_register_pin_tool()



# ── /model-info command ──────────────────────────────────────────────────


# Provider hint derived from model-name prefixes or known families.
_MODEL_PROVIDER_HINTS: dict[str, str] = {
    "fugu": "Sakana",
    "claude": "Anthropic",
    "gpt": "OpenAI",
    "o3": "OpenAI",
    "deepseek": "DeepSeek",
    "qwen": "Alibaba / Qwen",
    "kimi": "Moonshot",
    "moonshot": "Moonshot",
    "seed": "BytePlus",
    "glm": "BytePlus",
    "grok": "xAI",
    "local-model": "Local",
}

# Capabilities are broad tags useful for REPL display.
_MODEL_CAPABILITIES: dict[str, list[str]] = {
    "fugu-ultra": ["chat", "tool-calling", "long-context", "streaming"],
    "fugu-ultra-20260615": ["chat", "tool-calling", "long-context", "streaming"],
    "claude-sonnet-4": ["chat", "tool-calling", "vision", "long-context", "streaming"],
    "claude-opus-4": ["chat", "tool-calling", "vision", "long-context", "streaming", "reasoning"],
    "claude-haiku-4": ["chat", "tool-calling", "vision", "streaming"],
    "gpt-4o": ["chat", "tool-calling", "vision", "streaming"],
    "gpt-4o-mini": ["chat", "tool-calling", "vision", "streaming"],
    "o3": ["chat", "reasoning", "tool-calling", "streaming"],
    "deepseek-chat": ["chat", "tool-calling", "streaming"],
    "deepseek-v4-flash": ["chat", "tool-calling", "streaming"],
    "deepseek-reasoner": ["chat", "reasoning", "streaming"],
    "qwen-max-latest": ["chat", "tool-calling", "streaming"],
    "qwen-plus-latest": ["chat", "tool-calling", "streaming"],
    "qwen3.7-max": ["chat", "tool-calling", "streaming"],
    "kimi-for-coding": ["chat", "tool-calling", "long-context", "streaming"],
    "moonshot-v1-128k": ["chat", "long-context", "streaming"],
    "seed-1-6-250915": ["chat", "tool-calling", "streaming"],
    "glm-4-7-251222": ["chat", "tool-calling", "streaming"],
    "grok-4.20-0309-non-reasoning": ["chat", "tool-calling", "streaming"],
    "grok-4": ["chat", "tool-calling", "long-context", "streaming"],
    "grok-3": ["chat", "tool-calling", "long-context", "streaming"],
    "local-model": ["chat", "local"],
}


def _provider_hint(model: str) -> str:
    """Return a human-readable provider hint for *model*."""
    lower = model.lower()
    for prefix, provider in _MODEL_PROVIDER_HINTS.items():
        if lower.startswith(prefix):
            return provider
    return "Unknown"


def _model_capabilities(model: str) -> list[str]:
    """Return capability tags for *model*."""
    return _MODEL_CAPABILITIES.get(model, ["chat"])


def _format_price(rate: float) -> str:
    """Format a price-per-million-tokens rate as USD."""
    if rate == 0.0:
        return "—"
    return f"${rate:.2f}"


def _format_context_window(tokens: int) -> str:
    """Format a context-window size in a human-readable way."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}K"
    return str(tokens)


def _print_model_info_table(model: str, *, is_current: bool = False) -> None:
    """Render detailed info for a single model as a Rich table."""
    from rich.table import Table

    from .providers import _MODEL_CONTEXTS, _MODEL_PRICING

    context_window = _MODEL_CONTEXTS.get(model, 128_000)
    input_price, output_price = _MODEL_PRICING.get(model, (0.0, 0.0))
    provider = _provider_hint(model)
    capabilities = _model_capabilities(model)

    title = f"[bold realm]᛭ Modelo {model}[/]"
    if is_current:
        title += " [dim](actual)[/]"

    table = Table(
        title=title,
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Propiedad", style="tool.name")
    table.add_column("Valor", style="tool.result")

    table.add_row("Nombre", model)
    table.add_row("Proveedor", provider)
    table.add_row("Ventana de contexto", f"{context_window} tokens ({_format_context_window(context_window)})")
    table.add_row("Precio entrada", f"{_format_price(input_price)} / 1M tokens")
    table.add_row("Precio salida", f"{_format_price(output_price)} / 1M tokens")
    table.add_row("Capacidades", ", ".join(capabilities))
    console.print(table)


def _print_model_list() -> None:
    """Render a compact list of all known models with pricing."""
    from rich.table import Table

    from .providers import _MODEL_CONTEXTS, _MODEL_PRICING

    table = Table(
        title="[bold realm]᛭ Modelos conocidos[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Modelo", style="tool.name")
    table.add_column("Proveedor", style="tool.result")
    table.add_column("Contexto", justify="right")
    table.add_column("Entrada / 1M", justify="right")
    table.add_column("Salida / 1M", justify="right")

    for model in sorted(_MODEL_CONTEXTS.keys()):
        provider = _provider_hint(model)
        context_window = _MODEL_CONTEXTS.get(model, 0)
        input_price, output_price = _MODEL_PRICING.get(model, (0.0, 0.0))
        table.add_row(
            model,
            provider,
            f"{_format_context_window(context_window)}",
            _format_price(input_price),
            _format_price(output_price),
        )

    console.print(table)


async def run_model_info_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /model-info para mostrar información detallada de modelos.

    Examples:
        /model-info                 — información del modelo actual
        /model-info <modelo>        — información de un modelo específico
        /model-info list            — lista todos los modelos conocidos con precios
    """
    from .providers import _MODEL_CONTEXTS

    text = args.strip()

    if not text or text.lower() == "current":
        model = session.config.model
        _print_model_info_table(model, is_current=True)
        return

    if text.lower() in ("list", "ls", "all"):
        _print_model_list()
        return

    model = text
    if model.lower() not in {m.lower() for m in _MODEL_CONTEXTS}:
        render_error(f"Modelo desconocido: {model}. Usá /model-info list para ver los conocidos.")
        return

    # Use canonical casing from the registry.
    canonical = next(m for m in _MODEL_CONTEXTS if m.lower() == model.lower())
    _print_model_info_table(canonical, is_current=(canonical == session.config.model))


# ── Shared result printer ─────────────────────────────────────────────


def _print_tool_result(result) -> None:
    """Renderiza el resultado genérico de una herramienta de lilith_tools."""
    if not result.success:
        error = result.error or "Error desconocido ejecutando la herramienta"
        render_error(error)
        return

    data = result.data
    if isinstance(data, dict):
        if "message" in data:
            console.print(f"[success]✓ {data['message']}[/]")
            return
        if "output" in data:
            console.print(data["output"])
            return

    console.print(str(data) if data is not None else "[success]✓ Hecho[/]")




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
            content = str(todo.get("content", todo.get("task", str(todo))))
            status = str(todo.get("status", "pending"))
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
async def run_json_mode_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /json-mode para alternar la salida estructurada JSON del LLM.

    Examples:
        /json-mode on     — habilita JSON output
        /json-mode off    — deshabilita JSON output
        /json-mode status — muestra el estado actual
    """
    text = args.strip().lower()

    if not text or text == "status":
        state = "ON" if getattr(session, "_json_mode", False) else "OFF"
        console.print(f"[bold realm]᛭ JSON mode:[/] [cyan]{state}[/]")
        return

    if text == "on":
        session._json_mode = True
        console.print("[success]✓ JSON mode habilitado.[/]")
        return

    if text == "off":
        session._json_mode = False
        console.print("[success]✓ JSON mode deshabilitado.[/]")
        return

    render_error("Uso: /json-mode [on|off|status]")


async def run_hooks_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Gestiona hooks del lifecycle (/hooks [list|add <event> <file>|remove <event> <file>])."""

    from .hooks import _EVENTS, _HOOKS_DIR, list_hooks

    text = args.strip()
    if not text or text == "list":
        installed = list_hooks()
        console.print("\n[bold realm]᛭ Hooks de Lilith ⚔[/]\n")
        for event in _EVENTS:
            files = installed.get(event, [])
            count = len(files)
            mark = f"[green]{count} script(s)[/]" if count > 0 else "[dim](ninguno)[/]"
            console.print(f"  [bold cyan]{event}[/]: {mark}")
            for f in files:
                console.print(f"    [dim]└─ {f}[/]")
        console.print(f"\n[muted]Directorio: {_HOOKS_DIR}[/muted]")
        console.print("[dim]Eventos: " + ", ".join(_EVENTS) + "[/dim]")
        return

    if text == "help":
        console.print(
            "[dim]Eventos: pre-tool-call, post-tool-call, on-error, on-cancel, on-compact[/dim]"
        )
        return

    parts = text.split(maxsplit=2)
    if len(parts) >= 3 and parts[0] == "add":
        event = parts[1]
        if event not in _EVENTS:
            console.print(f"[error]Evento desconocido: {event}[/error]")
            return
        script_path = Path(parts[2]).expanduser()
        if not script_path.exists():
            console.print(f"[error]Script no existe: {script_path}[/error]")
            return
        target_dir = _HOOKS_DIR / event
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / script_path.name
        target.write_text(script_path.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            target.chmod(0o755)
        except Exception:
            pass
        console.print(f"[success]✓ Hook registrado: {target}[/success]")
        return

    if len(parts) >= 3 and parts[0] == "remove":
        event = parts[1]
        if event not in _EVENTS:
            console.print(f"[error]Evento desconocido: {event}[/error]")
            return
        target = _HOOKS_DIR / event / parts[2]
        if not target.exists():
            console.print(f"[error]Hook no existe: {target}[/error]")
            return
        target.unlink()
        console.print(f"[warning]✗ Hook eliminado: {target}[/warning]")
        return

    console.print(
        "[dim]Uso: /hooks [list|add <event> <file>|remove <event> <file>|help][/dim]"
    )


# ── /fork command ─────────────────────────────────────────────────────

_FORKS_DIR = Path.home() / ".yggdrasil" / "forks"


def _serialize_session(session: AgentSession) -> dict[str, Any]:
    """Return a JSON-serializable snapshot of the session state."""
    return {
        "version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "config": session.config.model_dump(),
        "history": list(session.history),
        "system_prompt": session.system_prompt,
        "total_usage": dict(session._total_usage),
        "per_model_usage": dict(session._per_model_usage),
        "last_user_message": session._last_user_message,
        "agent_mode": session.agent_mode,
        "agent_allow_writes": session._agent_allow_writes,
        "agent_plan_first": session._agent_plan_first,
        "auto_execute": session._auto_execute,
        "auto_approved_patterns": list(session._auto_approved_patterns),
        "stream_enabled": session._stream_enabled,
        "disabled_tools": sorted(session._disabled_tools),
        "pinned_messages": list(session._pinned_messages),
        "tool_call_history": list(session._tool_call_history),
        "command_history": list(session._command_history),
        "file_edit_history": list(session._file_edit_history),
    }


def _deserialize_session(session: AgentSession, data: dict[str, Any]) -> None:
    """Restore a session snapshot produced by `_serialize_session`."""
    from .config import YggdrasilConfig

    cfg_data = data.get("config", session.config.model_dump())
    session.config = YggdrasilConfig(**cfg_data)
    session.system_prompt = data.get("system_prompt", session.config.system_prompt)
    session.history = list(data.get("history", []))
    session._total_usage = dict(data.get("total_usage", session._total_usage))
    session._per_model_usage = dict(data.get("per_model_usage", session._per_model_usage))
    session._last_user_message = data.get("last_user_message", "")
    session.agent_mode = data.get("agent_mode", "default")
    session._agent_allow_writes = data.get("agent_allow_writes", True)
    session._agent_plan_first = data.get("agent_plan_first", False)
    session._auto_execute = data.get("auto_execute", False)
    session._auto_approved_patterns = list(data.get("auto_approved_patterns", []))
    session._stream_enabled = data.get("stream_enabled", True)
    session._disabled_tools = set(data.get("disabled_tools", []))
    session._pinned_messages = list(data.get("pinned_messages", []))
    session._tool_call_history = list(data.get("tool_call_history", []))
    session._command_history = list(data.get("command_history", []))
    session._file_edit_history = list(data.get("file_edit_history", []))


def _fork_path(name: str) -> Path:
    """Return the file path for a named fork."""
    safe_name = re.sub(r"[^\w\-]", "_", name.strip())
    return _FORKS_DIR / f"{safe_name}.json"


def _list_forks() -> list[str]:
    """Return sorted list of fork names currently stored."""
    if not _FORKS_DIR.exists():
        return []
    return sorted(
        p.stem for p in _FORKS_DIR.glob("*.json") if p.is_file()
    )


async def run_fork_command(session: AgentSession, args: str) -> None:
    """Ejecuta /fork para ramificar la sesión actual.

    Examples:
        /fork <nombre>          — Guarda el estado actual en una nueva sesión y vuelve al original
        /fork list              — Lista las sesiones bifurcadas
        /fork switch <nombre>   — Cambia a una sesión bifurcada
        /fork delete <nombre>   — Elimina una sesión bifurcada
    """
    text = args.strip()
    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if subcmd in ("list", "ls"):
        forks = _list_forks()
        if not forks:
            console.print("[dim]No hay sesiones bifurcadas.[/]")
            return
        console.print("\n[bold realm]᛭ Sesiones bifurcadas[/]")
        for name in forks:
            console.print(f"  [bold cyan]{name}[/]")
        console.print()
        return

    if subcmd == "delete":
        if not rest:
            render_error("Uso: /fork delete <nombre>")
            return
        path = _fork_path(rest)
        if not path.exists():
            render_error(f"No existe la sesión bifurcada: {rest}")
            return
        path.unlink()
        console.print(f"[success]✓ Sesión eliminada: [bold cyan]{rest}[/][/]")
        return

    if subcmd == "switch":
        if not rest:
            render_error("Uso: /fork switch <nombre>")
            return
        path = _fork_path(rest)
        if not path.exists():
            render_error(f"No existe la sesión bifurcada: {rest}")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            render_error(f"Error cargando la sesión bifurcada: {exc}")
            return
        _deserialize_session(session, data)
        console.print(
            f"[success]✓ Sesión activa cambiada a [bold cyan]{rest}[/] "
            f"({len(session.history)} mensajes)[/]"
        )
        return

    # /fork <nombre> — save current state to a named fork and return to original
    name = text if text and not subcmd else subcmd
    if not name:
        render_error("Uso: /fork <nombre> | /fork list | /fork switch <nombre> | /fork delete <nombre>")
        return
    path = _fork_path(name)
    _FORKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(_serialize_session(session), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        console.print(
            f"[success]✓ Sesión bifurcada guardada: [bold cyan]{name}[/] "
            f"({len(session.history)} mensajes)[/]"
        )
    except Exception as exc:
        render_error(f"Error guardando la sesión bifurcada: {exc}")


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


# ── /recent command ──────────────────────────────────────────────────


async def run_recent_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """List files edited or written during the current session.

    Reads ``session._file_edit_history`` (populated by agent.py when
    file_write / file_edit tools succeed) and renders the most recent
    entries first. Each entry shows the file path, the tool used, a
    short timestamp, and the file's current size on disk.

    Examples:
        /recent                — show last 10 edits (default)
        /recent 25             — show last 25
        /recent clear          — wipe the in-session history
    """
    history = getattr(session, "_file_edit_history", None)
    if history is None:
        console.print(
            "[warning]Telemetría de ediciones no activa en esta sesión.[/]"
        )
        return

    text = args.strip().lower()

    if text == "clear":
        history.clear()
        console.print("[success]✓ Historial de archivos recientes vaciado.[/]")
        return

    # Parse optional count (default 10, max 50).
    limit = 10
    if text:
        try:
            limit = max(1, min(50, int(text)))
        except ValueError:
            render_error(f"Uso: /recent [N | clear]  ·  N entre 1 y 50, recibí: {text!r}")
            return

    if not history:
        console.print("[dim]No hay archivos editados en esta sesión todavía.[/]")
        return

    # Most recent first, deduped by path so multiple edits to the same
    # file collapse to one entry with the latest timestamp.
    seen: dict[str, dict] = {}
    for entry in reversed(history):
        path = entry.get("path", "")
        if path and path not in seen:
            seen[path] = entry

    items = list(seen.values())[:limit]

    from rich.table import Table

    table = Table(
        title="[bold realm]᛭ Archivos editados recientemente[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("#", style="dim", justify="right", width=4)
    # no_wrap + overflow='ignore' preserves long paths verbatim (no
    # '…' truncation), letting the terminal's own wrap handle them.
    table.add_column("Archivo", style="tool.name", no_wrap=True, overflow="ignore")
    table.add_column("Tool", justify="center", width=10)
    table.add_column("Cuándo", style="dim", width=19)
    table.add_column("Tamaño", justify="right", style="dim", width=10)

    for i, entry in enumerate(items, start=1):
        path_str = entry.get("path", "?")
        tool = entry.get("tool", "?")
        ts = entry.get("timestamp", "")
        if "T" in ts:
            ts = ts.replace("T", " ")[:19]

        # Resolve size from disk; if the file was deleted, show "—".
        try:
            size_bytes = Path(path_str).stat().st_size
            size_str = _format_size(size_bytes)
        except OSError:
            size_str = "[dim]—[/]"

        table.add_row(str(i), path_str, tool, ts, size_str)

    console.print(table)
    if len(seen) > limit:
        console.print(
            f"[dim]Mostrando {limit} de {len(seen)} archivos únicos. "
            f"Usá /recent {limit * 2} para ver más.[/]"
        )
    console.print()


def _format_size(num_bytes: int) -> str:
    """Compact human-readable file size."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.1f} GB"


# ── /cls (clear screen) ─────────────────────────────────────────────


async def run_clear_screen_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Limpia la pantalla del terminal sin tocar el historial.

    Equivalente a escribir ``cls`` (Windows) o ``clear`` (Unix) en el shell,
    pero dentro del REPL. No toca ``session.history`` ni ``session._file_edit_history``.

    Examples:
        /cls
    """
    import os
    import sys

    del session  # unused; kept for command-dispatcher signature parity.

    # ANSI clear-screen + cursor-home. Works on Windows 10+ Terminal, modern
    # conemu, Linux/macOS terminals, and the Git-Bash mintty used here.
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")
    else:
        # Non-TTY (piped output, tests, IDE captures): just emit enough
        # newlines to push the prior content off-screen. Better than
        # silently doing nothing.
        sys.stdout.write("\n" * 50)
        sys.stdout.flush()


async def run_tree_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /tree para mostrar el árbol de archivos del directorio actual.

    Diferente a system_info.directory_list: muestra una jerarquía visual con
    iconos y tamaños, limitada por profundidad.

    Examples:
        /tree                       — árbol del directorio actual (profundidad 3)
        /tree src                   — árbol del directorio indicado
        /tree src depth=2           — profundidad personalizada
    """
    text = args.strip()
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

EDITOR_CONFIG_FILE = CONFIG_DIR / "editor.json"
_FROZEN_EDITOR: str | None = None


def _get_editor() -> str | None:
    """Return the preferred editor command.

    Order of precedence:
    1. Runtime override set via /editor set.
    2. EDITOR environment variable.
    3. Fallback editors (vim, vi, nano, notepad).
    """
    if _FROZEN_EDITOR is not None:
        return _FROZEN_EDITOR
    if os.environ.get("EDITOR"):
        return os.environ.get("EDITOR")
    for candidate in ("vim", "vi", "nano", "notepad"):
        if shutil.which(candidate):
            return candidate
    return None


def _set_editor(command: str) -> None:
    """Persist the preferred editor to disk and update the in-memory value."""
    global _FROZEN_EDITOR
    _FROZEN_EDITOR = command
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    EDITOR_CONFIG_FILE.write_text(json.dumps({"command": command}, ensure_ascii=False), encoding="utf-8")


def _load_editor() -> None:
    """Load a previously persisted editor override from disk."""
    global _FROZEN_EDITOR
    if EDITOR_CONFIG_FILE.exists():
        try:
            data = json.loads(EDITOR_CONFIG_FILE.read_text(encoding="utf-8"))
            command = data.get("command", "")
            if command:
                _FROZEN_EDITOR = command
        except (json.JSONDecodeError, OSError):
            pass


# Load persisted editor override on module import.
_load_editor()


async def run_editor_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /editor para abrir archivos en el editor preferido.

    Examples:
        /editor <archivo>              — abre el archivo
        /editor <archivo>:<línea>      — abre el archivo en una línea específica
        /editor set <comando>          — establece el editor preferido
        /editor current                — muestra el editor configurado
    """
    text = args.strip()

    if not text:
        render_error("Uso: /editor <archivo>[:línea] | /editor set <comando> | /editor current")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "set":
        command = rest.strip()
        if not command:
            render_error("Uso: /editor set <comando>")
            return
        _set_editor(command)
        console.print(f"[success]✓ Editor configurado: [bold cyan]{command}[/][/]")
        return

    if subcmd == "current":
        editor = _get_editor()
        if editor:
            console.print(f"[info]Editor actual: [bold cyan]{editor}[/][/]")
        else:
            console.print("[warning]⚠ No hay editor configurado. Usa /editor set <comando> o define $EDITOR.[/]")
        return

    # /editor <archivo>[:línea]
    line: int | None = None
    target = text
    if ":" in text and not target.startswith("/"):
        # Split from the last colon to handle Windows paths with drive letters.
        path_part, _, line_part = text.rpartition(":")
        if path_part and line_part.isdigit():
            target = path_part
            line = int(line_part)

    path = Path(target).expanduser()
    if not path.exists():
        render_error(f"Archivo no encontrado: {path}")
        return
    if not path.is_file():
        render_error(f"La ruta no es un archivo: {path}")
        return

    editor = _get_editor()
    if editor is None:
        render_error("No se encontró un editor. Define $EDITOR o usa /editor set <comando>.")
        return

    # Build command line preserving the editor command as a single token if possible.
    cmd = shlex.split(editor)
    if line is not None:
        # Common line-number syntaxes. Try the simplest first; if the editor is known
        # to use a specific flag, use that. Otherwise append +N for vi/vim/nano style.
        editor_base = os.path.basename(cmd[0]).lower() if cmd else ""
        if editor_base in {"code", "code.exe", "code-oss", "code-oss.exe", "cursor", "cursor.exe"}:
            cmd.extend(["--goto", f"{path}:{line}"])
        elif editor_base in {"subl", "subl.exe", "sublime_text", "sublime_text.exe"}:
            cmd.extend([f"{path}:{line}"])
        elif editor_base in {"idea", "idea.exe", "idea64", "idea64.exe"}:
            cmd.extend(["--line", str(line), str(path)])
        elif editor_base in {"atom", "atom.exe"}:
            cmd.extend([f"{path}:{line}"])
        else:
            # vi/vim/nano/emacs fallback: +N
            cmd.append(f"+{line}")
            cmd.append(str(path))
    else:
        cmd.append(str(path))

    try:
        subprocess.Popen(cmd, stdin=None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        render_error(f"Error abriendo el editor: {exc}")
        return

    console.print(f"[success]✓ Abriendo [bold cyan]{path}[/] en {editor}[/]")


# ── /profile (saved agent config profiles) ──────────────────────────────────


_PROFILES_PATH: Path = Path.home() / ".yggdrasil" / "profiles.json"

_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "description": "Rápido y económico",
    },
    "reasoning": {
        "provider": "anthropic",
        "model": "claude-opus-4",
        "description": "Razonamiento profundo (costoso)",
    },
    "local": {
        "provider": "local",
        "model": "local-model",
        "description": "Modelo local, sin costo de API",
    },
}


def _profiles_path() -> Path:
    global _PROFILES_PATH
    if _PROFILES_PATH is None:
        home = Path(os.environ.get("HOME", os.path.expanduser("~")))
        _PROFILES_PATH = home / ".yggdrasil" / "profiles.json"
    return _PROFILES_PATH


def _ensure_profiles() -> Path:
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _PROFILES_PATH.exists():
        _save_profiles(_DEFAULT_PROFILES)
    return _PROFILES_PATH


def _load_profiles() -> dict[str, dict[str, Any]]:
    _ensure_profiles()
    try:
        import json as _json
        data = _json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        # Don't hide corruption: a broken profiles.json silently falling
        # back to defaults makes /profile save look broken on next launch.
        console.print(
            f"[warning]profiles.json corrupto o ilegible ({exc}); "
            f"usando defaults. Borrá {_PROFILES_PATH} para regenerar.[/]"
        )
    return dict(_DEFAULT_PROFILES)


def _save_profiles(profiles: dict[str, dict[str, Any]]) -> None:
    import json as _json
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES_PATH.write_text(
        _json.dumps(profiles, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

_TEST_LAST_FAILED_PATH: Path = Path.home() / ".yggdrasil" / "test_last_failed.json"


def _test_last_failed_path() -> Path:
    return _TEST_LAST_FAILED_PATH


def _set_test_last_failed_path(path: Path) -> None:
    global _TEST_LAST_FAILED_PATH
    _TEST_LAST_FAILED_PATH = path


# ── /test (subprocess pytest runner) ────────────────────────────────────────
# Defaults to the Lilith test suite. Pure subprocess wrapper — does NOT mutate
# the repo. Adds a -k keyword filter and parses the pytest summary line into
# a small dict so the REPL can render a clean overview.

_DEFAULT_TEST_SUITE = "lilith-stack/lilith-cli/tests/"
_PYTEST_SUMMARY_RE = re.compile(
    r"(?P<passed>\d+)\s+passed|"
    r"(?P<failed>\d+)\s+failed|"
    r"(?P<error>\d+)\s+error",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"in\s+(?P<seconds>[0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _parse_pytest_summary(text: str) -> dict[str, Any]:
    """Parse a pytest output blob into a small summary dict.

    Recognises both the canonical ``475 passed in 28.13s`` line and
    per-segment lines like ``3 failed, 1 passed``. Always returns a dict
    with the four numeric keys plus ``duration`` (float seconds) and
    ``last_failure`` (str | None — last ``FAILED`` line if any).

    Args:
        text: Captured stdout/stderr from a pytest subprocess run.

    Returns:
        ``{"passed": int, "failed": int, "error": int, "duration": float,
           "last_failure": str | None}``.
    """
    passed = failed = error = 0
    duration = 0.0
    last_failure: str | None = None

    for raw in (text or "").splitlines():
        line = raw.strip()
        # last failure line is more useful than the count itself
        if line.startswith("FAILED "):
            last_failure = line
        for match in _PYTEST_SUMMARY_RE.finditer(line):
            kind = match.lastgroup
            if not kind:
                continue
            value = int(match.group(kind))
            if kind == "passed":
                passed += value
            elif kind == "failed":
                failed += value
            elif kind == "error":
                error += value
        dur = _DURATION_RE.search(line)
        if dur and "=" not in line.split("in", 1)[0][-1:]:
            # only take the duration when the line looks like a summary,
            # not e.g. ``passed in 0.01s = setup`` (defensive)
            try:
                duration = float(dur.group("seconds"))
            except ValueError:
                pass

    return {
        "passed": passed,
        "failed": failed,
        "error": error,
        "duration": duration,
        "last_failure": last_failure,
    }


def _render_test_summary(summary: dict[str, Any], returncode: int) -> str:
    """Format a parsed pytest summary into a one-line Spanish status."""
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    error = summary.get("error", 0)
    duration = summary.get("duration", 0.0)
    bits: list[str] = []
    if passed:
        bits.append(f"[success]{passed} passed[/success]")
    if failed:
        bits.append(f"[error]{failed} failed[/error]")
    if error:
        bits.append(f"[error]{error} error[/error]")
    if not bits:
        bits.append("[dim]sin resultados[/dim]")
    bits.append(f"[dim]{duration:.2f}s[/dim]")
    if returncode != 0:
        bits.append(f"[warning]exit={returncode}[/warning]")
    line = " · ".join(bits)
    if summary.get("last_failure"):
        line += f"\n  [error]{summary['last_failure']}[/error]"
    return line


def _run_pytest_subprocess(
    target: str,
    *,
    keyword: str | None = None,
    extra_args: list[str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run pytest via subprocess and return a parsed summary dict.

    Args:
        target: Path or node-id passed to pytest (e.g. ``tests/`` or
            ``tests/test_plan.py::test_x``).
        keyword: Optional ``-k`` expression. ``None`` means no filter.
        extra_args: Extra pytest flags (e.g. ``["--maxfail=1"]``).
        cwd: Working directory for the subprocess. ``None`` falls back to
            the Asgard repo root (parent of the ``lilith-stack`` suite).

    Returns:
        ``{"passed": int, "failed": int, "error": int, "duration": float,
           "last_failure": str | None, "returncode": int, "command": list[str]}``.
    """
    if cwd is None:
        # Asgard root sits two levels above lilith-stack/lilith-cli
        cwd = Path(__file__).resolve().parents[3]

    venv_py = cwd / ".venv" / "Scripts" / "python.exe"
    if sys.platform != "win32":
        venv_py = cwd / ".venv" / "bin" / "python"

    cmd: list[str] = [str(venv_py), "-m", "pytest", target, "-q", "--no-header", "--tb=line"]
    if keyword:
        cmd.extend(["-k", keyword])
    if extra_args:
        cmd.extend(extra_args)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError as exc:
        return {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "duration": time.monotonic() - start,
            "last_failure": None,
            "returncode": -1,
            "command": cmd,
            "error": f"pytest no disponible: {exc}",
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "duration": time.monotonic() - start,
            "last_failure": None,
            "returncode": -1,
            "command": cmd,
            "error": "pytest excedió el timeout (600s)",
        }

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    summary = _parse_pytest_summary(output)
    # If pytest didn't print a duration, fall back to our own clock.
    if not summary.get("duration"):
        summary["duration"] = time.monotonic() - start
    summary["returncode"] = proc.returncode
    summary["command"] = cmd
    return summary


def _render_test_usage() -> None:
    """Muestra la ayuda de /test."""
    console.print("\n[bold realm]᛭ Uso de /test[/]")
    console.print(
        "  [cyan]/test[/]                              — corre la suite por defecto "
        f"({_DEFAULT_TEST_SUITE})"
    )
    console.print(
        "  [cyan]/test <ruta>[/]                       — corre pytest sobre la ruta dada"
    )
    console.print(
        "  [cyan]/test -k <expresión>[/]               — filtra por nombre de test"
    )
    console.print(
        "  [cyan]/test <ruta> -k <expresión>[/]        — ruta + filtro combinados"
    )
    console.print(
        "  [cyan]/test last[/]                         — re-corre los tests que fallaron antes"
    )
    console.print("  [cyan]/test --help[/]                       — muestra esta ayuda")
    console.print()


async def run_test_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /test como wrapper directo de pytest.

    Descripción:
        Lanza pytest en un subproceso desde la raíz de Asgard usando el
        intérprete ``.venv/Scripts/python.exe``. Por defecto corre la suite
        de Lilith (``lilith-stack/lilith-cli/tests/``). Imprime un resumen
        corto: passed/failed/error, duración y la última línea ``FAILED``
        si hubo fallos. Nunca modifica archivos del repo.

    Uso:
        /test
        /test <ruta>
        /test -k <expresión>
        /test <ruta> -k <expresión>
        /test last
        /test --help

    Ejemplos:
        /test
        /test lilith-stack/lilith-cli/tests/test_plan.py
        /test -k hello
        /test lilith-stack/lilith-cli/tests/ -k smoke
        /test last
    """
    text = (args or "").strip()

    if text in ("--help", "-h", "help"):
        _render_test_usage()
        return

    # /test last  — re-corre los tests fallidos previos
    if text == "last":
        last_file = _test_last_failed_path()
        if not last_file.exists():
            console.print("[dim]No hay tests fallidos previos.[/dim]")
            return
        try:
            import json as _json

            data = _json.loads(last_file.read_text(encoding="utf-8"))
            failed = data.get("failed", [])
        except Exception as exc:
            console.print(f"[error]Error leyendo historial: {exc}[/error]")
            return
        if not failed:
            console.print("[dim]No hay tests fallidos previos.[/dim]")
            return
        console.print(f"[info]Re-corriendo {len(failed)} tests fallidos...[/info]")
        # convert last-failed list into a single -k expression
        pattern = " or ".join(failed)
        summary = _run_pytest_subprocess(
            _DEFAULT_TEST_SUITE, keyword=pattern
        )
        console.print(_render_test_summary(summary, summary["returncode"]))
        console.print()
        return

    # Split args into a target path and an optional -k keyword.
    target: str = _DEFAULT_TEST_SUITE
    keyword: str | None = None
    tokens = text.split()
    i = 0
    path_tokens: list[str] = []
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-k" and i + 1 < len(tokens):
            keyword = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("-k="):
            keyword = tok.split("=", 1)[1]
            i += 1
            continue
        path_tokens.append(tok)
        i += 1
    if path_tokens:
        target = " ".join(path_tokens)

    summary = _run_pytest_subprocess(target, keyword=keyword)
    if summary.get("error"):
        console.print(f"[error]{summary['error']}[/error]")
        console.print(
            "[dim]tip: verifica que .venv exista y pytest esté instalado[/dim]"
        )
        console.print()
        return
    console.print(_render_test_summary(summary, summary["returncode"]))
    console.print()





# ── /voice (TTS via PowerShell System.Speech on Windows) ───────────────────


def _speak_text(text: str) -> bool:
    """Speak *text* via TTS. Returns True on success, False on failure.

    Falls back to:
    - Windows: PowerShell with System.Speech.Synthesis
    - macOS:  say command
    - Linux:  espeak-ng or spd-say
    """
    import platform
    import subprocess

    text = (text or "").strip()
    if not text:
        return False

    system = platform.system()
    try:
        if system == "Windows":
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "(New-Object System.Speech.Synthesis.SpeechSynthesizer).SpeakTime = 0; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.Speak([Console]::In.ReadToEnd())"
            )
            # Actually just speak the text
            safe = text.replace('"', '`"').replace('$', '`$')
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak(\"{safe}\")"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                timeout=30,
            )
            return True
        elif system == "Darwin":
            subprocess.run(["say", text], capture_output=True, timeout=30)
            return True
        else:
            # Linux: try espeak-ng, fallback to spd-say
            for cmd in (["espeak-ng"], ["espeak"], ["spd-say", "--"]):
                try:
                    subprocess.run(
                        cmd + [text],
                        capture_output=True,
                        timeout=30,
                    )
                    return True
                except FileNotFoundError:
                    continue
            return False
    except Exception:
        return False


async def run_voice_command(session: AgentSession, args: str) -> None:
    """TTS toggle (/voice [on|off|status|test <text>])."""
    text = args.strip()
    state = getattr(session, "_voice_enabled", False)

    if not text or text == "status":
        status = "ON" if state else "OFF"
        console.print(f"[bold]Voice mode:[/] {status}")
        return

    if text == "on":
        session._voice_enabled = True
        console.print("[success]✓ Voice mode activado[/success]")
        # Confirm with TTS
        _speak_text("Voice mode enabled.")
        return

    if text == "off":
        session._voice_enabled = False
        console.print("[warning]✗ Voice mode desactivado[/warning]")
        return

    if text.startswith("test "):
        phrase = text[5:].strip() or "Hola, soy Lilith"
        console.print(f"[info]Reproduciendo: {phrase}[/info]")
        ok = _speak_text(phrase)
        if ok:
            console.print("[success]✓ Audio reproducido[/success]")
        else:
            console.print("[error]No hay motor TTS disponible[/error]")
        return

    # Default: treat whole arg as a phrase and speak
    console.print(f"[info]Reproduciendo: {text}[/info]")
    if _speak_text(text):
        console.print("[success]✓ Audio reproducido[/success]")
    else:
        console.print("[error]No hay motor TTS disponible[/error]")



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


async def run_multi_file_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
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
# ── /release (version bump + CHANGELOG entry + commit) ──────────────────────


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][\w.]+)?$")


def _parse_version(text):
    """Return (major, minor, patch) for a semver-ish string, else None."""
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _format_version(v):
    return str(v[0]) + "." + str(v[1]) + "." + str(v[2])


def _bump_version(current, level):
    major, minor, patch = current
    if level == "major":
        return (major + 1, 0, 0)
    if level == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


_VERSION_LINE_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


def _read_package_version():
    """Read __version__ from lilith_cli/__init__.py."""
    init_path = Path(__file__).resolve().parent / "__init__.py"
    try:
        text = init_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _VERSION_LINE_RE.search(text)
    if not m:
        return None
    return _parse_version(m.group(1))


def _write_package_version(new_version):
    init_path = Path(__file__).resolve().parent / "__init__.py"
    text = init_path.read_text(encoding="utf-8")
    new_text = _VERSION_LINE_RE.sub(
        '__version__ = "' + new_version + '"',
        text,
        count=1,
    )
    init_path.write_text(new_text, encoding="utf-8")


def _prepend_changelog(new_version, today):
    changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    if not changelog.exists():
        return False
    text = changelog.read_text(encoding="utf-8")
    entry = "## [" + new_version + "] - " + today + "\n\n- Bumped version to " + new_version + "\n\n"
    lines = text.splitlines(keepends=True)
    out = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and line.startswith("## ") and i > 0:
            out.insert(-1, entry)
            inserted = True
    if not inserted:
        out.insert(0, entry)
    changelog.write_text("".join(out), encoding="utf-8")
    return True


async def run_release_command(session, args):  # noqa: ARG001
    """Bump version, update CHANGELOG, commit (no push).

    Usage: /release [patch|minor|major] [--dry-run]
    """
    raw = args.strip()
    dry_run = "--dry-run" in raw.split()
    level = next(
        (tok for tok in raw.split() if tok in {"patch", "minor", "major"}),
        "patch",
    )

    current = _read_package_version()
    if current is None:
        console.print("[error]No se pudo leer __version__ desde lilith_cli/__init__.py[/error]")
        return

    new = _bump_version(current, level)
    new_str = _format_version(new)
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    console.print("[info]Versión actual:[/info] " + _format_version(current))
    console.print("[info]Versión nueva:[/info]  " + new_str + " (" + level + ")")
    console.print("[info]Fecha:[/info]         " + today)
    console.print("[info]Dry-run:[/info]       " + ("sí" if dry_run else "no"))
    console.print()

    if dry_run:
        console.print(
            "[dim]DRY-RUN: se actualizaría __init__.py a "
            + new_str
            + " y se antepondría entrada al CHANGELOG[/dim]"
        )
        console.print(
            "[dim]DRY-RUN: se crearía commit 'chore(release): v"
            + new_str
            + "' (no se ejecuta)[/dim]"
        )
        return

    try:
        _write_package_version(new_str)
    except OSError as exc:
        console.print("[error]Error escribiendo __init__.py: " + str(exc) + "[/error]")
        return

    changelog_written = _prepend_changelog(new_str, today)
    if not changelog_written:
        console.print("[warning]⚠ CHANGELOG.md no existe; sólo se actualizó __init__.py[/warning]")

    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        commit = subprocess.run(
            [
                "git",
                "-c",
                "user.email=hermes@nous.local",
                "-c",
                "user.name=Hermes",
                "commit",
                "-m",
                "chore(release): v" + new_str,
            ],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        console.print("[error]git no está disponible en PATH[/error]")
        return

    if commit.returncode != 0:
        err_msg = (commit.stderr or commit.stdout or "").strip()
        console.print("[error]git commit falló: " + err_msg + "[/error]")
        return

    console.print("[success]✓ Released v" + new_str + "[/success]")

# ── /explain command ───────────────────────────


async def run_explain_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
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


async def run_whereami_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
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
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        branch = branch_proc.stdout.strip() or "(detached HEAD)"
        last_proc = _sp.run(
            ["git", "log", "-1", "--oneline"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
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

# ── /lint-fix command ──────────────────────


async def run_lint_fix_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Auto-fix lint issues with ruff or black (/lint-fix [path])."""
    import shutil
    import subprocess

    text = args.strip()
    target = text or "."

    # Try ruff first (faster, more fixes)
    ruff = shutil.which("ruff")
    if ruff:
        console.print(f"[info]Running:[/info] [bold cyan]ruff check --fix {target}[/bold cyan]")
        try:
            proc = subprocess.run(
                [ruff, "check", "--fix", target],
                capture_output=True, text=True, timeout=60,
            )
            if proc.stdout:
                console.print(proc.stdout)
            if proc.stderr:
                console.print(f"[dim]{proc.stderr}[/dim]")
            if proc.returncode == 0:
                console.print("[success]✓ ruff: all issues fixed[/success]")
            else:
                console.print(f"[info]ruff exit {proc.returncode} (some issues unfixable)[/info]")
        except subprocess.TimeoutExpired:
            render_error("ruff timed out after 60s")
        return

    # Fallback to black
    black = shutil.which("black")
    if black:
        console.print(f"[info]Running:[/info] [bold cyan]black {target}[/bold cyan]")
        try:
            proc = subprocess.run(
                [black, target],
                capture_output=True, text=True, timeout=60,
            )
            if proc.stdout:
                console.print(proc.stdout)
            if proc.returncode == 0:
                console.print("[success]✓ black: all files reformatted[/success]")
            else:
                render_error(f"black exited with code {proc.returncode}")
        except subprocess.TimeoutExpired:
            render_error("black timed out after 60s")
        return

    # Neither available
    render_error("Neither ruff nor black is installed. Install with: pip install ruff")

from .doctor import apply_fixes, run_diagnostics
from lilith_tools.env import EnvGetTool, EnvListTool, SysInfoTool



# ── /doctor command ───────────────────────────────────────


async def run_doctor_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Run environment diagnostics (/doctor [--fix] [--json] [--quiet] [--deep])."""
    import json as _json

    tokens = args.split()
    do_fix = "--fix" in tokens
    do_json = "--json" in tokens
    do_quiet = "--quiet" in tokens or "-q" in tokens
    do_deep = "--deep" in tokens

    results = run_diagnostics(session)
    if do_deep:
        if not do_quiet:
            with console.status("[cyan]Running deep diagnostics\u2026[/cyan]", spinner="dots"):
                results.extend(_run_deep_checks(session))
        else:
            results.extend(_run_deep_checks(session))

    if do_json:
        # Machine-readable output for scripting
        # Use sys.stdout (not Rich console) to avoid markup interpretation
        import sys as _sys
        out = _json.dumps(results, indent=2, ensure_ascii=False)
        _sys.stdout.write(out + "\n")
        _sys.stdout.flush()
        return

    if not do_quiet:
        console.print("\n[bold realm]᛭ Doctor[/] — diagnosticando entorno…")

    ok_count = warn_count = error_count = 0
    for r in results:
        status = r["status"]
        check = r["check"]
        message = r["message"]
        if status == "ok":
            mark = "[success]✓[/success]"
            ok_count += 1
        elif status == "warn":
            mark = "[warn]![/warn]"
            warn_count += 1
        else:
            mark = "[error]✗[/error]"
            error_count += 1
        if not do_quiet:
            console.print(f"  {mark} [bold cyan]{check}[/bold cyan]: {message}")

    if not do_quiet:
        console.print(f"\n[info]Resumen:[/info] {ok_count} OK, {warn_count} warnings, {error_count} errors")

    if do_fix and (warn_count or error_count):
        if not do_quiet:
            console.print("\n[info]Aplicando fixes…[/info]")
        fixes = apply_fixes(results)
        for fix in fixes:
            if not do_quiet:
                console.print(f"  [success]✓[/success] {fix}")
    elif (warn_count or error_count) and not do_fix and not do_quiet:
        console.print("[dim]Pasá --fix para intentar reparar los issues detectables.[/dim]")
    if not do_quiet:
        console.print()


def _run_deep_checks(session: AgentSession) -> list[dict]:
    """Run extended diagnostic checks for --deep mode.

    Adds:
    - Disk free space in working directory
    - Active provider latency probe (no LLM call, just round-trip setup)
    - Network connectivity (DNS lookup of api.openai.com)
    - Number of tool calls recorded in current session
    """
    import shutil
    import socket
    import subprocess
    import time

    results: list[dict] = []

    # Disk free space
    try:
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = 100 * usage.used // usage.total
        if free_gb < 1:
            status = "error"
            msg = f"Solo {free_gb:.2f} GB libres de {total_gb:.1f} GB ({used_pct}% usado)"
        elif free_gb < 5:
            status = "warn"
            msg = f"{free_gb:.2f} GB libres de {total_gb:.1f} GB ({used_pct}% usado)"
        else:
            status = "ok"
            msg = f"{free_gb:.2f} GB libres de {total_gb:.1f} GB ({used_pct}% usado)"
        results.append({"check": "Disk space", "status": status, "message": msg})
    except Exception as exc:
        results.append({"check": "Disk space", "status": "warn", "message": f"No se pudo verificar: {exc}"})

    # Network probe
    try:
        start = time.time()
        socket.gethostbyname("api.openai.com")
        latency_ms = int((time.time() - start) * 1000)
        results.append({
            "check": "Network DNS",
            "status": "ok",
            "message": f"DNS resolved api.openai.com en {latency_ms}ms",
        })
    except socket.gaierror as exc:
        results.append({
            "check": "Network DNS",
            "status": "error",
            "message": f"No se pudo resolver api.openai.com: {exc}",
        })
    except Exception as exc:
        results.append({
            "check": "Network DNS",
            "status": "warn",
            "message": f"Error de red: {exc}",
        })

    # Git remote (if in a git repo)
    try:
        proc = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            remote_count = len(proc.stdout.strip().split("\n"))
            results.append({
                "check": "Git remote",
                "status": "ok",
                "message": f"{remote_count} remote(s) configurado(s)",
            })
        else:
            results.append({
                "check": "Git remote",
                "status": "warn",
                "message": "No hay git remote configurado",
            })
    except (subprocess.TimeoutExpired, FileNotFoundError):
        results.append({
            "check": "Git remote",
            "status": "warn",
            "message": "git no disponible o no es un repo",
        })

    # Session tool call count
    try:
        tool_history = getattr(session, "_tool_call_history", None) or []
        count = len(tool_history)
        if count == 0:
            status = "warn"
            msg = "Ninguna herramienta llamada aún en esta sesión"
        elif count > 100:
            status = "warn"
            msg = f"{count} herramientas llamadas (considerá /compact)"
        else:
            status = "ok"
            msg = f"{count} herramientas llamadas en esta sesión"
        results.append({"check": "Session tools", "status": status, "message": msg})
    except Exception as exc:
        results.append({"check": "Session tools", "status": "warn", "message": f"Error: {exc}"})

    return results

# ── /now command ───────────────────────────────────────────


async def run_now_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Show current timestamps (/now [--utc|--local|--unix])."""
    from datetime import datetime, timezone

    tokens = args.split()
    show_unix = "--unix" in tokens
    show_utc = "--utc" in tokens
    show_local = "--local" in tokens or not (show_unix or show_utc)

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()

    if show_local:
        console.print(f"[info]Local:[/info]  [bold cyan]{now_local.strftime('%Y-%m-%d %H:%M:%S %Z (%z)')}[/bold cyan]")
    if show_utc:
        console.print(f"[info]UTC:[/info]    [bold cyan]{now_utc.strftime('%Y-%m-%d %H:%M:%S')}[/bold cyan]")
    if show_unix:
        console.print(f"[info]Unix:[/info]   [bold cyan]{int(now_utc.timestamp())}[/bold cyan]")
    console.print()

# ── /hash command ───────────────────────────────────────────────


async def run_hash_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Compute hashes of text or file (/hash <algo> <text|file>)."""
    import hashlib

    text = args.strip()
    if not text:
        render_error("Uso: /hash <md5|sha1|sha256|sha512> <texto|ruta_archivo>")
        return

    parts = text.split(maxsplit=1)
    algo = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    supported = ("md5", "sha1", "sha256", "sha512")
    if algo not in supported:
        render_error(f"Algoritmo no soportado: {algo}. Use: {', '.join(supported)}")
        return

    if not target:
        render_error("Uso: /hash <algo> <texto|ruta_archivo>")
        return

    # Detect file vs text: try as path first, fall back to literal text
    target_path = Path(target).expanduser()
    if target_path.is_file():
        try:
            data_bytes = target_path.read_bytes()
            source = f"archivo: {target_path}"
        except OSError as exc:
            render_error(f"No se pudo leer {target_path}: {exc}")
            return
    else:
        data_bytes = target.encode("utf-8")
        source = f"texto ({len(data_bytes)} chars)"

    h = hashlib.new(algo)
    h.update(data_bytes)
    digest = h.hexdigest()
    console.print(f"[info]{algo}[/info] [bold cyan]{digest}[/bold cyan]  [dim]({source})[/dim]")
    console.print()

# ── /lines command ───────────────────────────────────────────────────


async def run_lines_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Count lines/words/chars of a file (/lines <path>)."""
    text = args.strip()
    if not text:
        render_error("Uso: /lines <archivo>")
        return

    target = Path(text).expanduser()
    if not target.exists():
        render_error(f"No existe: {target}")
        return
    if not target.is_file():
        render_error(f"No es un archivo: {target}")
        return

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {target}: {exc}")
        return

    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    words = len(content.split())
    chars = len(content)
    bytes_size = target.stat().st_size
    console.print(f"[info]Líneas:[/info] [bold cyan]{lines}[/bold cyan]  [dim]palabras: {words}  chars: {chars}  bytes: {bytes_size}[/dim]  [dim]({target.name})[/dim]")
    console.print()

# ── /base64 command ────────────────────────────────────────────────────


async def run_base64_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Base64 encode or decode text (/base64 <encode|decode> <text>)."""
    import base64

    text = args.strip()
    if not text:
        render_error("Uso: /base64 <encode|decode> <texto>")
        return

    parts = text.split(maxsplit=1)
    op = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    if op not in ("encode", "decode"):
        render_error("Uso: /base64 <encode|decode> <texto>")
        return

    if not target:
        render_error(f"Uso: /base64 {op} <texto>")
        return

    try:
        if op == "encode":
            encoded = base64.b64encode(target.encode("utf-8")).decode("ascii")
            console.print(f"[info]encoded:[/info] [bold cyan]{encoded}[/bold cyan]")
        else:
            try:
                decoded = base64.b64decode(target, validate=True).decode("utf-8")
                console.print(f"[info]decoded:[/info] [bold cyan]{decoded}[/bold cyan]")
            except Exception as exc:
                render_error(f"Base64 inválido: {exc}")
                return
    except Exception as exc:
        render_error(f"Error: {exc}")
        return
    console.print()

# ── /uuid command ───────────────────────────────────────────────────────────


async def run_uuid_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Generate UUIDs (/uuid [N] [--v1|--v4|--v7])."""
    import uuid

    tokens = args.split()
    count = 1
    version = 4

    for tok in tokens:
        if tok.isdigit():
            count = max(1, min(int(tok), 50))
        elif tok == "--v1":
            version = 1
        elif tok == "--v4":
            version = 4
        elif tok == "--v7":
            version = 7

    if count == 1:
        if version == 1:
            new_id = uuid.uuid1()
        elif version == 7:
            new_id = uuid.uuid7()
        else:
            new_id = uuid.uuid4()
        console.print(f"[bold cyan]{new_id}[/bold cyan]  [dim](v{version})[/dim]")
    else:
        console.print(f"[info]{count} UUIDs (v{version}):[/info]")
        for _ in range(count):
            if version == 1:
                new_id = uuid.uuid1()
            elif version == 7:
                new_id = uuid.uuid7()
            else:
                new_id = uuid.uuid4()
            console.print(f"  [bold cyan]{new_id}[/bold cyan]")
    console.print()



# ── /qr command ─────────────────────────────────────────────────────────────────

_QR_LAST_FILE = CONFIG_DIR / "qr_last.json"
_QR_PREFS_FILE = CONFIG_DIR / "qr.json"


def _qr_usage() -> str:
    """Cadena de uso en español para /qr."""
    return (
        "Uso: /qr <texto> [--save <ruta.png>] [--last] "
        "[--error-correction L|M|Q|H] [--box-size N] [--border N] [--help]"
    )


def _load_qr_last() -> dict:
    """Carga el último texto y opciones de QR generados."""
    if not _QR_LAST_FILE.exists():
        return {}
    try:
        data = json.loads(_QR_LAST_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def _save_qr_last(payload: dict) -> None:
    """Persiste el último texto y opciones del QR generado."""
    _QR_LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QR_LAST_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_qr_prefs() -> dict:
    """Carga preferencias persistidas del usuario para /qr."""
    if not _QR_PREFS_FILE.exists():
        return {}
    try:
        data = json.loads(_QR_PREFS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def _save_qr_prefs(prefs: dict) -> None:
    """Persiste preferencias del usuario para /qr."""
    _QR_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QR_PREFS_FILE.write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _qr_resolve_ec(letter: str) -> int:
    """Resuelve el identificador numérico de corrección de errores.

    Se importa ``qrcode.constants`` perezosamente para no penalizar el
    arranque si el usuario nunca invoca /qr.
    """
    import qrcode.constants as _qc  # type: ignore

    return {
        "L": _qc.ERROR_CORRECT_L,
        "M": _qc.ERROR_CORRECT_M,
        "Q": _qc.ERROR_CORRECT_Q,
        "H": _qc.ERROR_CORRECT_H,
    }.get(letter.upper(), _qc.ERROR_CORRECT_M)


def _render_qr_ascii(
    text: str,
    ec: int,
    box_size: int,
    border: int,
) -> str:
    """Genera el ASCII de un código QR para mostrarlo en la terminal."""
    import io

    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=ec,
        box_size=box_size,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, tty=False, invert=False)
    return buf.getvalue()


async def run_qr_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Genera códigos QR en la terminal o como PNG.

    Examples:
        /qr https://example.com
        /qr https://example.com --save qr.png
        /qr --last
        /qr "hola mundo" --error-correction H --box-size 4 --border 2
        /qr --help
    """
    import qrcode.exceptions

    text = args.strip()

    # ── Sin args / ayuda ────────────────────────────────────────
    if not text:
        render_error(_qr_usage())
        return

    if text in ("--help", "-h", "help"):
        console.print(f"[info]{_qr_usage()}[/info]")
        console.print()
        console.print("[dim]Genera un QR en la terminal a partir de <texto>.[/dim]")
        console.print("[dim]--save <ruta.png>  Guarda el QR como PNG en la ruta indicada.[/dim]")
        console.print("[dim]--last             Muestra el último QR generado (persiste entre sesiones).[/dim]")
        console.print("[dim]--error-correction Nivel de corrección: L|M|Q|H (por defecto M).[/dim]")
        console.print("[dim]--box-size         Tamaño de caja para terminal (por defecto 2).[/dim]")
        console.print("[dim]--border           Margen en módulos (por defecto 1 en terminal, 4 en PNG).[/dim]")
        console.print("[dim]Preferencias por defecto se guardan en ~/.yggdrasil/qr.json[/dim]")
        console.print()
        return

    # ── /qr --last ───────────────────────────────────────────────
    if text == "--last" or text.startswith("--last "):
        last = _load_qr_last()
        if not last or not last.get("text"):
            render_error("No hay un QR previo guardado. Usa /qr <texto> primero.")
            return
        previous = str(last.get("text", ""))
        saved_path = last.get("saved_path")
        ec_letter = str(last.get("ec", "M"))
        try:
            box_size = int(last.get("box_size", 2))
            border = int(last.get("border", 1))
        except (TypeError, ValueError):
            box_size, border = 2, 1

        console.print(
            f"[info]Re-renderizando último QR (EC={ec_letter}, texto «{previous}»)[/info]"
        )
        try:
            ec = _qr_resolve_ec(ec_letter)
            rendered = _render_qr_ascii(previous, ec, box_size, border)
        except qrcode.exceptions.DataOverflowError:
            render_error(
                "El último texto guardado es demasiado largo para un QR "
                "con los parámetros actuales."
            )
            return
        except Exception as exc:  # pragma: no cover - defensivo
            render_error(f"Error re-renderizando QR: {exc}")
            return

        console.print(rendered)
        if saved_path:
            console.print(f"[dim]Guardado previamente en: {saved_path}[/dim]")
        console.print()
        return

    # ── Parseo de argumentos ────────────────────────────────────
    import argparse as _argparse
    import shlex as _shlex

    parser = _argparse.ArgumentParser(prog="/qr", add_help=False)
    parser.add_argument("text", nargs="?")
    parser.add_argument("--save", dest="save_path", default=None)
    parser.add_argument(
        "--error-correction",
        dest="ec",
        default=None,
        help="Nivel L|M|Q|H",
    )
    parser.add_argument("--box-size", dest="box_size", type=int, default=None)
    parser.add_argument("--border", dest="border", type=int, default=None)

    try:
        # En Windows conservamos las barras invertidas (posix=False) para no
        # romper rutas tipo ``C:\Users\...\qr.png``.
        tokens = _shlex.split(text, posix=not sys.platform.startswith("win"))
    except ValueError as exc:
        render_error(f"Error parseando argumentos: {exc}")
        return

    try:
        parsed, _unknown = parser.parse_known_args(tokens)
    except SystemExit:
        render_error(_qr_usage())
        return

    if not parsed.text:
        render_error(_qr_usage())
        return

    prefs = _load_qr_prefs()
    ec_letter = (parsed.ec or prefs.get("error_correction") or "M").upper()
    if ec_letter not in ("L", "M", "Q", "H"):
        render_error(
            f"Nivel de corrección inválido: {parsed.ec!r}. Usa L, M, Q o H."
        )
        return
    ec = _qr_resolve_ec(ec_letter)

    if parsed.save_path:
        default_box, default_border = 10, 4
    else:
        default_box, default_border = 2, 1

    box_size = (
        parsed.box_size
        if parsed.box_size is not None
        else int(prefs.get("box_size", default_box))
    )
    border = (
        parsed.border
        if parsed.border is not None
        else int(prefs.get("border", default_border))
    )
    if box_size < 1 or border < 0:
        render_error("--box-size debe ser ≥ 1 y --border ≥ 0")
        return

    # ── Render en terminal ───────────────────────────────────────
    if not parsed.save_path:
        try:
            rendered = _render_qr_ascii(parsed.text, ec, box_size, border)
        except qrcode.exceptions.DataOverflowError:
            render_error(
                "El texto es demasiado largo para caber en un QR con la "
                "corrección de errores y el tamaño de caja actuales. "
                "Prueba con --error-correction L o reduce el texto."
            )
            return
        except Exception as exc:  # pragma: no cover - defensivo
            render_error(f"Error generando QR: {exc}")
            return

        console.print(rendered)
        console.print(
            f"[dim]QR ({len(parsed.text)} caracteres, EC={ec_letter}, "
            f"box={box_size}, border={border})[/dim]"
        )

        _save_qr_last(
            {
                "text": parsed.text,
                "ec": ec_letter,
                "box_size": box_size,
                "border": border,
                "saved_path": None,
                "ts": datetime.now(UTC).isoformat(),
            }
        )
        _save_qr_prefs(
            {
                "error_correction": ec_letter,
                "box_size": box_size,
                "border": border,
            }
        )
        console.print()
        return

    # ── Guardar como PNG ─────────────────────────────────────────
    import qrcode

    save_target = Path(parsed.save_path).expanduser()
    try:
        save_target.parent.mkdir(parents=True, exist_ok=True)
        img = qrcode.make(
            parsed.text,
            error_correction=ec,
            border=border,
            box_size=box_size,
        )
        img.save(str(save_target))
    except (OSError, ValueError) as exc:
        render_error(f"No se pudo guardar el PNG en {save_target}: {exc}")
        return
    except Exception as exc:  # pragma: no cover - defensivo
        render_error(f"Error generando PNG: {exc}")
        return

    console.print(
        f"[success]✓ QR guardado en [bold cyan]{save_target}[/bold cyan][/success]"
    )
    console.print(
        f"[dim]({len(parsed.text)} caracteres, EC={ec_letter}, "
        f"box={box_size}, border={border})[/dim]"
    )

    _save_qr_last(
        {
            "text": parsed.text,
            "ec": ec_letter,
            "box_size": box_size,
            "border": border,
            "saved_path": str(save_target),
            "ts": datetime.now(UTC).isoformat(),
        }
    )
    _save_qr_prefs(
        {
            "error_correction": ec_letter,
            "box_size": box_size,
            "border": border,
        }
    )
    console.print()

# ── /json command ───────────────────────────────────────────────────────────────


async def run_json_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Validate and pretty-print JSON (/json <text|file>)."""
    import json

    text = args.strip()
    if not text:
        render_error("Uso: /json <texto_json|ruta_archivo>")
        return

    # Try as file path first, fall back to literal text
    target = Path(text).expanduser()
    if target.is_file():
        try:
            content = target.read_text(encoding="utf-8")
            source = f"archivo: {target.name}"
        except OSError as exc:
            render_error(f"No se pudo leer {target}: {exc}")
            return
    else:
        content = text
        source = "texto"

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        render_error(f"JSON inválido: {exc}")
        return

    pretty = json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
    type_name = type(parsed).__name__
    if isinstance(parsed, (dict, list)):
        size = len(parsed)
    else:
        size = "?"
    console.print(f"[info]Válido ({source}):[/info] [bold cyan]{type_name}[/bold cyan] [dim]({size} items)[/dim]")
    console.print(pretty)
    console.print()

# ── /reverse command ───────────────────────────────────────────────────────────────


async def run_reverse_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Reverse a string or list lines (/reverse [--lines] <text>)."""
    text = args.strip()
    if not text:
        render_error("Uso: /reverse [--lines] <texto>")
        return

    lines_mode = False
    if text.startswith("--lines "):
        lines_mode = True
        text = text[len("--lines "):].strip()
    elif text == "--lines":
        render_error("Uso: /reverse --lines <texto>")
        return

    if lines_mode:
        lines = text.split("\n")
        reversed_lines = list(reversed(lines))
        result = "\n".join(reversed_lines)
        console.print(f"[info]Líneas invertidas ({len(lines)}):[/info]")
    else:
        result = text[::-1]
        console.print(f"[info]Reverso:[/info]")

    console.print(f"[bold cyan]{result}[/bold cyan]")
    console.print()


# ── /conclave command ─────────────────────────────────────────────────────


async def run_conclave_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Fan-out the same question across 2-4 Hlidskjalf presets (/conclave).

    Examples:
        /conclave ¿Qué motor usa Lilith?
        /conclave "¿Qué motor usa Lilith?" --presets investigador-minimax,grok-research
        /conclave "resume X" --presets a,b --structured --max-tokens 1024 --timeout 30

    Behaviour:
        * Delegates to :class:`lilith_tools.conclave.ConclaveTool`, which
          already implements the parallel fan-out + per-preset 60s
          timeout contract.
        * Renders a Rich panel per preset with model, content (truncated
          to ~15 lines), and the per-preset error (if any). A failing
          preset never takes down the rest.
        * The conclave call is sync internally (it spins its own
          ``asyncio.run`` per preset), so we run it through
          ``loop.run_in_executor`` to avoid nested event loops.
    """
    import asyncio as _asyncio

    text = args.strip()
    if not text:
        render_error(
            "Uso: /conclave <pregunta> "
            "[--presets a,b,c] [--structured] [--max-tokens N] [--timeout N]"
        )
        return

    presets: list[str] | None = None
    structured = False
    max_tokens: int | None = None
    per_timeout: float | None = None

    import shlex as _shlex
    try:
        tokens = _shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    filtered: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--presets" and i + 1 < len(tokens):
            presets = [
                p.strip() for p in tokens[i + 1].split(",") if p.strip()
            ]
            i += 2
            continue
        if tok == "--structured":
            structured = True
            i += 1
            continue
        if tok == "--max-tokens" and i + 1 < len(tokens):
            try:
                max_tokens = int(tokens[i + 1])
            except ValueError:
                render_error("--max-tokens requiere un entero")
                return
            i += 2
            continue
        if tok == "--timeout" and i + 1 < len(tokens):
            try:
                per_timeout = float(tokens[i + 1])
            except ValueError:
                render_error("--timeout requiere un número")
                return
            i += 2
            continue
        filtered.append(tok)
        i += 1

    question = " ".join(filtered).strip()
    if not question:
        render_error("La pregunta no puede estar vacía.")
        return

    try:
        from lilith_tools.conclave import ConclaveTool  # type: ignore[import-not-found]
    except Exception as exc:
        render_error(f"No se pudo cargar la tool 'conclave': {exc}")
        return

    def _invoke() -> Any:
        kwargs: dict[str, Any] = {
            "question": question,
            "structured": structured,
        }
        if presets is not None:
            kwargs["presets"] = presets
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if per_timeout is not None:
            kwargs["timeout"] = per_timeout
        return ConclaveTool().execute(**kwargs)

    loop = _asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        # The REPL must survive a per-command crash. Surface the exception
        # as a synthetic ToolResult so the renderer can describe it.
        from lilith_tools.base import ToolResult  # type: ignore[import-not-found]

        result = ToolResult(
            success=False,
            data=None,
            error=f"conclave raised: {type(exc).__name__}: {exc}",
        )

    _render_conclave_panel(question, presets, result)


def _render_conclave_panel(
    question: str,
    presets: list[str] | None,
    result: Any,
) -> None:
    """Pretty-print the ConclaveTool result as one panel per preset."""
    from rich.panel import Panel
    from rich.text import Text

    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        render_error(
            f"conclave devolvió resultado inesperado: {type(result).__name__}"
        )
        return

    requested = data.get("presets_requested") or presets or []
    responses = data.get("responses") or []
    ok = data.get("ok_count", 0)
    failed = data.get("failed_count", 0)

    header_status = (
        "[success]OK[/success]" if getattr(result, "success", False)
        else "[error]FALLO[/error]"
    )
    console.print(
        f"\n[bold realm]᛭ Conclave[/] {header_status} · "
        f"pregunta: [italic]{question!r}[/italic] · "
        f"presets={len(requested)} · ok={ok} fallaron={failed}"
    )
    if getattr(result, "error", "") and not getattr(result, "success", False):
        console.print(f"  [error]{result.error}[/error]")

    if not responses:
        console.print("  [dim](sin respuestas)[/dim]\n")
        return

    for row in responses:
        preset_name = row.get("preset", "?")
        model = row.get("model") or "?"
        content = row.get("content") or ""
        error = row.get("error") or ""
        usage = row.get("usage") or {}

        title_parts = [f"preset={preset_name}", f"model={model}"]
        if usage.get("total_tokens"):
            title_parts.append(f"tokens={usage['total_tokens']}")
        title = " · ".join(title_parts)

        body = Text()
        if error:
            body.append(f"ERROR: {error}\n", style="bold red")
            body.append("(este preset no tumba al resto)\n", style="dim")
        if content:
            body.append(_truncate_content(content, max_lines=15))
        else:
            body.append("(sin contenido)", style="dim")

        style = "red" if error else "cyan"
        console.print(Panel(body, title=title, border_style=style, expand=True))

    console.print()


def _truncate_content(content: str, *, max_lines: int = 15) -> str:
    """Return *content* truncated to at most *max_lines* lines."""
    lines_in = content.splitlines()
    if len(lines_in) <= max_lines:
        return content
    head = lines_in[:max_lines]
    hidden = len(lines_in) - max_lines
    return "\n".join(head) + f"\n[dim]… (+{hidden} líneas más)[/dim]"


async def run_help_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Show available commands grouped by category (/help [category])."""
    from rich.table import Table

    # Command catalog grouped by category
    catalog: dict[str, list[tuple[str, str]]] = {
        "Session": [
            ("clear", "Limpiar historial"),
            ("compact", "Resumir historial [--dry-run --force --keep-last N]"),
            ("history", "Ver historial [--tool <name>]"),
            ("status", "Estado de la sesión con colores por uso"),
            ("undo", "Deshacer última operación"),
            ("redo", "Rehacer"),
            ("save", "Guardar conversación"),
            ("export", "Exportar [--format json|md --output path]"),
            ("bookmark", "Marcadores de conversación [list|go|delete|rename|search]"),
            ("copy", "Copiar al portapapeles"),
            ("quit", "Salir"),
            ("log", "Resumen de sesión [stats|clear|path|N]"),
            ("capture", "Transcripción Markdown [--output <ruta> | --include-tools | --no-usage | --tags <tags> | --exclude-system | --first N | --last N]"),
            ("load", "Restaurar conversación exportada"),
            ("continue", "Reanudar conversación guardada"),
            ("last-tool", "Detalles de la última tool call"),
            ("pin", "Fijar mensaje al contexto siempre visible"),
            ("cls", "Limpiar la pantalla del terminal (sin tocar historial)"),
            ("recent", "Archivos editados recientemente [N | clear]"),
        ],
        "Configuration": [
            ("config", "Configuración actual"),
            ("model", "Mostrar/cambiar modelo"),
            ("provider", "Mostrar/cambiar proveedor"),
            ("theme", "Cambiar tema visual [name | current | preview <name> | list]"),
            ("tools", "Habilitar/deshabilitar herramientas"),
            ("profile", "Perfiles de configuración [list|save|show|load|delete]"),
        ],
        "Development": [
            ("plan", "Plan numerado de tareas"),
            ("init", "Inicializar proyecto"),
            ("file", "Operaciones de archivo"),
            ("macro", "Grabar/ejecutar macros"),
            ("template", "Plantillas de prompts"),
            ("lint", "Linting"),
            ("lint-fix", "Auto-fix lints con ruff/black"),
            ("test", "Test runner rápido [path|pattern|last]"),
            ("fork", "Fork de la sesión actual"),
            ("agent", "Subagent asíncrono"),
            ("auto", "Modo autónomo"),
            ("todos", "Lista de TODOs persistente"),
            ("review", "Pre-commit review con quality gates"),
            ("stream", "Estado del streaming de tokens"),
            ("json-mode", "Toggle modo JSON forzado"),
            ("watch", "Re-run de un comando al cambiar archivos"),
            ("editor", "Abrir editor externo para el último patch"),
            ("explain", "Explicar el último comando o tool call"),
        ],
        "Information": [
                    ("cost", "Costo estimado"),
                    ("tokens", "Uso de tokens"),
                    ("usage", "Estadísticas detalladas"),
                    ("metrics", "Métricas agregadas"),
                    ("whereami", "Contexto del proyecto (panel)"),
                    ("status", "Estado de la sesión"),
                    ("doctor", "Diagnóstico [--fix --deep --json]"),
                    ("deps", "Dependencias del proyecto [path|outdated|licenses|help]"),
                    ("now", "Timestamp actual"),
                    ("state", "Plan de orquestación persistente [show|clear]"),
                    ("costs", "Telemetría de delegaciones por preset [reset]"),
                    ("skills", "Catálogo de skills de delegación [show|save|delete <name>]"),
                    ("learn", "Minar post-mortems de delegación y sugerir skills [save N]"),
                ],
        "Files & Git": [
            ("git", "Operaciones git"),
            ("diff", "Diff (legacy)"),
            ("diff-config", "Diff de configuración"),
            ("diff-staged", "Cambios preparadas en git [stats | <archivo>]"),
            ("diff-unstaged", "Cambios sin preparar en git [stats | <archivo>]"),
            ("tree", "Árbol de archivos"),
            ("multi-file", "Edit multi-archivo atómico"),
            ("hooks", "Listar/instalar/desinstalar hooks de git"),
            ("release", "Tag + changelog + push de release"),
        ],
        "Utilities": [
            ("hash", "MD5/SHA de texto/archivo"),
            ("uuid", "Generar UUIDs v1/v4/v7"),
            ("qr", "Generar códigos QR [--save ruta.png --last]"),
            ("json", "Validar/pretty-print JSON"),
            ("base64", "Encode/decode base64"),
            ("lines", "Contar líneas de archivo"),
            ("reverse", "Invertir texto o líneas"),
            ("alias", "Aliases [set|get|remove|list]"),
            ("tip", "Tips de Lilith [N|list|add|count]"),
            ("compare", "Comparar archivos [files|json|text] <a> <b> | recent <modo>"),
            ("search", "Buscar en historial o archivos [history|files <patrón>]"),
            ("snippet", "Guardar/ejecutar snippets reutilizables"),
            ("model-info", "Detalles del modelo activo (precio, contexto, alias)"),
            ("bench", "Medir latencias del proveedor [N iteraciones]"),
            ("redact", "Redactar secretos antes de copiar al portapapeles"),
            ("voice", "TTS toggle [on|off|status|test <texto>]"),
            ("lint-fix", "Auto-fix con ruff/black"),
        ],
        "Environment": [
            ("env", "Variables de entorno [NAME | prefix X | snapshot | diff | info | --json]"),
            ("secret", "Secretos de sesión"),
        ],
        "System": [
                    ("bifrost", "Estado IPC entre agentes"),
                    ("ygg", "Contexto Yggdrasil"),
                    ("feedback", "Enviar feedback"),
                    ("recap", "Resumen de turnos"),
                    ("summary", "Resumen conciso de la conversación"),
                    ("replay", "Reproducir interacción"),
                    ("subagents", "Listar y probar presets de sub-agentes [test]"),
                    ("conclave", "Fan-out de pregunta a 2-4 presets en paralelo [--presets a,b,c]"),
                    ("mcp", "Servidores MCP montados en el REPL [list|reload]"),
                ],
        "Help": [
            ("help", "Este comando"),
            ("quickstart", "Tour de 30 segundos"),
            ("tour", "Tour completo"),
            ("commands", "Lista plana de comandos"),
            ("tools", "Lista de herramientas"),
            ("changelog", "Historial [--list | <version>]"),
        ],
    }

    text = args.strip().lower()

    if text:
        # Filter by category
        matches = {k: v for k, v in catalog.items() if k.lower() == text or text in k.lower()}
        if not matches:
            available = ", ".join(sorted(catalog.keys()))
            render_error(f"Categoría desconocida: {text}. Disponibles: {available}")
            return
        catalog = matches

    table = Table(
        title="[bold realm]᛭ Comandos de Lilith[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=True,
        caption=f"[dim]{sum(len(v) for v in catalog.values())} comandos en {len(catalog)} categorías[/dim]",
    )
    table.add_column("Comando", style="bold cyan", no_wrap=True)
    table.add_column("Descripción", style="white")

    for category in sorted(catalog.keys()):
        # Add category separator row
        table.add_row(f"[bold magenta]{category}[/]", "")
        for cmd_name, desc in catalog[category]:
            table.add_row(f"  /{cmd_name}", desc)

    console.print(table)
    console.print()
"""Source for /deps slash command block. Appended to extra_commands.py by _deps_section.py."""
import re
import shutil
import subprocess
from pathlib import Path


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
    for m in re.finditer(r"\[\[package\]\][\s\S]*?(?=\[\[package\]\]|\[\[|$)", text, re.MULTILINE):
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
                capture_output=True, text=True, timeout=8,
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


async def run_deps_command(session: AgentSession, args: str) -> None:
    """Manage project dependencies from common manifests.

    Usage:
        /deps [path]            -> Show deps from pyproject/requirements/package.json
        /deps outdated [path]   -> Best-effort newer version check (Python pkgs)
        /deps licenses [path]   -> List licenses from uv.lock when present
        /deps help              -> Show usage
    """
    args_clean = (args or "").strip()
    tokens = args_clean.split()
    if not tokens:
        target = Path.cwd()
        deps, licenses = _deps_collect(target)
        console.print(f"[info]\u16ed Dependencias en:[/info] [bold cyan]{target}[/bold cyan]")
        _deps_render_table(deps, licenses)
        return

    sub = tokens[0].lower()
    rest = tokens[1:]

    if sub in ("help", "--help", "-h", "?"):
        console.print("[bold realm]\u16ed /deps — Gestión de dependencias[/]")
        console.print()
        console.print("  [bold cyan]/deps [path][/bold cyan]            → Lista dependencias detectadas")
        console.print("  [bold cyan]/deps outdated [path][/bold cyan]   → Chequeo de versiones más recientes")
        console.print("  [bold cyan]/deps licenses [path][/bold cyan]   → Licencias desde uv.lock")
        console.print("  [bold cyan]/deps help[/bold cyan]              → Esta ayuda")
        console.print()
        console.print(
            "  [dim]Manifiestos soportados:[/dim] [green]pyproject.toml[/], [green]requirements.txt[/], [green]package.json[/]"
        )
        console.print("  [dim]Bloqueo de licencias:[/dim] [green]uv.lock[/]")
        console.print()
        return

    if sub == "outdated":
        target = Path(rest[0]).expanduser().resolve() if rest else Path.cwd()
        if not target.exists() or not target.is_dir():
            render_error(f"Ruta no encontrada o no es directorio: {target}")
            return
        deps, _ = _deps_collect(target)
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
            render_error("No se encontró uv.lock para licencias")
            return
        console.print(f"[info]\u16ed Licencias en:[/info] [bold cyan]{target}[/bold cyan]")
        _deps_render_licenses(licenses)
        return

    target = Path(sub).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        render_error(f"Ruta no encontrada o no es directorio: {sub}")
        return
    deps, licenses = _deps_collect(target)
    console.print(f"[info]\u16ed Dependencias en:[/info] [bold cyan]{target}[/bold cyan]")
    _deps_render_table(deps, licenses)


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


async def run_compare_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
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


def _compare_recent_paths(session: AgentSession, count: int = 2) -> list[str]:
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


# ── /log command ───────────────────────────────────────────────────────
#
# Muestra un resumen paginado de la sesión activa, distinto a /history.
# Incluye cabecera con metadatos de sesión, una línea de tiempo compacta
# por turno y conteos agregados (turnos del usuario / asistente, llamadas
# a herramientas con desglose por nombre, errores). Subcomandos: stats,
# clear (archivo de log en disco, no session.history), help, path.

_LOG_FILE = CONFIG_DIR / "session.log"


def _clear_log_file() -> bool:
    """Borra el archivo de log en disco si existe. Devuelve True si borró algo.

    Esta función es deliberadamente no destructiva: si el archivo no
    existe, devuelve False y el caller debe emitir un mensaje amable.
    """
    try:
        if _LOG_FILE.exists():
            _LOG_FILE.unlink()
            return True
    except OSError as exc:  # pragma: no cover — defensivo
        logger = logging.getLogger(__name__)
        logger.warning("No se pudo borrar el log: %s", exc)
    return False


def _append_log_entry(entry: dict[str, Any]) -> None:
    """Añade una entrada al log persistente en disco (best-effort).

    No se usa en el flujo principal de /log; queda como gancho para
    que otros comandos (o hooks) puedan registrar turnos sin tocar
    session.history. Si el archivo no se puede escribir, no rompe.
    """
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:  # pragma: no cover — defensivo
        logger = logging.getLogger(__name__)
        logger.warning("No se pudo escribir en el log: %s", exc)


async def run_log_command(session: AgentSession, args: str) -> None:
    """Ejecuta /log para mostrar un resumen paginado de la sesión activa.

    A diferencia de /history, /log ofrece una vista agregada de la sesión
    con cabecera de metadatos, una línea de tiempo compacta por turno y
    conteos agregados (turnos del usuario / asistente, llamadas a
    herramientas con desglose por nombre, errores detectados).

    Subcomandos disponibles:

    - (sin args) o ``N`` entero → muestra los últimos 20 (o ``N``) turnos
      seguidos de la cabecera de conteos.
    - ``stats`` → muestra sólo el panel de conteos agregados.
    - ``clear`` → borra el archivo de log en disco (``session.log``).
      No toca ``session.history``.
    - ``help``  → imprime esta ayuda en español.
    - ``path``  → imprime la ruta absoluta del archivo de log.

    Args:
        session: La sesión activa del agente (provee ``history``,
            ``_tool_call_history``, ``config``, ``_session_start``).
        args: Texto crudo con los argumentos del usuario.

    Examples:
        /log
        /log 50
        /log stats
        /log clear
        /log help
        /log path
    """
    text = (args or "").strip()

    # ── Subcomandos sin historial ────────────────────────────────────────
    if text.lower() in ("help", "--help", "-h", "?"):
        console.print("[bold realm]᛭ /log[/bold realm] [dim]— resumen de sesión[/dim]")
        console.print()
        console.print("[bold]Uso:[/bold]")
        console.print("  /log              [dim]# últimos 20 turnos + conteos[/dim]")
        console.print("  /log N            [dim]# últimos N turnos + conteos[/dim]")
        console.print("  /log stats        [dim]# sólo panel de conteos agregados[/dim]")
        console.print("  /log clear        [dim]# borra el log en disco[/dim]")
        console.print("  /log path         [dim]# ruta absoluta del log[/dim]")
        console.print("  /log help         [dim]# esta ayuda[/dim]")
        console.print()
        console.print("[dim]El log persistido vive en:[/dim]")
        console.print(f"  [dim]{_LOG_FILE}[/dim]")
        return

    if text.lower() == "path":
        console.print(f"[info]Ruta del log de sesión:[/info] {_LOG_FILE}")
        return

    if text.lower() == "clear":
        if _clear_log_file():
            console.print("[success]✓ Log de sesión borrado.[/success]")
        else:
            console.print(
                "[dim]No hay archivo de log para borrar (nada que limpiar).[/dim]"
            )
        return

    # ── Determinar límite de la línea de tiempo ──────────────────────────
    stats_only = False
    limit = 20

    if text:
        tokens = text.split()
        first = tokens[0].lower()
        if first == "stats":
            stats_only = True
        elif first.isdigit():
            limit = int(first)
            if limit < 1:
                render_error("Uso: /log [N] [stats|clear|help|path] (N >= 1)")
                return
        else:
            render_error(
                f"Subcomando desconocido: {first!r}. Use: /log [N] [stats|clear|help|path]"
            )
            return

    # ── Recolectar datos de la sesión ──────────────────────────────────
    history = getattr(session, "history", None) or []
    tool_history: list[dict[str, Any]] = (
        getattr(session, "_tool_call_history", None) or []
    )

    # Inicio de sesión: intenta _session_start, _started_at, mtime del log,
    # y finalmente ahora.
    started_at: datetime = datetime.now()
    for attr in ("_session_start", "_started_at"):
        candidate = getattr(session, attr, None)
        if candidate is not None:
            started_at = (
                candidate if isinstance(candidate, datetime) else started_at
            )
            break
    if _LOG_FILE.exists():
        try:
            mtime = datetime.fromtimestamp(_LOG_FILE.stat().st_mtime)
            if mtime < started_at:
                started_at = mtime
        except OSError:  # pragma: no cover
            pass

    model = getattr(getattr(session, "config", None), "model", "?")
    provider = getattr(getattr(session, "config", None), "provider", "?")

    # Conteos agregados.
    user_turns = sum(1 for m in history if m.get("role") == "user")
    assistant_turns = sum(1 for m in history if m.get("role") == "assistant")
    tool_messages = [m for m in history if m.get("role") == "tool"]
    error_count = 0
    for m in tool_messages:
        content = str(m.get("content", ""))
        if not content:
            continue
        lowered = content.lower()
        if (
            content.startswith("Error")
            or content.startswith("Traceback")
            or "traceback" in lowered[:200]
            or ("error" in lowered[:200] and len(content) > 200)
        ):
            error_count += 1

    tool_call_total = len(tool_history)
    tool_breakdown: dict[str, int] = {}
    for entry in tool_history:
        name = entry.get("name", "?") if isinstance(entry, dict) else "?"
        tool_breakdown[name] = tool_breakdown.get(name, 0) + 1

    total_turns = len(history)
    total_tool_calls = tool_call_total

    # ── Cabecera de metadatos ───────────────────────────────────────
    console.print("[bold realm]᛭ Sesión[/bold realm] [dim]— resumen /log[/dim]")
    console.print(
        f"  [dim]Inicio:[/dim]    {started_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    console.print(f"  [dim]Modelo:[/dim]    [model]{model}[/model]")
    console.print(f"  [dim]Proveedor:[/dim] [model]{provider}[/model]")
    console.print(f"  [dim]Turnos totales:[/dim]       {total_turns}")
    console.print(f"  [dim]Llamadas a herramientas:[/dim] {total_tool_calls}")

    # ── Panel de conteos agregados ─────────────────────────────────────
    console.print()
    console.print("[bold cyan]Conteos agregados[/bold cyan]")
    console.print(f"  [green]❯[/green] Turnos de usuario:    {user_turns}")
    console.print(f"  [blue]○[/blue] Turnos de asistente:  {assistant_turns}")
    console.print(f"  [magenta]⚒[/magenta] Mensajes de tool:    {len(tool_messages)}")
    console.print(f"  [red]✗[/red] Errores detectados:   {error_count}")
    if tool_breakdown:
        # Desglose por nombre de herramienta, ordenado por frecuencia.
        console.print("  [dim]Desglose de herramientas:[/dim]")
        for name, count in sorted(
            tool_breakdown.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            console.print(f"    [bold cyan]{name}[/bold cyan]: {count}")

    if stats_only:
        return

    # ── Línea de tiempo compacta ────────────────────────────────────────
    if not history:
        console.print()
        console.print("[dim]No hay turnos para mostrar en la línea de tiempo.[/dim]")
        return

    console.print()
    console.print(f"[bold cyan]Línea de tiempo[/bold cyan] [dim](últimos {limit})[/dim]")

    role_icons = {
        "user": ("❯", "green"),
        "assistant": ("○", "blue"),
        "system": ("⚙", "yellow"),
        "tool": ("⚒", "magenta"),
        "function": ("∫", "cyan"),
        "error": ("✗", "red"),
    }

    # Las primeras N desde el final = últimos N turnos.
    recent = history[-limit:]
    start_index = total_turns - len(recent) + 1
    for offset, msg in enumerate(recent):
        role = msg.get("role", "?")
        icon, color = role_icons.get(role, ("•", "white"))
        raw_content = msg.get("content", "")
        if not isinstance(raw_content, str):
            raw_content = str(raw_content)
        # Limpiar markup de Rich y saltos de línea.
        preview = raw_content.replace("\n", " ").replace("\r", " ").strip()
        # Quitar etiquetas tipo [dim]...[/dim] de forma simple.
        preview = re.sub(r"\[[^\]]{1,40}\]", "", preview)
        if len(preview) > 80:
            preview = preview[:80] + "…"
        ts = _format_history_timestamp(msg.get("timestamp"))
        turn_no = start_index + offset
        console.print(
            f"  [dim]{turn_no:>3}.[/dim] [dim]{ts}[/dim] "
            f"[{color}]{icon} {role}[/{color}] {preview}"
        )

"""Visual upgrades for /metrics, /tokens and /usage slash commands.

Added in 2026-07-11 round N. Replicates the Rich Panel+Table.grid pattern
used by /whereami, /system_info and /cost. Migrated from legacy classes
MetricsCommand / TokensCommand / UsageCommand in commands.py to async
run_X_command functions so the dispatcher in repl.py picks them up
BEFORE the registry.dispatch fallback (which still works but is
considered legacy-only).
"""


# ── Helpers ────────────────────────────────────────────────────────────


def _usage_color(value: int) -> str:
    """Color a token count by usage tier (green < 4k < yellow < 16k < red)."""
    if value < 4000:
        return "green"
    if value < 16000:
        return "yellow"
    return "red"


def _format_duration_short(seconds: float) -> str:
    """Human-readable duration like 3m 14s or 42s."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


def _tool_metrics(session) -> tuple[dict[str, int], dict[str, float], int]:
    """Aggregate tool call counts and average duration from session history.

    Returns (counts, averages, total). If the session was never wired with
    telemetry tracking (``_tool_call_history`` attribute missing), returns
    empty dicts + 0 — caller can detect this via ``_telemetry_status``.
    """
    history = getattr(session, "_tool_call_history", None)
    if history is None:
        return {}, {}, 0
    counts: dict[str, int] = {}
    durations: dict[str, list[float]] = {}
    total = 0
    for entry in history:
        name = entry.get("name")
        if not name:
            continue
        total += 1
        counts[name] = counts.get(name, 0) + 1
        durations.setdefault(name, []).append(entry.get("duration", 0.0))
    avg: dict[str, float] = {
        name: sum(durs) / len(durs) if durs else 0.0
        for name, durs in durations.items()
    }
    return counts, avg, total


def _command_metrics(session) -> dict[str, int]:
    """Count slash command invocations from session history."""
    history = getattr(session, "_command_history", None)
    if history is None:
        return {}
    counts: dict[str, int] = {}
    for entry in history:
        name = entry.get("name")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _file_edit_metrics(session) -> dict[str, int]:
    """Count file edits per path from session history."""
    history = getattr(session, "_file_edit_history", None)
    if history is None:
        return {}
    counts: dict[str, int] = {}
    for entry in history:
        path = entry.get("path")
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def _telemetry_status(session) -> dict[str, bool]:
    """Which telemetry lists are active on the session.

    Lets the renderer distinguish 'telemetry off in this session' from
    'no events recorded yet' — the latter is a normal empty state,
    the former is an actionable hint.
    """
    return {
        "tools": hasattr(session, "_tool_call_history"),
        "commands": hasattr(session, "_command_history"),
        "files": hasattr(session, "_file_edit_history"),
    }


# ── /tokens ─────────────────────────────────────────────────────────────


async def run_tokens_command(session, args: str) -> None:  # noqa: ARG001
    """Show session token usage in a colored grid panel (/tokens).

    Same data as the legacy TokensCommand — prompt / completion / total —
    but rendered as a Rich Panel with color-coded values (green / yellow / red).
    """
    from rich.panel import Panel
    from rich.table import Table

    usage = session.total_usage
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")
    grid.add_row(
        "Prompt",
        f"[{_usage_color(prompt)}]{prompt:,}[/{_usage_color(prompt)}]",
    )
    grid.add_row(
        "Completion",
        f"[{_usage_color(completion)}]{completion:,}[/{_usage_color(completion)}]",
    )
    grid.add_row(
        "Total",
        f"[bold {_usage_color(total)}]{total:,}[/bold {_usage_color(total)}]",
    )

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Tokens de la sesión[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print()


# ── /metrics ────────────────────────────────────────────────────────────


async def run_metrics_command(session, args: str) -> None:
    """Aggregate session metrics: tokens + tool calls + commands + file edits.

    Subcommands (same as legacy MetricsCommand):
        /metrics               — full summary panel
        /metrics tools         — tool breakdown (Table)
        /metrics commands      — most-used slash commands (Table)
        /metrics files         — most-edited files (Table)
        /metrics json          — machine-readable JSON
        /metrics all           — alias for no-subcommand
    """
    import json
    import sys

    from rich.panel import Panel
    from rich.table import Table

    subcmd = args.strip().lower()
    if subcmd == "json":
        _metrics_emit_json(session, sys.stdout)
        return

    if not subcmd or subcmd == "all":
        await _metrics_show_summary(session)
        return

    if subcmd == "tools":
        _metrics_show_tools(session)
        return

    if subcmd == "commands":
        _metrics_show_commands(session)
        return

    if subcmd == "files":
        _metrics_show_files(session)
        return

    render_error(
        "Uso: /metrics [tools|commands|files|json|all] — muestra métricas de la sesión",
    )


async def _metrics_show_summary(session) -> None:
    """Top-level summary: tokens, top tools, top commands, top files."""
    from rich.panel import Panel
    from rich.table import Table

    usage = session.total_usage
    counts, avg, total = _tool_metrics(session)
    cmd_counts = _command_metrics(session)
    file_counts = _file_edit_metrics(session)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    # Tokens
    grid.add_row(
        "[bold frost]Tokens[/]",
        f"prompt [cyan]{usage.get('prompt_tokens', 0):,}[/] · "
        f"completion [cyan]{usage.get('completion_tokens', 0):,}[/] · "
        f"total [{_usage_color(usage.get('total_tokens', 0))}]"
        f"{usage.get('total_tokens', 0):,}[/{_usage_color(usage.get('total_tokens', 0))}]",
    )

    # Tool calls
    if counts:
        top_tools = sorted(counts.items(), key=lambda x: -x[1])[:5]
        tool_lines = ", ".join(
            f"[tool.name]{name}[/]: {cnt} (avg {_fmt_secs(avg[name])})"
            for name, cnt in top_tools
        )
    else:
        status = _telemetry_status(session)
        if not status["tools"]:
            tool_lines = "[info](telémetría no activa en esta sesión)[/]"
        else:
            tool_lines = "[dim](ninguna)[/]"
    grid.add_row(f"[bold frost]Herramientas[/] ({total})", tool_lines)

    # Slash commands
    if cmd_counts:
        top_cmds = sorted(cmd_counts.items(), key=lambda x: -x[1])[:5]
        cmd_lines = ", ".join(
            f"[tool.name]/{name}[/]: {cnt}" for name, cnt in top_cmds
        )
    else:
        status = _telemetry_status(session)
        if not status["commands"]:
            cmd_lines = "[info](telémetría no activa en esta sesión)[/]"
        else:
            cmd_lines = "[dim](ninguno)[/]"
    grid.add_row("[bold frost]Comandos[/]", cmd_lines)

    # File edits
    if file_counts:
        top_files = sorted(file_counts.items(), key=lambda x: -x[1])[:5]
        file_lines = ", ".join(
            f"[tool.name]{path}[/]: {cnt}" for path, cnt in top_files
        )
    else:
        status = _telemetry_status(session)
        if not status["files"]:
            file_lines = "[info](telémetría no activa en esta sesión)[/]"
        else:
            file_lines = "[dim](ninguno)[/]"
    grid.add_row("[bold frost]Archivos editados[/]", file_lines)

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Métricas de la sesión[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print()


def _metrics_show_tools(session) -> None:
    """Detailed tool call breakdown (Table)."""
    from rich.table import Table

    counts, avg, total = _tool_metrics(session)
    console.print()
    console.print(f"[bold realm]᛭ Métricas de herramientas[/] [dim]({total} llamadas)[/dim]")
    if not counts:
        console.print("  [dim](ninguna)[/]")
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=False,
    )
    table.add_column("Herramienta", style="tool.name")
    table.add_column("Llamadas", justify="right")
    table.add_column("Duración promedio", justify="right")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(name, str(count), _fmt_secs(avg[name]))
    console.print(table)
    console.print()


def _metrics_show_commands(session) -> None:
    """Detailed slash-command breakdown (Table)."""
    from rich.table import Table

    counts = _command_metrics(session)
    console.print()
    console.print("[bold realm]᛭ Métricas de comandos[/]")
    if not counts:
        console.print("  [dim](ninguno)[/]")
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=False,
    )
    table.add_column("Comando", style="tool.name")
    table.add_column("Usos", justify="right")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(f"/{name}", str(count))
    console.print(table)
    console.print()


def _metrics_show_files(session) -> None:
    """Detailed file-edit breakdown (Table)."""
    from rich.table import Table

    counts = _file_edit_metrics(session)
    console.print()
    console.print("[bold realm]᛭ Métricas de archivos editados[/]")
    if not counts:
        console.print("  [dim](ninguno)[/]")
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=False,
    )
    table.add_column("Archivo", style="tool.name")
    table.add_column("Ediciones", justify="right")
    for path, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(path, str(count))
    console.print(table)
    console.print()


def _metrics_emit_json(session, stream) -> None:
    """Emit machine-readable JSON via stream (bypasses Rich markup)."""
    import json

    counts, avg, total = _tool_metrics(session)
    data = {
        "tokens": dict(session.total_usage),
        "tools": {
            "total": total,
            "counts": counts,
            "average_duration": avg,
        },
        "commands": _command_metrics(session),
        "files": _file_edit_metrics(session),
        "session": {
            "start_time": session.session_start.isoformat(),
            "duration_seconds": session.session_duration(),
        },
    }
    stream.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
    stream.flush()


def _fmt_secs(seconds: float) -> str:
    """Format seconds as 'X.XXXs' or 'Xms' for small values."""
    if seconds < 0.001:
        return "0.000s"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.3f}s"


# ── /usage ──────────────────────────────────────────────────────────────


async def run_usage_command(session, args: str) -> None:
    """Detailed session statistics: tokens, cost, tools, messages, duration.

    Subcommands (same as legacy UsageCommand):
        /usage         — full statistics grid panel
        /usage json    — machine-readable JSON
    """
    import json
    import sys

    from .providers import estimate_cost
    from rich.panel import Panel
    from rich.table import Table

    usage = session.total_usage
    model = session.config.model
    total_cost = estimate_cost(
        model,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
    )
    tool_counts = session.tool_call_counts
    msg_counts = session.message_counts
    duration = session.session_duration()
    duration_str = _format_duration_short(duration)
    start_time = session.session_start.strftime("%Y-%m-%d %H:%M:%S")

    if args.strip().lower() == "json":
        data = {
            "tokens": {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            },
            "cost": {
                "total_usd": round(total_cost, 6),
                "per_model": session.per_model_usage,
            },
            "tool_calls": tool_counts,
            "messages": msg_counts,
            "session": {
                "start_time": session.session_start.isoformat(),
                "duration_seconds": duration,
                "duration_human": duration_str,
            },
        }
        # Bypass Rich console for JSON to avoid markup interpretation.
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0)

    grid.add_row("[bold frost]Tokens — Prompt[/]", f"[{_usage_color(prompt)}]{prompt:,}[/{_usage_color(prompt)}]")
    grid.add_row("[bold frost]Tokens — Completion[/]", f"[{_usage_color(completion)}]{completion:,}[/{_usage_color(completion)}]")
    grid.add_row("[bold frost]Tokens — Total[/]", f"[bold {_usage_color(total)}]{total:,}[/bold {_usage_color(total)}]")

    grid.add_row("[bold frost]Costo[/]", f"[model]${total_cost:.4f} USD[/]")

    if tool_counts:
        tool_lines = ", ".join(
            f"[tool.name]{name}[/]: {cnt}"
            for name, cnt in sorted(tool_counts.items(), key=lambda x: -x[1])
        )
    else:
        tool_lines = "[dim](ninguna)[/]"
    grid.add_row("[bold frost]Herramientas[/]", tool_lines)

    msg_total = sum(msg_counts.values())
    grid.add_row(
        "[bold frost]Mensajes[/]",
        f"usuario [cyan]{msg_counts.get('user', 0)}[/] · "
        f"asistente [cyan]{msg_counts.get('assistant', 0)}[/] · "
        f"herramienta [cyan]{msg_counts.get('tool', 0)}[/] · "
        f"total [bold cyan]{msg_total}[/]",
    )

    grid.add_row("[bold frost]Inicio[/]", f"[dim]{start_time}[/]")
    grid.add_row("[bold frost]Duración[/]", f"[bold cyan]{duration_str}[/]")

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Estadísticas de la sesión[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print()

    # Per-model breakdown table if multiple models have been used.
    if len(session.per_model_usage) > 1:
        table = Table(
            title="[bold realm]Desglose por modelo[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            expand=False,
        )
        table.add_column("Modelo", style="model")
        table.add_column("Prompt", justify="right")
        table.add_column("Completion", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Costo", justify="right")
        for m, stats in sorted(session.per_model_usage.items()):
            table.add_row(
                m,
                str(stats.get("prompt_tokens", 0)),
                str(stats.get("completion_tokens", 0)),
                str(stats.get("total_tokens", 0)),
                f"${stats.get('cost', 0.0):.4f}",
            )
        console.print(table)
        console.print()


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


def _render_macro_status(subcmd: str, name: str = "") -> None:
    """Print a Rich status indicator before delegating to MacroCommand.

    Adds visual framing only — does NOT change behavior. The MacroCommand
    inside still does the actual recording / stopping / playback.
    """
    try:
        from rich.panel import Panel
        from rich.table import Table
    except Exception:
        return

    if subcmd == "record":
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column(style="white")
        grid.add_row("Accion", "[bold green]Iniciar grabacion[/bold green]")
        grid.add_row("Nombre", f"[bold cyan]{name or '?'}[/bold cyan]")
        grid.add_row("Estado", "[bold red]\u25cf REC[/bold red]")
        grid.add_row(
            "Tip",
            "Cada comando de barra que escribas se anadira a la macro. "
            "Usa /macro stop para finalizar.",
        )
        console.print(Panel(
            grid,
            title="[bold realm]\u16ed /macro record[/]",
            border_style="green",
            expand=False,
        ))
        console.print()
    elif subcmd == "stop":
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column(style="white")
        grid.add_row("Accion", "[bold red]Detener grabacion[/bold red]")
        grid.add_row("Estado", "[dim]\u25cb STOP[/dim]")
        console.print(Panel(
            grid,
            title="[bold realm]\u16ed /macro stop[/]",
            border_style="red",
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


async def run_snippet_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
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
    except Exception as exc:  # pragma: no cover
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando snippets: %s", exc)
    return {}


def _save_snippets(snippets: dict[str, dict]) -> None:
    """Guarda snippets en ``CONFIG_DIR/snippets.json``."""
    _SNIPPETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SNIPPETS_PATH.write_text(
        json.dumps(snippets, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Feedback command ─────────────────────────────────────────────────


async def run_feedback_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Gestiona feedback local con ``/feedback [add|clear|help]``."""
    feedback_path = CONFIG_DIR / "feedback.json"
    text = args.strip()
    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""

    if subcmd == "help":
        console.print(
            "[bold realm]Uso de /feedback[/]\n"
            "  [bold cyan]/feedback[/] — muestra las últimas 5 entradas\n"
            "  [bold cyan]/feedback add <mensaje>[/] — guarda feedback\n"
            "  [bold cyan]/feedback clear[/] — borra todas las entradas\n"
            "  [bold cyan]/feedback help[/] — muestra esta ayuda"
        )
        return

    try:
        if feedback_path.exists():
            entries = json.loads(feedback_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                render_error("El archivo de feedback tiene un formato inválido.")
                return
        else:
            entries = []
    except (OSError, json.JSONDecodeError) as exc:
        render_error(f"No se pudo leer el feedback: {exc}")
        return

    if not text:
        if not entries:
            console.print("[dim]No hay feedback guardado.[/]")
            return

        from rich.table import Table

        table = Table(title="Feedback reciente")
        table.add_column("Fecha", style="cyan", no_wrap=True)
        table.add_column("Mensaje")
        for entry in entries[-5:]:
            if isinstance(entry, dict):
                timestamp = str(entry.get("ts", ""))
                message = str(entry.get("message", ""))
            else:
                timestamp = ""
                message = str(entry)
            table.add_row(timestamp, message)
        console.print(table)
        return

    if subcmd == "add":
        message = parts[1].strip() if len(parts) > 1 else ""
        if not message:
            render_error("Uso: /feedback add <mensaje>")
            return
        entries.append({"ts": datetime.now(UTC).isoformat(), "message": message})
        try:
            feedback_path.parent.mkdir(parents=True, exist_ok=True)
            feedback_path.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            render_error(f"No se pudo guardar el feedback: {exc}")
            return
        console.print("[success]✓ Feedback guardado.[/]")
        return

    if subcmd == "clear":
        count = len(entries)
        if not count:
            console.print("[dim]No hay feedback para borrar.[/]")
            return

        try:
            from rich.prompt import Confirm
        except ImportError:  # pragma: no cover - Rich incluye Confirm normalmente
            confirmed = False
            while True:
                console.print(f"¿Borrar {count} entries? (s/n)")
                try:
                    answer = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = "n"
                if answer in ("s", "sí", "si"):
                    confirmed = True
                    break
                if answer in ("n", "no"):
                    break
        else:
            try:
                confirmed = Confirm.ask(
                    f"¿Borrar {count} entradas de feedback?",
                    default=False,
                )
            except (EOFError, KeyboardInterrupt):
                confirmed = False

        if not confirmed:
            console.print("[dim]Operación cancelada.[/]")
            return

        try:
            feedback_path.write_text("[]\n", encoding="utf-8")
        except OSError as exc:
            render_error(f"No se pudo borrar el feedback: {exc}")
            return
        console.print(f"[success]✓ {count} entradas de feedback borradas.[/]")
        return

    render_error("Uso: /feedback [add <mensaje>|clear|help]")

"""Source for /learn slash command block. Imported as a module from _learn_section.py."""

from ._learn_section import (
    SkillSuggestion,
    suggest_from_post_mortems,
    suggest_from_state_path,
    save_suggestion,
)

# Internal state: cached suggestions from the last /learn invocation,
# keyed by state file path so the user can /learn save <n> without
# re-running the analysis.
_LEARN_CACHE: dict[str, list[SkillSuggestion]] = {}


async def run_learn_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Suggest reusable delegation skills from historical post-mortems (/learn).

    Examples:
        /learn              # show candidate skills (table, numbered)
        /learn save 1       # persist suggestion #1 as a real DelegationSkill YAML
        /learn save 2 3     # persist suggestions 2 and 3 in one go
        /learn clear        # drop the cached suggestions for the current session

    Behaviour:
      * Reads post-mortems from the active orchestration state file
        (``~/.yggdrasil/orchestration_state.json`` by default; override
        via ``YGGDRASIL_ORCHESTRATION_STATE``).
      * Groups successful delegations by preset; presets with ``>=2``
        successes are surfaced as candidates.
      * ``/learn save <n>`` materialises the suggestion via the
        ``DelegationSkillRegistry`` (lilith-skills, ~/.yggdrasil/skills/<n>.yaml).

    This command is the automejora nivel-2 surface: it converts past
    successful delegations into reusable skills without operator intervention.
    """
    import shlex as _shlex

    from .render import console, render_error

    text = args.strip()
    if not text:
        _render_learn_table()
        return

    try:
        tokens = _shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    sub = tokens[0].lower()
    if sub == "clear":
        _LEARN_CACHE.clear()
        console.print("[dim]caché de /learn vaciada.[/]")
        return

    if sub != "save":
        render_error(
            "Uso: /learn [save <n> [<n> ...] | clear]"
        )
        return

    indices: list[int] = []
    for raw in tokens[1:]:
        try:
            indices.append(int(raw))
        except ValueError:
            render_error(f"Índice inválido: {raw!r} (debe ser un entero)")
            return

    suggestions = _learn_cached_or_refresh()
    if not suggestions:
        render_error(
            "No hay sugerencias en caché; ejecuta /learn primero."
        )
        return

    by_index = {s.index: s for s in suggestions}
    saved: list[str] = []
    for idx in indices:
        suggestion = by_index.get(idx)
        if suggestion is None:
            render_error(
                f"Índice fuera de rango: {idx} (rango válido: 1..{len(suggestions)})"
            )
            continue
        try:
            path = save_suggestion(suggestion)
        except (OSError, TypeError, ValueError) as exc:
            render_error(
                f"No se pudo guardar {suggestion.name!r}: {exc}"
            )
            continue
        saved.append(str(path))

    if saved:
        console.print(
            f"[success]✓ {len(saved)} skill(s) guardadas:[/]"
        )
        for path in saved:
            console.print(f"  [dim]- {path}[/]")


def _learn_state_path() -> Path:
    """Return the active state path, honouring the env override."""
    import os as _os
    override = _os.environ.get("YGGDRASIL_ORCHESTRATION_STATE")
    return (
        Path(override).expanduser() if override
        else Path.home() / ".yggdrasil" / "orchestration_state.json"
    )


def _learn_cached_or_refresh() -> list[SkillSuggestion]:
    """Return cached suggestions for the current state path, refreshing if empty."""
    state_path = _learn_state_path()
    key = str(state_path)
    cached = _LEARN_CACHE.get(key)
    if cached:
        return cached
    suggestions = suggest_from_state_path(state_path)
    _LEARN_CACHE[key] = suggestions
    return suggestions


def _render_learn_table() -> None:
    """Render the /learn table (or a clear empty-state message)."""
    from rich.table import Table

    from .render import console, render_error

    suggestions = _learn_cached_or_refresh()
    state_path = _learn_state_path()
    if not suggestions:
        if state_path.exists():
            render_error(
                "No hay suficientes post-mortems exitosos para proponer skills "
                "(se requieren >=2 por preset). Ejecuta mas delegaciones primero."
            )
        else:
            render_error(
                f"No se encontró el archivo de estado {state_path}. "
                "Ejecuta delegaciones primero para generar post-mortems."
            )
        return

    table = Table(
        title="[bold realm]᛭ /learn — skills sugeridas desde post-mortems[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=True,
        caption=(
            f"[dim]{len(suggestions)} sugerencia(s) · "
            "guarda con /learn save <n>[/dim]"
        ),
    )
    table.add_column("#", style="bold cyan", no_wrap=True)
    table.add_column("Nombre", style="white")
    table.add_column("Preset", style="white")
    table.add_column("Flags", style="dim")
    table.add_column("Éxitos", style="bold", justify="right")
    table.add_column("Descripción", style="white")

    for s in suggestions:
        flags = []
        if s.agentic:
            flags.append("agentic")
        if s.structured:
            flags.append("structured")
        if s.max_tokens is not None:
            flags.append(f"max_tokens={s.max_tokens}")
        flags_str = ", ".join(flags) if flags else "-"
        table.add_row(
            str(s.index),
            s.name,
            s.preset,
            flags_str,
            str(s.success_count),
            s.description,
        )

    console.print(table)
    console.print()
