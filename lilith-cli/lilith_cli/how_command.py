"""
/how command: detailed help for any slash command.

Given a command name or alias, /how prints:
  * its canonical name
  * all aliases
  * the short description
  * the docstring (Usage, Examples, etc.)
  * the location of the implementation file

``BaseCommand`` subclasses are looked up in :class:`CommandRegistry`; every
other command comes from the route table in :mod:`lilith_cli.slash_router`
(built-in routes and command plugins), so /how covers exactly what the REPL
dispatches. The route index is built on first use and cached.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from rich.panel import Panel

    from .session_runtime import SessionRuntime


_EXTRA_INDEX: dict[str, dict[str, Any]] | None = None


def _resolve_command(session: "SessionRuntime", name: str):
    """Look up a command (or alias) in the freshly-built registry.

    Returns the BaseCommand instance, or ``None`` if not found. A fresh
    registry is built each call so the introspection always reflects the
    current code, not a stale snapshot.
    """
    from .commands import CommandRegistry

    registry = CommandRegistry(session)
    registry.discover()
    return registry.get(name)


def _resolve_extra_command(name: str) -> dict[str, Any] | None:
    """Fallback resolver for commands that live outside the BaseCommand registry.

    Returns a dict with keys ``name``, ``aliases``, ``summary``, ``doc``,
    ``origin`` (str) and ``callable``, or ``None`` if ``name`` is not a
    routed command or alias. The index is cached on ``_EXTRA_INDEX``.
    """
    global _EXTRA_INDEX
    if _EXTRA_INDEX is None:
        _EXTRA_INDEX = _build_extra_index()

    # Direct match or alias match.
    if name in _EXTRA_INDEX:
        return _EXTRA_INDEX[name]
    for entry in _EXTRA_INDEX.values():
        if name in entry["aliases"]:
            return entry
    return None


def _build_extra_index() -> dict[str, dict[str, Any]]:
    """Map every routed command to its aliases, summary, doc, origin and callable."""
    from .slash_router import all_routes

    index: dict[str, dict[str, Any]] = {}
    for item in all_routes().values():
        if item.name in index:
            continue
        func = item.handler
        doc = inspect.getdoc(func) or ""
        module = inspect.getmodule(func)
        module_name = module.__name__ if module else "lilith_cli.slash_router"
        index[item.name] = {
            "name": item.name,
            "aliases": list(item.aliases),
            "summary": _first_meaningful_line(doc),
            "doc": doc,
            "origin": module_name.replace(".", "/") + ".py",
            "callable": func,
        }
    return index


def _first_meaningful_line(doc: str) -> str:
    """Return the first non-empty, non-pure-Examples line of ``doc``."""
    for raw in doc.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("examples:"):
            continue
        return line
    return ""


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


def _format_extra_block(entry: dict[str, Any]) -> "Panel":
    """Render a lighter panel for non-BaseCommand commands."""
    from rich.panel import Panel

    canonical = entry["name"]
    aliases = entry["aliases"]
    summary = entry["summary"]
    doc = entry["doc"]
    origin = entry["origin"]

    lines: list[str] = []
    lines.append(f"  [bold]Nombre:[/]     [bold cyan]/{canonical}[/]")
    if aliases:
        lines.append(
            "  [bold]Aliases:[/]    "
            + ", ".join(f"[cyan]/{a}[/]" for a in aliases)
        )
    else:
        lines.append("  [bold]Aliases:[/]    [dim](ninguno)[/]")
    if summary:
        lines.append(f"  [bold]Resumen:[/]    {summary}")
    if origin:
        lines.append(f"  [bold]Origen:[/]     [dim]{origin}[/]")
    lines.append(
        "  [bold]Tipo:[/]       [dim]ruta de slash_router "
        "(no registrado en CommandRegistry)[/]"
    )

    body = "\n".join(lines)
    if doc:
        body += "\n\n[bold]Documentación:[/]\n" + doc.rstrip()

    return Panel(
        body,
        title=f"[gold]᛭ Cómo usar /{canonical}[/]",
        border_style="dim magenta",
    )


async def run_how_command(session: "SessionRuntime", args: str) -> None:
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
    if cmd is not None:
        panel = _format_block(cmd)
        console.print(panel)
        console.print()
        return

    entry = _resolve_extra_command(name)
    if entry is not None:
        panel = _format_extra_block(entry)
        console.print(panel)
        console.print()
        return

    render_error(f"Comando desconocido: /{name}")
    console.print("[dim]Escribí /help para ver la lista completa.[/]")