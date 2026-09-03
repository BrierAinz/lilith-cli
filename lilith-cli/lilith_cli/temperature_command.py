"""/temperature: leer y ajustar la temperatura de sampling en caliente.

``config.temperature`` ya viajaba a todos los proveedores (``providers.py``
la pasa como default en cada request), pero no había forma de verla ni de
cambiarla sin editar ``config.yaml`` y reiniciar. Todo el resto de la
configuración de modelo tiene comando —``/model``, ``/provider``,
``/model-info``— y la temperatura era la excepción invisible.

Vive en su propio archivo y se despacha desde ``repl.py`` como el resto de
los comandos recientes, de modo que no hace falta tocar ``commands.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import save_config
from .render import console, render_error

if TYPE_CHECKING:
    from .session_runtime import SessionRuntime


# Rango aceptado por las APIs tipo OpenAI/Anthropic. Por encima de ~1.2 la
# salida se vuelve incoherente, pero el límite duro es lo que rechaza el
# servidor, así que validamos contra eso y avisamos del resto.
_MIN = 0.0
_MAX = 2.0
_DEFAULT = 0.7
_ALTA = 1.2


def _es_kimi(session: "SessionRuntime") -> bool:
    """¿El proveedor activo ignora la temperatura?

    ``kimi-for-coding`` devuelve 400 con cualquier valor que no sea 1.0, así
    que ``providers.py`` la fuerza. Conviene decirlo en vez de fingir que el
    cambio tuvo efecto.
    """
    base = getattr(session.config, "base_url", None) or ""
    if not base:
        provider = getattr(session, "provider", None)
        resolver = getattr(provider, "_resolve_base_url", None)
        if callable(resolver):
            try:
                base = resolver() or ""
            except Exception:  # noqa: BLE001 — nunca romper por introspección
                base = ""
    return "kimi.com" in str(base).lower()


def _mostrar(session: "SessionRuntime") -> None:
    actual = getattr(session.config, "temperature", _DEFAULT)
    console.print(f"[info]᛭ Temperatura:[/info] [model]{actual}[/]")
    console.print(
        f"  [dim]rango {_MIN}–{_MAX} · por defecto {_DEFAULT} · "
        f"más alto = más aleatorio[/]"
    )
    if _es_kimi(session):
        console.print(
            "  [warning]⚠ El proveedor activo (kimi) fuerza temperature=1.0; "
            "este valor no se aplica.[/]"
        )


async def run_temperature_command(session: "SessionRuntime", args: str) -> None:
    """Muestra o ajusta la temperatura de sampling (/temperature [valor])."""
    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        _mostrar(session)
        return

    if text.lower() in ("help", "--help", "-h", "?"):
        console.print("[bold realm]᛭ /temperature — Temperatura de sampling[/]")
        console.print()
        console.print("  [bold cyan]/temperature[/]           → Ver el valor actual")
        console.print("  [bold cyan]/temperature 0.3[/]       → Fijar un valor")
        console.print("  [bold cyan]/temperature reset[/]     → Volver al por defecto")
        console.print("  [bold cyan]/temperature 0.3 --save[/] → Fijar y persistir")
        console.print()
        return

    tokens = text.split()
    persistir = "--save" in tokens
    tokens = [t for t in tokens if t != "--save"]
    if not tokens:
        render_error("Uso: /temperature <valor> [--save]")
        return

    crudo = tokens[0]
    if crudo.lower() == "reset":
        valor = _DEFAULT
    else:
        try:
            valor = float(crudo.replace(",", "."))
        except ValueError:
            render_error(f"Valor inválido: {crudo!r}. Usá un número entre {_MIN} y {_MAX}.")
            return

    if not (_MIN <= valor <= _MAX):
        render_error(f"Fuera de rango: {valor}. Debe estar entre {_MIN} y {_MAX}.")
        return

    anterior = getattr(session.config, "temperature", _DEFAULT)
    session.config.temperature = valor
    console.print(f"[success]✓ Temperatura: [model]{anterior}[/] → [model]{valor}[/][/]")

    if valor >= _ALTA:
        console.print(
            "  [warning]⚠ Por encima de 1.2 la salida suele volverse incoherente.[/]"
        )
    if _es_kimi(session):
        console.print(
            "  [warning]⚠ El proveedor activo (kimi) fuerza temperature=1.0; "
            "el cambio no llegará al modelo.[/]"
        )

    if persistir:
        try:
            save_config(session.config)
        except Exception as exc:  # noqa: BLE001 — el cambio en runtime ya se aplicó
            render_error(f"No se pudo persistir en config.yaml: {exc}")
            return
        console.print("  [dim]Guardado en config.yaml.[/]")
