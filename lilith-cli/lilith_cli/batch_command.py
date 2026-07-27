"""Batch command: run multiple prompts sequentially from the REPL.

The ``/batch`` command lets users queue several natural-language prompts and
have Lilith execute them one after the other, sharing the same
:class:`AgentSession`. Each prompt is dispatched through the existing
:func:`repl.run_oneshot` so streaming and tool use behave exactly like a
one-shot ``lilith prompt`` invocation.

Batches can be supplied three ways:

* inline, separated by ``;;`` (``/batch hola;;chau;;resume``)
* from a file, one prompt per line (``/batch file prompts.txt``)
* from a saved batch in ``~/.yggdrasil/batches.json`` (``/batch run review``)

Storage mirrors the existing ``/pipeline`` command (JSON dict under
``~/.yggdrasil/``). Subcommands: ``list``, ``show``, ``run``, ``save``,
``delete``.

The command is intentionally read-only against the codebase: it only
orchestrates LLM turns, never touches files itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from .agent import AgentSession


_BATCH_DIR: Path = Path.home() / ".yggdrasil"
_BATCH_FILE: Path = _BATCH_DIR / "batches.json"


class _BatchStore:
    """Mutable wrapper so tests can inject a temporary batches file."""

    def __init__(self) -> None:
        self.batch_dir: Path = _BATCH_DIR
        self.batch_file: Path = _BATCH_FILE

    @property
    def path(self) -> Path:
        return self.batch_file

    def ensure(self) -> None:
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        if not self.batch_file.exists():
            self.batch_file.write_text(
                json.dumps({}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def load(self) -> dict[str, list[str]]:
        self.ensure()
        try:
            data = json.loads(self.batch_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): [str(p) for p in v] for k, v in data.items()}
        except Exception:
            pass
        return {}

    def save(self, batches: dict[str, list[str]]) -> None:
        self.ensure()
        self.batch_file.write_text(
            json.dumps(batches, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )


_BATCH_STORE = _BatchStore()


def _load_batches() -> dict[str, list[str]]:
    return _BATCH_STORE.load()


def _save_batches(batches: dict[str, list[str]]) -> None:
    _BATCH_STORE.save(batches)


def _parse_inline(text: str) -> list[str]:
    """Split ``;;``-separated prompts.

    Empty segments are dropped. Whitespace around each prompt is trimmed.
    The double-semicolon separator is rare enough in natural language that
    we don't bother with an escape syntax — users who need literal ``;;``
    should put the prompt in a file and use ``/batch file``.
    """
    parts = [seg.strip() for seg in text.split(";;")]
    return [seg for seg in parts if seg]


def _parse_file(path_text: str) -> list[str]:
    """Read prompts (one per line, blanks and ``#`` ignored) from a file."""
    raw = path_text.strip()
    # Allow ``file <path>`` and ``from <path>`` for ergonomics.
    for prefix in ("file ", "from ", "f "):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    p = Path(raw).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {p}")
    prompts: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        prompts.append(stripped)
    return prompts


async def _execute_batch(session: "AgentSession", name: str, prompts: list[str]) -> None:
    """Dispatch each prompt sequentially via :func:`repl.run_oneshot`."""
    from .repl import run_oneshot

    total = len(prompts)
    console.print(f"\n[bold realm]᛭ Batch: {name}[/] [dim]({total} prompts)[/]\n")
    failures: list[tuple[int, str, str]] = []
    started = 0
    for i, prompt in enumerate(prompts, start=1):
        console.print(f"[bold cyan]▶ [{i}/{total}][/] {prompt}")
        try:
            await run_oneshot(session, prompt)
            started += 1
        except Exception as exc:  # noqa: BLE001 — surface, don't crash REPL
            failures.append((i, prompt, str(exc)))
            render_error(f"Prompt {i} falló: {exc}")
            continue

    console.print()
    if failures:
        console.print(
            f"[warning]Batch {name} terminado: {started}/{total} OK, "
            f"{len(failures)} fallaron.[/]"
        )
    else:
        console.print(
            f"[success]✓ Batch {name} terminado: {started}/{total} OK.[/]"
        )


async def run_batch_command(session: "AgentSession", args: str) -> None:
    """Ejecuta ``/batch list|show|run|save|delete`` o dispatch inline.

    Examples:
        /batch
        /batch list
        /batch show review
        /batch run review
        /batch save review hola;;chau;;resume
        /batch save reviews file prompts.txt
        /batch delete review
        /batch hola mundo;;chau;;resume
        /batch file prompts.txt
    """
    text = args.strip()

    # No args → list.
    if not text or text.lower() in ("list", "ls"):
        batches = _load_batches()
        if not batches:
            console.print(
                "\n[dim]No hay batches guardados. "
                "Probá [bold cyan]/batch save review hola;;chau[/].[/]\n"
            )
            return
        console.print("\n[bold realm]᛭ Batches disponibles[/]\n")
        for name in sorted(batches):
            console.print(
                f"  [bold cyan]{name}[/] — [dim]{len(batches[name])} prompts[/]"
            )
        console.print(
            "\n[dim]Usá [bold cyan]/batch show <nombre>[/] o "
            "[bold cyan]/batch run <nombre>[/].[/]"
        )
        return

    parts = text.split(maxsplit=1)
    subcmd_raw = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    subcmd = subcmd_raw.lower()

    batches = _load_batches()

    if subcmd in ("show", "view"):
        if not rest:
            render_error("Uso: /batch show <nombre>")
            return
        name = rest.strip()
        if name not in batches:
            render_error(f"Batch no encontrado: [model]{name}[/]")
            return
        console.print(f"\n[bold realm]᛭ Batch: {name}[/]\n")
        for i, prompt in enumerate(batches[name], start=1):
            console.print(f"  [bold cyan]{i}.[/] {prompt}")
        console.print()
        return

    if subcmd == "run":
        if not rest:
            render_error("Uso: /batch run <nombre>")
            return
        name = rest.strip()
        if name not in batches:
            render_error(f"Batch no encontrado: [model]{name}[/]")
            return
        await _execute_batch(session, name, batches[name])
        return

    if subcmd in ("delete", "rm", "remove"):
        if not rest:
            render_error("Uso: /batch delete <nombre>")
            return
        name = rest.strip()
        if name not in batches:
            render_error(f"Batch no encontrado: [model]{name}[/]")
            return
        del batches[name]
        _save_batches(batches)
        console.print(f"[warning]✗ Batch eliminado: [model]{name}[/]")
        return

    if subcmd == "save":
        if not rest:
            render_error(
                "Uso: /batch save <nombre> <prompts separados por ;; "
                "o 'file <ruta>'>"
            )
            return
        name_parts = rest.split(maxsplit=1)
        name = name_parts[0].strip()
        body = name_parts[1] if len(name_parts) > 1 else ""
        if not name or not body:
            render_error(
                "Uso: /batch save <nombre> <prompts separados por ;; "
                "o 'file <ruta>'>"
            )
            return
        if body.lower().startswith(("file ", "from ", "f ")):
            try:
                prompts = _parse_file(body)
            except FileNotFoundError as exc:
                render_error(str(exc))
                return
        else:
            prompts = _parse_inline(body)
        if not prompts:
            render_error("El batch no contiene prompts.")
            return
        batches[name] = prompts
        _save_batches(batches)
        console.print(
            f"[success]✓ Batch guardado: [model]{name}[/] "
            f"({len(prompts)} prompts)"
        )
        return

    if subcmd in ("file", "from", "f"):
        try:
            prompts = _parse_file(rest)
        except FileNotFoundError as exc:
            render_error(str(exc))
            return
        if not prompts:
            render_error("El archivo no contiene prompts.")
            return
        await _execute_batch(session, "inline", prompts)
        return

    # Otherwise, treat the whole input as inline ``prompt1;;prompt2``.
    prompts = _parse_inline(text)
    if not prompts:
        render_error(
            "Uso: /batch [list|show|run|save|delete] "
            "o /batch prompt1;;prompt2;;prompt3"
        )
        return
    await _execute_batch(session, "inline", prompts)


__all__ = [
    "run_batch_command",
    "_parse_inline",
    "_parse_file",
    "_load_batches",
    "_save_batches",
    "_BatchStore",
    "_BATCH_FILE",
]