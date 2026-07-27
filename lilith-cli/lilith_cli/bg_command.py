"""Background-process slash command for Lilith (``/bg``).

Wraps the existing :class:`lilith_cli.process_manager.ProcessManager` so
the user can launch, inspect, tail and stop long-running dev servers,
watchers and similar background tasks directly from the REPL.

La infra (start/stop/status/list/log/cleanup) ya estaba implementada y
cubierta por ``tests/test_process_manager.py``; este modulo solo expone
la funcionalidad como slash command y agrega mensajes en espanol,
renderizado Rich y parsing ergonomico de argumentos.
"""
from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING

from .process_manager import ProcessManager
from .render import console, render_error


if TYPE_CHECKING:
    from .agent import AgentSession


_BG_USAGE = """Uso: /bg <subcomando> [args]

Subcomandos:
  list                                 Lista procesos en background (alive + zombies).
  start <nombre> -- <comando>          Inicia un proceso en background.
  status <nombre>                      Estado de un proceso (PID, alive, port, comando).
  stop <nombre>                        Detiene un proceso y limpia su estado.
  log <nombre> [--lines N | -n N]      Muestra las últimas N líneas del log (por defecto 50).
  cleanup                              Elimina archivos de estado de procesos muertos.

Ejemplos:
  /bg start devserver -- python -m http.server 8080
  /bg list
  /bg status devserver
  /bg log devserver --lines 20
  /bg stop devserver
"""


def _render_bg_table(rows: list[dict]) -> None:
    """Render a Rich table of background processes."""
    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Nombre", style="tool.name")
    table.add_column("PID", justify="right")
    table.add_column("Alive", justify="center")
    table.add_column("Port", justify="right")
    table.add_column("Comando", overflow="fold")

    if not rows:
        table.add_row(
            "[dim]—[/]",
            "[dim]—[/]",
            "[dim]—[/]",
            "[dim]—[/]",
            "[dim](sin procesos registrados)[/]",
        )
    else:
        for row in rows:
            alive = row.get("alive", False)
            alive_str = "[success]✓[/]" if alive else "[error]✗[/]"
            port = row.get("port") or "—"
            cmd = row.get("command") or ""
            table.add_row(
                str(row.get("name", "")),
                str(row.get("pid", "—")),
                alive_str,
                str(port),
                cmd,
            )
    console.print(table)


def _parse_bg_subcommand(args: str) -> tuple[str, str]:
    """Split ``args`` into ``(subcommand, rest)``; subcommand is lowercased."""
    text = (args or "").strip()
    if not text:
        return "", ""
    parts = text.split(maxsplit=1)
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


def _parse_log_lines(rest: str) -> int:
    """Extract ``--lines N`` or ``-n N`` from *rest*, defaulting to 50."""
    text = (rest or "").strip()
    if not text:
        return 50
    for flag in ("--lines", "-n"):
        m = re.search(rf"{re.escape(flag)}\s+(\d+)", text)
        if m:
            try:
                value = int(m.group(1))
                return max(1, min(value, 1000))
            except ValueError:
                return 50
    return 50


async def run_bg_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Gestiona procesos en background (``/bg list|start|status|stop|log|cleanup``).

    Examples:
        /bg
        /bg list
        /bg start devserver -- python -m http.server 8080
        /bg status devserver
        /bg log devserver --lines 30
        /bg stop devserver
        /bg cleanup
    """
    subcmd, rest = _parse_bg_subcommand(args)

    manager = ProcessManager()

    if not subcmd or subcmd in ("help", "?"):
        console.print(_BG_USAGE)
        return

    if subcmd == "list":
        rows = manager.list()
        _render_bg_table(rows)
        return

    if subcmd == "start":
        text = (rest or "").strip()
        if "--" not in text:
            render_error("Uso: /bg start <nombre> -- <comando>")
            return
        name_part, _, cmd_part = text.partition("--")
        name = name_part.strip()
        command = cmd_part.strip()
        if not name:
            render_error("Uso: /bg start <nombre> -- <comando>")
            return
        if not command:
            render_error("Falta el comando tras ``--``.")
            return
        pid = manager.start(name, command)
        if pid is None:
            render_error(
                f"No se pudo iniciar ``{name}``. ¿Ya existe un proceso con ese nombre "
                f"vivo? Probá ``/bg list`` y ``/bg stop {name}`` si es zombi."
            )
            return
        console.print(
            f"[success]✓[/] Proceso ``{name}`` iniciado (PID {pid}). "
            f"Log: ~/.yggdrasil/processes/logs/{name}.log"
        )
        return

    if subcmd == "status":
        name = rest.strip()
        if not name:
            render_error("Uso: /bg status <nombre>")
            return
        row = manager.status(name)
        if row is None:
            render_error(f"Proceso ``{name}`` no registrado.")
            return
        alive_str = "vivo" if row.get("alive") else "muerto"
        port = row.get("port")
        port_str = f", port {port}" if port else ""
        console.print(
            f"[bold cyan]{name}[/] — {alive_str}{port_str}\n"
            f"  PID    : {row.get('pid', '—')}\n"
            f"  Log    : {row.get('log_file', '—')}\n"
            f"  Comando: {row.get('command', '—')}"
        )
        return

    if subcmd == "stop":
        name = rest.strip()
        if not name:
            render_error("Uso: /bg stop <nombre>")
            return
        ok = manager.stop(name)
        if not ok:
            render_error(f"No se pudo detener ``{name}`` (¿no existe?).")
            return
        console.print(f"[success]✓[/] Proceso ``{name}`` detenido y limpiado.")
        return

    if subcmd == "log":
        name = rest.strip()
        if not name:
            render_error("Uso: /bg log <nombre> [--lines N]")
            return
        try:
            tokens = shlex.split(name)
        except ValueError:
            tokens = name.split()
        if not tokens:
            render_error("Uso: /bg log <nombre> [--lines N]")
            return
        proc_name = tokens[0]
        flag_rest = " ".join(tokens[1:])
        lines = _parse_log_lines(flag_rest)
        text = manager.get_log(proc_name, lines=lines)
        if not text:
            render_error(f"Sin log para ``{proc_name}`` (¿existe?).")
            return
        console.print(
            f"[dim]── log de [bold cyan]{proc_name}[/] "
            f"(últimas {lines} líneas) ──[/]"
        )
        console.print(text)
        return

    if subcmd == "cleanup":
        cleaned = manager.cleanup()
        if cleaned:
            console.print(
                f"[success]✓[/] Limpiados {len(cleaned)} estado(s) zombi: "
                f"{', '.join(f'[bold cyan]{n}[/]' for n in cleaned)}"
            )
        else:
            console.print("[dim]Nada que limpiar.[/]")
        return

    render_error(f"Subcomando de /bg desconocido: {subcmd}\n\n{_BG_USAGE}")