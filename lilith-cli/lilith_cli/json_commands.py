"""Comandos utilitarios de validacion y pretty-print de JSON.

Vivian dentro de ``extra_commands.py``. Se extrajeron aca para reducir ese
modulo (12.000 lineas, varios intentos previos de patch lo rompieron) y
agrupar comandos autocontenidos: no comparten helpers con el resto del
paquete.

``extra_commands`` los reexporta, asi que los imports existentes siguen
funcionando sin cambios.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .render import console, render_error


if TYPE_CHECKING:
    from .session_runtime import SessionRuntime


async def run_json_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Validate and pretty-print JSON (/json <text|file>)."""
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
    console.print(
        f"[info]Válido ({source}):[/info] "
        f"[bold cyan]{type_name}[/bold cyan] [dim]({size} items)[/dim]"
    )
    console.print(pretty)
    console.print()


__all__ = ["run_json_command"]
