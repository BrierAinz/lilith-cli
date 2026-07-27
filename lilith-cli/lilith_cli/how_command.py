"""
/how command: detailed help for any registered slash command.

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

Coverage
--------
Historically ``/how`` only inspected the 47 ``BaseCommand`` subclasses
registered in ``commands.py``. That left 40+ functional slash commands —
the ones implemented as free-standing ``run_X_command`` coroutines in
``extra_commands.py`` or in their own ``<name>_command.py`` file and
routed through hard-coded branches in ``repl.py`` — silently reporting
"Comando desconocido" when the user asked ``/how timer``,
``/how quote``, ``/how epoch``, etc.

This module builds a parallel runtime index over those extra commands
the first time ``/how`` is invoked and caches it on the function itself.
The fallback panel uses the ``run_X_command`` docstring as the body,
and parses the dispatcher branches (``cmd_name == "x"`` /
``cmd_name in ("x","y")``) in ``repl.py`` to recover aliases that are
not surfaced anywhere else (e.g. ``diff-staged`` → ``diffstaged``,
``reverse`` → ``rev``).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from rich.panel import Panel

    from .agent import AgentSession


_LILITH_CLI_DIR = Path(__file__).resolve().parent
_EXTRA_INDEX: dict[str, dict[str, Any]] | None = None


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


def _resolve_extra_command(name: str) -> dict[str, Any] | None:
    """Fallback resolver for commands that live outside the BaseCommand registry.

    Returns a dict with keys ``name``, ``aliases``, ``summary``, ``doc``,
    ``origin`` (str) and ``callable``, or ``None`` if ``name`` is not
    found among the ``run_X_command`` coroutines exposed by
    ``extra_commands`` and the standalone ``<name>_command.py`` files.

    The result is cached on the module-level ``_EXTRA_INDEX`` after the
    first call to avoid re-parsing the 422 KB ``extra_commands.py`` every
    time ``/how`` is invoked.
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
    """Walk the lilith_cli package and collect every ``run_X_command`` coroutine.

    The result maps the canonical command name (the ``X`` in
    ``run_X_command``) to a dict with aliases, summary, doc, origin and
    the live callable. Aliases come from ``repl.py``'s ``cmd_name in
    ("a", "b")`` dispatch branches so the introspection matches what
    the user can actually type.
    """
    index: dict[str, dict[str, Any]] = {}
    aliases_by_name = _extract_repl_aliases()

    # 1) extra_commands.py — the 422 KB monolith.
    extra_path = _LILITH_CLI_DIR / "extra_commands.py"
    _harvest_module(extra_path, index, aliases_by_name, module_name="extra_commands")

    # 2) Standalone <name>_command.py files (batch, notes, pipeline,
    # workflow, completion). Each carries its own run_X_command.
    for module_path in sorted(_LILITH_CLI_DIR.glob("*_command.py")):
        if module_path.name == "extra_commands.py":
            continue
        _harvest_module(
            module_path,
            index,
            aliases_by_name,
            module_name=module_path.stem,
        )

    return index


def _extract_repl_aliases() -> dict[str, list[str]]:
    """Parse repl.py and return ``{primary_name: [aliases...]}``.

    A primary name is the first string literal of every dispatch branch
    that runs ``run_X_command``; aliases are the remaining literals in
    ``cmd_name in ("a", "b", ...)`` tuples.
    """
    repl_path = _LILITH_CLI_DIR / "repl.py"
    try:
        tree = ast.parse(repl_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    # Find the names of imported run_X_command coroutines so we know
    # which branches are command dispatches (vs e.g. ``cmd_name ==
    # "quit"``).
    imported_runs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name.startswith("run_") and alias.name.endswith("_command"):
                    imported_runs.add(alias.asname or alias.name)

    aliases: dict[str, list[str]] = {}

    # Walk every if-statement that compares cmd_name.
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Each if-statement in the dispatcher looks like:
        #   if cmd_name == "foo":           -> primary "foo"
        #   if cmd_name in ("a", "b"):      -> primary "a", alias "b"
        #   if cmd_name == "x" and X:       -> skip (combined condition)
        primary: str | None = None
        extras: list[str] = []
        for cmp in _iter_cmd_name_compares(node.test):
            if cmp["op"] == "Eq" and cmp["value"]:
                if primary is None:
                    primary = cmp["value"]
                else:
                    extras.append(cmp["value"])
            elif cmp["op"] == "In" and cmp["values"]:
                # Pure-tuple dispatch has no separate primary — the
                # *first* literal is the canonical name and the rest
                # are aliases (e.g. cmd_name in ("reverse", "rev")).
                if primary is None:
                    primary = cmp["values"][0]
                    extras.extend(cmp["values"][1:])
                else:
                    extras.extend(cmp["values"])
        if not primary:
            continue
        # Only treat as a dispatch branch if the body calls a known
        # run_X_command.
        if not _body_calls_run_command(node.body, imported_runs):
            continue
        seen = {primary}
        for value in extras:
            if value and value not in seen:
                seen.add(value)
                aliases.setdefault(primary, []).append(value)

    return aliases


def _iter_cmd_name_compares(expr: ast.AST) -> list[dict[str, Any]]:
    """Yield ``{"op": "Eq"|"In", "value": str | None, "values": [str]}``."""
    out: list[dict[str, Any]] = []
    if isinstance(expr, ast.Compare):
        left = expr.left
        if isinstance(left, ast.Name) and left.id == "cmd_name":
            for op, comparator in zip(expr.ops, expr.comparators):
                if (
                    isinstance(op, ast.Eq)
                    and isinstance(comparator, ast.Constant)
                    and isinstance(comparator.value, str)
                ):
                    out.append({"op": "Eq", "value": comparator.value, "values": []})
                elif isinstance(op, ast.In) and isinstance(
                    comparator, (ast.Tuple, ast.List)
                ):
                    values = [
                        elt.value
                        for elt in comparator.elts
                        if isinstance(elt, ast.Constant)
                        and isinstance(elt.value, str)
                        and elt.value
                    ]
                    out.append({"op": "In", "value": None, "values": values})
    return out


def _body_calls_run_command(
    body: list[ast.stmt], imported_runs: set[str]
) -> bool:
    """Return True if any await/run_X_command appears in the if body."""
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call):
                call = sub.value
                if (
                    isinstance(call.func, ast.Name)
                    and call.func.id in imported_runs
                ):
                    return True
                if isinstance(call.func, ast.Attribute) and isinstance(
                    call.func.value, ast.Name
                ) and call.func.value.id in imported_runs:
                    return True
    return False


def _harvest_module(
    path: Path,
    index: dict[str, dict[str, Any]],
    aliases_by_name: dict[str, list[str]],
    *,
    module_name: str,
) -> None:
    """Populate ``index`` with every ``run_X_command`` in ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return

    # Build the live module object lazily so we can pull the actual
    # callable (used by ``inspect.getdoc`` to render the docstring).
    live_module: Any | None = None

    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("run_")
            and node.name.endswith("_command")
        ):
            continue
        cmd_name = node.name[4:-8]
        if cmd_name in index:
            # Already collected (shouldn't happen because each module
            # has its own naming scheme, but be defensive).
            continue

        # Pull the real function object so inspect.getdoc returns the
        # actual docstring instead of having to parse the AST.
        if live_module is None:
            try:
                import importlib

                live_module = importlib.import_module(f"lilith_cli.{module_name}")
            except Exception:  # noqa: BLE001 - any import failure is non-fatal
                live_module = None
        func = getattr(live_module, node.name, None) if live_module else None

        doc = (inspect.getdoc(func) or "") if func else ast.get_docstring(node) or ""
        summary = _first_meaningful_line(doc)
        aliases = list(dict.fromkeys(aliases_by_name.get(cmd_name, [])))
        origin = f"lilith_cli/{module_name}.py" if module_name != "extra_commands" else "lilith_cli/extra_commands.py"

        index[cmd_name] = {
            "name": cmd_name,
            "aliases": aliases,
            "summary": summary,
            "doc": doc,
            "origin": origin,
            "callable": func,
        }


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
        "  [bold]Tipo:[/]       [dim]comando dispatched via repl.py "
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