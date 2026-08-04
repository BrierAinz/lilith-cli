"""``/note`` slash command: persistent scratchpad for the current session.

The session itself lives in memory and ``/bookmark`` only anchors positions,
but during long sessions users routinely want to jot free-form text they can
refer back to later — "remember to add tests before commit", "ask the user
about the API key rotation policy", or "TODO: split this function".

The :class:`BookmarkCommand` covers *positional* anchors in the conversation
history. :class:`UndoCommand` covers *file* state. ``/note`` is the third
leg of the same stool: free-form textual notes that survive across ``/clear``
and are scoped per user (not per session) so you can build up a working
memory across an entire day.

Storage lives in ``~/.yggdrasil/notes.json`` — the same pattern used by
``/bookmark`` and ``/feedback``. Notes are never written to the repo or to
any project file, so this command is safe to invoke anywhere.

Subcommands:

* ``/note <text>...``  — append a note (the canonical form)
* ``/note list``       — show all notes (most recent last), one per line
* ``/note list --ids`` — list with explicit IDs (useful for ``rm``)
* ``/note search <q>`` — filter notes whose text contains ``q`` (case-insensitive)
* ``/note search <q> --ids`` — same as above with explicit IDs
* ``/note show <id>``  — print a single note in full
* ``/note edit <id> <text>...`` — replace the text of a note (timestamp updated)
* ``/note rm <id>``    — delete a single note
* ``/note clear``      — wipe every note (with a confirmation summary)
* ``/note help``       — show usage

Aliases: ``/note-add``, ``/notes``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import CONFIG_DIR
from .render import console, render_error

if TYPE_CHECKING:
    from .session_runtime import SessionRuntime


_NOTES_PATH: Path = CONFIG_DIR / "notes.json"

# Defensive cap on individual note length. Real notes are a sentence or two;
# anything past 4 KiB is almost certainly a paste accident.
_MAX_NOTE_CHARS = 4096

logger = logging.getLogger(__name__)


# ── Storage helpers ────────────────────────────────────────────────


def _load_notes() -> list[dict[str, Any]]:
    """Return notes from ``~/.yggdrasil/notes.json``, or ``[]`` if missing.

    Malformed files are treated as empty so a corrupted store cannot break
    the REPL — the user can ``/note clear`` to recover.
    """
    if not _NOTES_PATH.exists():
        return []
    try:
        data = json.loads(_NOTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Error cargando notes: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    return [n for n in data if isinstance(n, dict) and "text" in n]


def _save_notes(notes: list[dict[str, Any]]) -> None:
    """Persist ``notes`` to ``~/.yggdrasil/notes.json``.

    Created atomically via ``mkdir(parents=True)`` + ``write_text``.
    """
    _NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _NOTES_PATH.write_text(
        json.dumps(notes, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _next_id(notes: list[dict[str, Any]]) -> int:
    """Return the next sequential 1-based ID for a new note."""
    if not notes:
        return 1
    return max(int(n.get("id", 0)) for n in notes) + 1


def _find_note(notes: list[dict[str, Any]], note_id: int) -> dict[str, Any] | None:
    """Look up a note by 1-based ID, returning the dict or ``None``."""
    for n in notes:
        if int(n.get("id", -1)) == note_id:
            return n
    return None


# ── Rendering ──────────────────────────────────────────────────────


def _print_usage() -> None:
    """Print the ``/note`` usage block."""
    console.print(
        "\n[bold realm]᛭ /note — bloc de notas persistente entre sesiones[/]\n\n"
        "  [bold cyan]/note <texto>...[/]               — agrega una nota\n"
        "  [bold cyan]/note list[/]                    — lista todas las notas\n"
        "  [bold cyan]/note list --ids[/]              — lista con IDs al inicio\n"
        "  [bold cyan]/note search <texto>[/]          — filtra notas por texto (case-insensitive)\n"
        "  [bold cyan]/note search <texto> --ids[/]    — igual, con IDs\n"
        "  [bold cyan]/note show <id>[/]               — muestra una nota entera\n"
        "  [bold cyan]/note edit <id> <texto>...[/]    — reemplaza el texto\n"
        "  [bold cyan]/note rm <id>[/]                 — borra una nota\n"
        "  [bold cyan]/note clear[/]                   — borra todas las notas\n"
        "  [bold cyan]/note help[/]                    — esta ayuda\n\n"
        "[dim]Persiste en [bold cyan]~/.yggdrasil/notes.json[/]. "
        "Atajos: /note-add, /notes.[/]\n"
    )


def _render_notes_table(notes: list[dict[str, Any]], with_ids: bool) -> None:
    """Render the notes table in the REPL.

    ``with_ids=True`` prepends the numeric ID so ``rm`` / ``show`` are easy.
    """
    if not notes:
        console.print("[dim]No hay notas todavía. Probá /note tu primera idea.[/]")
        return

    console.print(
        f"\n[bold realm]᛭ Notas ({len(notes)}) — más reciente al final[/]\n"
    )
    for n in notes:
        ts = n.get("created", "?")
        # Trim ISO timestamps to "YYYY-MM-DD HH:MM" so rows fit comfortably.
        if isinstance(ts, str) and len(ts) >= 16:
            ts = ts[:16].replace("T", " ")
        text = str(n.get("text", ""))
        if len(text) > 200:
            text = text[:197] + "…"
        if with_ids:
            console.print(
                f"  [bold cyan]{n.get('id', '?'):>3}.[/] [dim]{ts}[/] · {text}"
            )
        else:
            console.print(f"  [dim]{ts}[/] · {text}")
    console.print()


def _render_single_note(note: dict[str, Any]) -> None:
    """Render one note in expanded form."""
    console.print(
        f"\n[bold realm]᛭ Nota {note.get('id')}[/] "
        f"[dim]{note.get('created', '?')}[/]\n"
    )
    console.print(note.get("text", ""))
    console.print()


# ── Subcommand handlers ────────────────────────────────────────────


def _add_note(text: str) -> None:
    """Append ``text`` to the store. Validates length and prints confirmation."""
    if not text.strip():
        render_error("La nota está vacía. Uso: /note <texto>")
        return
    if len(text) > _MAX_NOTE_CHARS:
        render_error(
            f"Nota demasiado larga ({len(text)} > {_MAX_NOTE_CHARS} chars). "
            "Dividila en varias notas más cortas."
        )
        return

    notes = _load_notes()
    note_id = _next_id(notes)
    stamp = datetime.now(UTC).isoformat()
    notes.append({"id": note_id, "text": text, "created": stamp})
    _save_notes(notes)

    preview = text if len(text) <= 80 else text[:77] + "…"
    console.print(
        f"[success]✓ Nota {note_id} guardada:[/] [dim]{preview}[/]"
    )


def _edit_note(note_id: int, new_text: str) -> None:
    """Replace the text of ``note_id`` with ``new_text``."""
    notes = _load_notes()
    note = _find_note(notes, note_id)
    if note is None:
        render_error(f"Nota {note_id} no encontrada.")
        return
    if not new_text.strip():
        render_error("El texto nuevo está vacío. Uso: /note edit <id> <texto>")
        return
    if len(new_text) > _MAX_NOTE_CHARS:
        render_error(
            f"Texto demasiado largo ({len(new_text)} > {_MAX_NOTE_CHARS} chars)."
        )
        return
    old_text = note.get("text", "")
    note["text"] = new_text
    note["updated"] = datetime.now(UTC).isoformat()
    _save_notes(notes)
    console.print(
        f"[success]✓ Nota {note_id} actualizada:[/] "
        f"[dim]{old_text[:40]}{'…' if len(old_text) > 40 else ''}[/] → "
        f"[bold cyan]{new_text[:60]}{'…' if len(new_text) > 60 else ''}[/]"
    )


def _rm_note(note_id: int) -> None:
    """Delete a single note by ID."""
    notes = _load_notes()
    for i, n in enumerate(notes):
        if int(n.get("id", -1)) == note_id:
            removed = notes.pop(i)
            _save_notes(notes)
            preview = removed.get("text", "")[:60]
            console.print(
                f"[success]✓ Nota {note_id} borrada:[/] [dim]{preview}…[/]"
            )
            return
    render_error(f"Nota {note_id} no encontrada.")


def _clear_all() -> None:
    """Wipe every note after printing a short summary."""
    notes = _load_notes()
    if not notes:
        console.print("[dim]No hay notas para borrar.[/]")
        return
    count = len(notes)
    console.print(
        f"\n[bold realm]᛭ Vas a borrar {count} nota(s):[/]\n"
    )
    for n in notes:
        preview = str(n.get("text", ""))[:60]
        console.print(
            f"  [bold cyan]{n.get('id', '?')}[/] · [dim]{preview}…[/]"
        )
    console.print()
    _save_notes([])
    console.print(
        f"[success]✓ {count} nota(s) borradas.[/] "
        "[dim]No se pueden recuperar — reescribilas si las necesitás.[/]"
    )


def _show_note(note_id: int) -> None:
    """Print a single note in expanded form."""
    notes = _load_notes()
    note = _find_note(notes, note_id)
    if note is None:
        render_error(f"Nota {note_id} no encontrada.")
        return
    _render_single_note(note)


def _search_notes(query: str, with_ids: bool) -> None:
    """Filter notes whose text contains ``query`` (case-insensitive) and render them.

    The match is a plain substring search on the note body; the query is
    stripped of the ``--ids`` / ``-i`` flag before matching.  When no notes
    match, a short hint is printed instead of an empty body — distinguishes
    "the store is empty" from "no match for that query" so the user knows
    which knob to turn next.
    """
    notes = _load_notes()
    if not notes:
        console.print("[dim]No hay notas todavía. Probá /note tu primera idea.[/]")
        return
    needle = query.lower()
    matches = [n for n in notes if needle in str(n.get("text", "")).lower()]
    if not matches:
        console.print(
            f"[dim]Sin coincidencias para «{query}». "
            f"Probá /note list para ver todas.[/dim]"
        )
        return
    _render_notes_table(matches, with_ids=with_ids)
    # Tiny footer so the user can tell search from list at a glance.
    console.print(
        f"[dim]Filtro aplicado: «{query}» — {len(matches)} de {len(notes)} "
        f"nota(s).[/dim]"
    )


# ── Entry point ────────────────────────────────────────────────────


async def run_note_command(session: "SessionRuntime", args: str) -> None:  # noqa: ARG001
    """Entry point dispatched by :mod:`lilith_cli.repl` for ``/note``.

    ``session`` is accepted for signature compatibility with the other
    ``run_X_command`` entry points; the command operates entirely on the
    local JSON store and never touches the session, the agent, or the repo.
    """
    text = args.strip()
    if not text or text.lower() in ("help", "?", "-h", "--help"):
        _print_usage()
        return

    # ``/note list [--ids]`` is the only subcommand with an inline flag.
    if text.lower().startswith("list"):
        rest = text[4:].strip().lower()
        _render_notes_table(_load_notes(), with_ids=rest in ("--ids", "-i"))
        return

    if text.lower() == "clear":
        _clear_all()
        return

    # ``/note search <query> [--ids|-i]`` is the only subcommand that takes
    # a free-form body, so it has to be matched BEFORE the generic split on
    # ``parts[0]`` (otherwise "search alpha" would be splitted into head="search"
    # and rest="alpha", but we want the rest to keep the trailing --ids flag).
    # We test against `text[len("search"):]` (case-insensitive) rather than the
    # whole text so that "search a b --ids" becomes query="a b", flag="--ids".
    if text.lower().startswith("search"):
        tail = text[len("search"):].strip()
        if not tail:
            render_error("Uso: /note search <texto> [--ids]")
            return
        lowered = tail.lower()
        if lowered.endswith("--ids") or lowered.endswith("-i"):
            flag = lowered[-len("--ids"):] if lowered.endswith("--ids") else "-i"
            query = tail[: len(tail) - len(flag) - 1].strip()
            _search_notes(query, with_ids=True)
            return
        _search_notes(tail, with_ids=False)
        return

    # ``/note show <id>``
    parts = text.split(maxsplit=1)
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if head == "show":
        try:
            note_id = int(rest.strip())
        except ValueError:
            render_error("Uso: /note show <id>")
            return
        _show_note(note_id)
        return

    if head in ("rm", "remove", "delete", "del"):
        try:
            note_id = int(rest.strip())
        except ValueError:
            render_error("Uso: /note rm <id>")
            return
        _rm_note(note_id)
        return

    if head == "edit":
        edit_parts = rest.split(maxsplit=1)
        if len(edit_parts) < 2 or not edit_parts[0].strip():
            render_error("Uso: /note edit <id> <texto>")
            return
        try:
            note_id = int(edit_parts[0].strip())
        except ValueError:
            render_error(f"ID inválido: {edit_parts[0]!r}")
            return
        _edit_note(note_id, edit_parts[1])
        return

    # Anything else is treated as a new note's body.
    _add_note(text)


__all__ = ["run_note_command"]