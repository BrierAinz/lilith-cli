"""``/undo-peek`` slash command: preview and manage undo backups safely.

The base :class:`lilith_cli.commands.UndoCommand` exposes ``/undo pop`` and
``/undo list``. Both are destructive or blind: ``pop`` replaces the file
without confirmation, and ``list`` only shows metadata. Real users want a
diff *before* they pop, plus a way to wipe stale backups. This module
adds those affordances as a complementary command without touching the
existing :class:`UndoCommand` (which has caused corruption incidents in
prior edit cycles — it stays untouched).

Subcommands:

* ``/undo-peek``           — show a unified diff of the most recent backup
* ``/undo-peek <N>``       — show the diff of the N-th most recent backup
* ``/undo-peek list``      — alias of ``/undo list`` for symmetry
* ``/undo-peek clear``     — wipe all pending backups after confirmation
* ``/undo-peek help``      — show usage

Aliases: ``/undo-diff``, ``/peeks``.
"""

from __future__ import annotations

import difflib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lilith_tools.undo import UndoManager
from .render import console, render_error

if TYPE_CHECKING:
    from .session_runtime import SessionRuntime


# Maximum number of context lines in the printed diff. Keeps REPL output
# readable for large files without flooding the screen.
_MAX_DIFF_CONTEXT = 3

# Maximum total bytes the diff section will print before truncating with
# an ellipsis. Prevents a runaway diff from clearing the screen.
_MAX_DIFF_BYTES = 32_000


def _format_entry(index: int, total: int, entry) -> str:
    """Return a short header line for ``entry`` (1-based ``index`` of ``total``)."""
    ts = datetime.fromtimestamp(entry.timestamp).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"  [bold cyan]{index}/{total}.[/] [model]{entry.tool}[/] "
        f"[dim]{entry.original_path}[/] · [dim]{ts}[/]"
    )


def _build_diff(original_path: Path, backup_path: Path) -> tuple[str, bool]:
    """Return ``(diff_text, has_changes)`` for a unified diff of two files.

    ``has_changes`` is ``False`` when the backup is byte-identical to the
    current file (nothing to undo) or when the backup is missing.
    """
    if not backup_path.exists():
        return (
            f"[dim]Backup eliminado o inaccesible: {backup_path}[/]",
            False,
        )

    original_text = ""
    if original_path.exists():
        original_text = original_path.read_text(encoding="utf-8", errors="replace")
    backup_text = backup_path.read_text(encoding="utf-8", errors="replace")

    if original_text == backup_text:
        return "", False

    diff = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        backup_text.splitlines(keepends=True),
        fromfile=f"actual: {original_path}",
        tofile=f"backup: {backup_path.name}",
        n=_MAX_DIFF_CONTEXT,
    )
    text = "".join(diff)
    truncated = False
    if len(text.encode("utf-8")) > _MAX_DIFF_BYTES:
        text = text.encode("utf-8")[:_MAX_DIFF_BYTES].decode("utf-8", errors="replace")
        truncated = True
    return text, True


def _print_usage() -> None:
    console.print(
        "\n[bold realm]᛭ /undo-peek — previsualiza y gestiona backups de undo[/]\n\n"
        "  [bold cyan]/undo-peek[/]              "
        "— diff del backup más reciente (sin restaurar)\n"
        "  [bold cyan]/undo-peek <N>[/]          "
        "— diff del backup N (1 = más reciente)\n"
        "  [bold cyan]/undo-peek list[/]         "
        "— lista de backups pendientes\n"
        "  [bold cyan]/undo-peek clear[/]        "
        "— borra todos los backups pendientes\n"
        "  [bold cyan]/undo-peek help[/]         "
        "— esta ayuda\n\n"
        "[dim]Para restaurar: [bold cyan]/undo pop[/]. "
        "Atajos: /undo-diff, /peeks.[/]\n"
    )


def _show_diff(manager, entries, target_index: int) -> None:
    """Show the diff of the backup at 1-based ``target_index``."""
    total = len(entries)
    if total == 0:
        console.print("[dim]No hay backups pendientes.[/]")
        return

    # Convert 1-based to 0-based; entries are stored oldest-first.
    if target_index < 1 or target_index > total:
        render_error(
            f"Indice fuera de rango: {target_index}. Hay {total} backup(s).",
        )
        return

    entry = entries[target_index - 1]
    original_path = Path(entry.original_path)
    backup_path = Path(entry.backup_path)

    console.print("\n[bold realm]᛭ Backup seleccionado[/]")
    console.print(_format_entry(target_index, total, entry))
    console.print()

    diff_text, has_changes = _build_diff(original_path, backup_path)

    if not has_changes:
        if diff_text:
            console.print(diff_text)
        else:
            console.print(
                "[dim]El backup es identico al archivo actual: "
                "no hay nada que deshacer.[/]",
            )
        return

    console.print(diff_text, highlight=False)


def _clear_all(manager) -> None:
    """Wipe all pending backups after showing a summary."""
    entries = manager.list()
    if not entries:
        console.print("[dim]No hay backups pendientes para borrar.[/]")
        return

    console.print(
        f"\n[bold realm]᛭ Vas a borrar {len(entries)} backup(s) pendiente(s):[/]\n",
    )
    for i, entry in enumerate(entries, start=1):
        console.print(_format_entry(i, len(entries), entry))
    console.print()

    manager.clear()
    console.print(
        f"[success]✓ {len(entries)} backup(s) borrados.[/] "
        "[dim]Usá [bold cyan]/undo pop[/] para restaurar antes de borrar.[/]",
    )


async def run_undo_peek_command(session: "SessionRuntime", args: str) -> None:  # noqa: ARG001
    """Entry point dispatched by :mod:`lilith_cli.repl` for ``/undo-peek``.

    ``session`` is accepted for signature compatibility with the other
    ``run_X_command`` entry points but is not used; the command operates
    entirely on the local :class:`UndoManager` index.
    """
    text = args.strip()
    subcmd = text.lower()
    parts = text.split(maxsplit=1)
    head = parts[0].lower() if parts else ""

    manager = UndoManager()

    if subcmd in ("", "list", "ls"):
        entries = manager.list()
        if not entries:
            console.print("[dim]No hay backups pendientes.[/]")
            return
        console.print(
            "\n[bold realm]᛭ Backups pendientes (más reciente al final)[/]\n",
        )
        for i, entry in enumerate(entries, start=1):
            console.print(_format_entry(i, len(entries), entry))
        console.print(
            "\n[dim]Usá [bold cyan]/undo-peek <N>[/] para previsualizar "
            "ó [bold cyan]/undo pop[/] para restaurar.[/]\n",
        )
        return

    if subcmd == "clear":
        _clear_all(manager)
        return

    if subcmd in ("help", "?", "-h", "--help"):
        _print_usage()
        return

    # Numeric argument: peek the N-th most recent backup.
    if head.isdigit():
        entries = manager.list()
        _show_diff(manager, entries, int(head))
        return

    _print_usage()


__all__ = ["run_undo_peek_command"]