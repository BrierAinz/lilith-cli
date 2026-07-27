"""/how command: detailed help for any registered slash command.

Given a command name or alias, /how prints:
  * its canonical name
  * all aliases
  * the short description
  * the class docstring (Usage, Examples, etc.)
  * the location of the implementation file (best-effort)

This is a self-contained, registry-aware introspection command — it does
not require editing commands.py because it uses the public CommandRegistry
interface. Implementation lives in its own file so it does not grow the
422 KB extra_commands.py monolith.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from .render import console, render_error

if TYPE_CHECKING:
    from rich.panel import Panel

    from .agent import AgentSession


def _resolve_command(session: "AgentSession", name: str):
    """Look up a command (or alias) in the freshly-built registry.

    Returns the BaseCommand instance, or ``None`` if not found. A fresh
    registry is built each call so the introspection always reflects the
    current code, not a stale snapshot.
    """
    from .commands import CommandRegistry

    registry = CommandRegistry(session)
    registry.discover()
    return registry.get(name)


def _format_block(cmd) -> "Panel":
    """Render the rich block describing a single BaseCommand."""
    from rich.panel import Panel

    canonical = cmd.name
    aliases = list(getattr(cmd, "aliases", []) or [])
    description = getattr(cmd, "description", "") or ""

    doc = inspect.getdoc(type(cmd)) or ""
    # Trim the class header line ("ClassName(...)") that inspect.getdoc
    # may include when the class has no explicit docstring.
    if doc.startswith(type(cmd).__name__):
        # Drop the first line if it is just the class signature.
        lines = doc.splitlines()
        if lines and (lines[0].strip().endswith(":") or ":" in lines[0]):
            doc = "\n".join(lines[1:]).lstrip("\n")

    module = inspect.getmodule(type(cmd))
    file_hint = ""
    if module is not None and getattr(module, "__file__", None):
        file_hint = module.__file__.replace("\\", "/").split("/lilith-cli/")[-1]
        if file_hint and not file_hint.startswith("lilith_cli/"):
            file_hint = f"lilith_cli/{file_hint}" if "lilith_cli" in module.__file__ else module.__file__

    lines: list[str] = []
    lines.append(f"  [bold]Nombre:[/]     [bold cyan]/{canonical}[/]")
    if aliases:
        lines.append(
            "  [bold]Aliases:[/]    "
            + ", ".join(f"[cyan]/{a}[/]" for a in aliases)
        )
    else:
        lines.append("  [bold]Aliases:[/]    [dim](ninguno)[/]")
    if description:
        lines.append(f"  [bold]Resumen:[/]    {description}")
    if file_hint:
        lines.append(f"  [bold]Origen:[/]     [dim]{file_hint}[/]")

    body = "\n".join(lines)
    if doc:
        body += "\n\n[bold]Documentación:[/]\n" + doc.rstrip()

    return Panel(
        body,
        title=f"[gold]᛭ Cómo usar /{canonical}[/]",
        border_style="dim cyan",
    )


async def run_how_command(session: "AgentSession", args: str) -> None:
    """Show detailed help for a slash command (/how <nombre>)."""
    name = args.strip().lstrip("/")
    if not name:
        render_error("Uso: /how <comando>  ·  ejemplo: /how help")
        console.print(
            "[dim]Inspecciona cualquier comando de barra registrado y muestra "
            "sus aliases, descripción y docstring.[/]"
        )
        return

    cmd = _resolve_command(session, name)
    if cmd is None:
        render_error(f"Comando desconocido: /{name}")
        console.print("[dim]Escribí /help para ver la lista completa.[/]")
        return

    panel = _format_block(cmd)
    console.print(panel)
    console.print()