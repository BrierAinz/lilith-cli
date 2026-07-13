"""Comandos de barra adicionales para Lilith CLI — watcher.

Implementa /watch como funcion simple que delega en las herramientas de
lilith_tools, sin modificar commands.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lilith_tools.watcher import (
    WatchEventsTool,
    WatchFilesTool,
    WatchStatusTool,
    WatchStopTool,
)

from .render import console, render_error


if TYPE_CHECKING:
    from .agent import AgentSession


async def run_watch_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /watch usando las herramientas de watcher de lilith_tools.

    Examples:
        /watch src
        /watch src --patterns *.py
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
            render_error("Uso: /watch stop <watch_id>")
            return
        result = WatchStopTool().execute(watch_id=parts[1])
        _print_tool_result(result)
        return

    if subcmd in ("events", "eventos"):
        if len(parts) < 2:
            render_error("Uso: /watch events <watch_id>")
            return
        watch_id = parts[1]
        since: float = 0
        if len(parts) >= 4 and parts[2] == "--since":
            try:
                since = float(parts[3])
            except ValueError:
                render_error("--since requiere un numero")
                return
        result = WatchEventsTool().execute(watch_id=watch_id, since=since)
        _print_watch_events(result)
        return

    # /watch <path> [patterns...]
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
    _print_tool_result(result)


def _print_tool_result(result) -> None:
    """Renderiza el resultado de una lilith_tools ToolResult en la consola."""
    if not result.success:
        error = result.error or "Error desconocido ejecutando la herramienta"
        render_error(error)
        return

    data = result.data
    if isinstance(data, dict):
        if "watch_id" in data:
            watch_id = data["watch_id"]
            paths = data.get("paths", [])
            console.print(f"[success]✓ Watcher iniciado: {watch_id}[/]")
            console.print(f"  [dim]paths: {', '.join(str(p) for p in paths)}[/]")
            if data.get("patterns"):
                console.print(f"  [dim]patterns: {', '.join(data['patterns'])}[/]")
            return
        if "stopped" in data:
            console.print(f"[success]✓ Watcher detenido: {data.get('watch_id')}[/]")
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


def _print_watch_events(result) -> None:
    """Muestra los eventos de un watcher."""
    if not result.success:
        render_error(result.error or "Error consultando eventos")
        return

    data = result.data or {}
    events = data.get("events", [])
    if not events:
        console.print("[dim]No hay eventos nuevos.[/]")
        return

    console.print("\n[bold realm]᛭ Eventos[/]")
    for e in events:
        console.print(
            f"  [cyan]{e.get('event_type')}[/] {e.get('path')} "
            f"[dim]({e.get('timestamp', 0):.3f})[/]"
        )
    console.print()
