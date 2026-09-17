"""Lectura de sesiones guardadas: el comando /transcript.

Por que existe (2026-09-15): las conversaciones se guardaban desde siempre
-- habia 422 ficheros en ~/.yggdrasil/conversations -- y NADA las leia. El
unico sitio que las abria era hearth.py, y para reanudar una sesion, no para
revisarla.

Hacia falta justo ahora porque el mismo dia se apagaron los paneles en vivo por
peticion del operador ("solo quiero su pensamiento y lo que dice al final").
Quitarle la ventana al proceso solo es razonable si queda la forma de mirarlo
despues; si no, es esconder la evidencia en vez de ordenarla.

Vive en su propio modulo y no en extra_commands.py a proposito: ese fichero
tenia cambios ajenos sin commitear y tocarlo habria mezclado trabajo de otro
con este.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .render import console, get_theme, render_error

_MAX_LISTADO = 20
_RECORTE = 2000


def _formatear_hora(marca: str) -> str:
    """Fecha legible a partir de una marca ISO o del formato compacto.

    Los ficheros guardados no usan ISO sino 20260915_194917_691086, asi que
    intentar solo fromisoformat dejaba la columna en crudo.
    """
    from datetime import datetime

    texto = str(marca or "").strip()
    if not texto:
        return "sin fecha"
    try:
        return datetime.fromisoformat(texto).strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        pass
    compacto = texto.replace("conv_", "")
    for formato in ("%Y%m%d_%H%M%S", "%Y%m%d%H%M%S", "%Y%m%d"):
        trozo = compacto[: len(datetime.now().strftime(formato))]
        try:
            return datetime.strptime(trozo, formato).strftime("%d %b %H:%M")
        except ValueError:
            continue
    return texto[:16]


def _listar(conversaciones: list[dict[str, Any]]) -> None:
    from rich.table import Table

    tabla = Table(show_header=True, header_style="tool.name", border_style="bark", expand=False)
    tabla.add_column("#", justify="right", style="tool.arg")
    tabla.add_column("Cuando", style="frost")
    tabla.add_column("Turnos", justify="right", style="tool.arg")
    tabla.add_column("Modelo", style="grove")
    tabla.add_column("Empezaba por", style="tool.result")

    for i, c in enumerate(conversaciones[:_MAX_LISTADO], start=1):
        tabla.add_row(
            str(i),
            _formatear_hora(c.get("timestamp", "")),
            str(c.get("message_count", "?")),
            str(c.get("model", "?")),
            str(c.get("preview") or c.get("task_request") or "—")[:58],
        )
    console.print(tabla)
    total = len(conversaciones)
    if total > _MAX_LISTADO:
        console.print(f"[dim]… y {total - _MAX_LISTADO} mas. /transcript N abre una.[/]")
    else:
        console.print("[dim]/transcript N abre una. /transcript N tools incluye las herramientas.[/]")


def _resumen_herramienta(llamada: dict[str, Any]) -> str:
    fn = llamada.get("function") or {}
    nombre = fn.get("name") or llamada.get("name") or "?"
    args = str(fn.get("arguments") or "")
    if len(args) > 70:
        args = args[:70] + "…"
    return f"{nombre} {args}".rstrip()


def _render(ruta: Path, con_herramientas: bool) -> None:
    import json

    from rich.rule import Rule

    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        render_error(f"No pude leer {ruta.name}: {exc}")
        return

    mensajes = datos.get("messages") or []
    tema = get_theme()
    runa = getattr(tema, "rune", None) or "·"

    console.print(Rule(f"{runa} {ruta.stem}", style="bark"))
    console.print(
        f"[dim]{_formatear_hora(datos.get('timestamp', ''))} · "
        f"{datos.get('model', '?')} vía {datos.get('provider', '?')} · "
        f"{len(mensajes)} mensajes[/]\n"
    )

    mostrados = 0
    for msg in mensajes:
        rol = msg.get("role")
        contenido = msg.get("content")
        contenido = contenido if isinstance(contenido, str) else ""
        llamadas = msg.get("tool_calls") or []

        if rol == "tool" and not con_herramientas:
            continue

        if rol == "user":
            console.print(f"[bold realm]▸ tú[/]  {contenido[:_RECORTE]}")
        elif rol == "assistant":
            if contenido.strip():
                console.print(f"[bold frost]◂ lilith[/]  {contenido[:_RECORTE]}")
            for c in llamadas:
                console.print(f"   [tool.name]ᛏ[/] [dim]{_resumen_herramienta(c)}[/]")
        elif rol == "tool":
            console.print(f"   [dim]↳ {contenido[:200]}[/]")
        elif rol == "system":
            if con_herramientas:
                console.print(f"[dim]⚙ sistema: {contenido[:200]}[/]")
            continue
        mostrados += 1
        console.print()

    if not mostrados:
        console.print("[dim]La sesión no tiene mensajes legibles.[/]")
    if not con_herramientas:
        console.print("[dim]Resultados de herramienta ocultos. Añade 'tools' para verlos.[/]")


async def run_transcript_command(session: Any, args: str) -> None:  # noqa: ARG001
    """Ejecuta /transcript para leer una sesión guardada.

    Examples:
        /transcript            — lista las sesiones guardadas
        /transcript 3          — abre la tercera de la lista
        /transcript 3 tools    — la abre incluyendo las herramientas
        /transcript conv_2026… — la abre por nombre
    """
    from .repl import _CONVERSATIONS_DIR, _list_saved_conversations

    piezas = args.split()
    con_herramientas = any(p.lower() in ("tools", "herramientas", "-v") for p in piezas)
    objetivo = next((p for p in piezas if p.lower() not in ("tools", "herramientas", "-v")), "")

    conversaciones = _list_saved_conversations()
    if not conversaciones:
        console.print(f"[dim]No hay sesiones guardadas en {_CONVERSATIONS_DIR}.[/]")
        return

    if not objetivo:
        _listar(conversaciones)
        return

    # Un numero es un indice SOLO si cae dentro de la lista. Los nombres de
    # sesion son numericos (conv_20260915_190741_...), asi que tratar cualquier
    # digito como indice hacia imposible buscar por un fragmento del nombre:
    # /transcript 190741 respondia "fuera de rango" en vez de abrir la sesion.
    if objetivo.isdigit() and 1 <= int(objetivo) <= len(conversaciones):
        _render(Path(conversaciones[int(objetivo) - 1]["file"]), con_herramientas)
        return

    for c in conversaciones:
        if objetivo in str(c.get("name", "")):
            _render(Path(c["file"]), con_herramientas)
            return
    if objetivo.isdigit():
        render_error(
            f"Fuera de rango: hay {len(conversaciones)} sesiones guardadas, "
            f"y {objetivo} no casa con ningún nombre."
        )
        return
    render_error(f"No encuentro una sesión que case con {objetivo!r}. /transcript las lista.")
