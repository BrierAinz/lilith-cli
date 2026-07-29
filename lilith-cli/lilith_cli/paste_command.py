"""``/paste`` slash command: leer el portapapeles del sistema y enviarlo al agente.

El companion natural de :func:`lilith_cli.extra_commands.run_copy_command`.
``/copy`` empuja el último mensaje del asistente al portapapeles; ``/paste``
tira lo que el usuario tenga copiado afuera y lo usa como prompt, de modo
que copy/paste entre Lilith y el resto del escritorio funciona igual que
en Claude Code, Codex y Gemini CLI.

Vive en su propio archivo y se despacha desde ``repl.py``, igual que los
comandos recientes (``/apply``, ``/note``, ``/bg``, ``/temperature``), de
modo que no hace falta tocar ``commands.py``.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from .agent import AgentSession


_MAX_BYTES = 64 * 1024  # 64 KiB — más que eso es casi seguro un accidente


def _is_wsl() -> bool:
    """``True`` si corremos bajo WSL (la clipboard de Windows requiere ``clip.exe``)."""
    try:
        return "microsoft" in open("/proc/version", encoding="utf-8").read().lower()
    except Exception:  # noqa: BLE001 — best-effort
        return False


def _read_clipboard() -> str | None:
    """Devuelve el contenido del portapapeles, o ``None`` si no se pudo leer.

    El orden de los probes es deliberado: empezamos por lo más nativo
    (``wl-paste`` en Wayland, ``pbpaste`` en macOS, ``powershell`` en
    Windows nativo), seguimos por las alternativas comunes (``xclip -o``,
    ``xsel --output``) y, sólo como último recurso, aceptamos ``xdotool``
    o devolvemos ``None`` para que el comando reporte el problema en
    lenguaje de usuario en vez de tirar stacktrace.
    """
    candidates: list[list[str]] = []

    # 1) Windows nativo: powershell Get-Clipboard
    if os.name == "nt":
        candidates.append(["powershell", "-NoProfile", "-Command", "Get-Clipboard"])

    # 2) macOS: pbpaste
    candidates.append(["pbpaste"])

    # 3) Wayland: wl-paste (con y sin --no-newline)
    candidates.append(["wl-paste"])
    candidates.append(["wl-paste", "--no-newline"])

    # 4) X11: xclip / xsel
    candidates.append(["xclip", "-selection", "clipboard", "-o"])
    candidates.append(["xsel", "--clipboard", "--output"])

    # 5) WSL: la clipboard de Windows via PowerShell.exe
    if _is_wsl():
        candidates.append(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"])

    for cmd in candidates:
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if completed.returncode == 0 and completed.stdout:
            return completed.stdout.decode("utf-8", errors="replace")

    return None


def _preview(text: str, limit: int = 120) -> str:
    """Una sola línea, apto para panel / log."""
    flat = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ⏎ ")
    flat = " ".join(flat.split())  # colapsa espacios múltiples
    if len(flat) > limit:
        return flat[: limit - 1] + "…"
    return flat


async def _send_to_agent(session: "AgentSession", text: str) -> None:
    """Reenvía ``text`` al loop del agente vía :meth:`AgentSession.process_message`.

    Si la sesión expone ``process_message_stream`` lo usamos; si no, caemos
    a ``process_message``. Cualquier excepción se propaga como ``render_error``
    desde el caller, no acá: queremos que el comando reporte el problema.
    """
    runner: Any = getattr(session, "process_message_stream", None)
    if not callable(runner):
        runner = getattr(session, "process_message", None)
    if not callable(runner):
        render_error(
            "Esta sesión no expone un punto de entrada público para prompts "
            "(`process_message` / `process_message_stream` no encontrados)."
        )
        return

    result = runner(text)
    if inspect.isawaitable(result):
        await result


async def run_paste_command(session: "AgentSession", args: str) -> None:
    """Lee el portapapeles y lo envía como prompt (``/paste [--prepend TEXTO]``).

    Examples:
        /paste               Envía el contenido del portapapeles al agente
        /paste --prepend "Resumí: "   Anteponer un prefijo al pegar
        /paste help          Mostrar ayuda
    """
    raw = args.strip()
    try:
        tokens = shlex.split(raw) if raw else []
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    if tokens and tokens[0].lower() in ("help", "--help", "-h", "?"):
        console.print("[bold realm]᛭ /paste — Pegar desde el portapapeles[/]")
        console.print()
        console.print("  [bold cyan]/paste[/]                  — Pegar y enviar al agente")
        console.print("  [bold cyan]/paste --prepend TEXTO[/]  — Anteponer un prefijo al pegar")
        console.print("  [bold cyan]/paste help[/]             — Esta ayuda")
        console.print()
        console.print(
            f"  [dim]Lectores: powershell · pbpaste · wl-paste · xclip · xsel · "
            f"powershell.exe (WSL). Límite: {_MAX_BYTES // 1024} KiB.[/dim]"
        )
        return

    prepend_parts: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--prepend":
            if i + 1 >= len(tokens):
                render_error("Falta el texto después de --prepend.")
                return
            prepend_parts.append(tokens[i + 1])
            i += 2
            continue
        render_error(f"Argumento desconocido: {tok!r}. Uso: /paste [--prepend TEXTO]")
        return

    text = await asyncio.to_thread(_read_clipboard)
    if text is None:
        render_error(
            "No pude leer el portapapeles. Probá instalar `wl-paste`, "
            "`xclip`, `pbpaste`, o PowerShell (en Windows)."
        )
        return
    if not text:
        render_error("El portapapeles está vacío.")
        return

    size_bytes = len(text.encode("utf-8"))
    if size_bytes > _MAX_BYTES:
        render_error(
            f"Portapapeles demasiado grande ({size_bytes / 1024:.1f} KiB). "
            f"Límite: {_MAX_BYTES // 1024} KiB — pegalo en fragmentos."
        )
        return

    final_text = ("".join(prepend_parts) + text) if prepend_parts else text

    console.print(
        f"[success]᛭ Portapapeles:[/success] "
        f"[dim]{size_bytes} bytes · {len(text.split())} palabras[/dim]"
    )
    console.print(f"  [dim]preview:[/dim] {_preview(text)}")
    if prepend_parts:
        console.print(f"  [dim]prepend:[/dim] {' / '.join(prepend_parts)}")

    await _send_to_agent(session, final_text)
