"""Onboarding and personal slash commands: /tip, /tour, /learn, /feedback, /log, /qr, /voice and /timer."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import CONFIG_DIR
from ..render import console, render_error

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime
from .._learn_section import SkillSuggestion, save_suggestion, suggest_from_state_path
from ._shared import _format_history_timestamp

# ── Tip helpers ──────────────────────────────────────────────────────

_DEFAULT_TIPS: list[str] = [
    "Usá /theme nord para cambiar al tema inspirado en el Ártico.",
    "Con /env prefix PYTHON listás todas las variables de entorno que empiezan con PYTHON.",
    "/compact resumí los últimos mensajes de la conversación para ahorrar contexto.",
    "Grabá macros con /macro record y ejecutalas con /macro play <nombre>.",
    "/status muestra el estado general de la sesión, incluyendo tokens y herramientas.",
    "Usá /search across para buscar patrones en todos los archivos del proyecto.",
    "Exportá la conversación con /export y cargala después con /load.",
    "El comando /watch vigila cambios de archivos y los reporta en tiempo real.",
    "Con /pin fijás mensajes importantes para que no se pierdan al compactar.",
    "/bench mide latencias del proveedor actual para comparar configuraciones.",
]
_TIPS_PATH = CONFIG_DIR / "tips.json"

# LILITH_TIPS starts as the bundled defaults; user-added tips are loaded
# from disk on first access via _ensure_tips_loaded() and persisted on
# /tip add so they survive across REPL restarts (previously they lived
# only in-process and leaked across sessions in the same run).
LILITH_TIPS: list[str] = list(_DEFAULT_TIPS)
_TIPS = LILITH_TIPS  # back-compat alias
_TIPS_LOADED = False


def _ensure_tips_loaded() -> None:
    """Lazy-load user tips from ~/.yggdrasil/tips.json on first access."""
    global _TIPS_LOADED
    if _TIPS_LOADED:
        return
    _TIPS_LOADED = True
    try:
        if _TIPS_PATH.exists():
            data = json.loads(_TIPS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Append user tips after the bundled defaults, skipping any
                # that exactly match a default (idempotent across sessions).
                for tip in data:
                    if isinstance(tip, str) and tip not in _DEFAULT_TIPS:
                        LILITH_TIPS.append(tip)
    except Exception as exc:
        console.print(f"[warning]tips.json ilegible ({exc}); usando defaults.[/]")


def _save_user_tips() -> None:
    """Persist only the user-added tips (not the bundled defaults)."""
    user_tips = [t for t in LILITH_TIPS if t not in _DEFAULT_TIPS]
    try:
        _TIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TIPS_PATH.write_text(
            json.dumps(user_tips, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        console.print(
            f"[warning]No pude persistir el consejo en {_TIPS_PATH.name}: {exc}[/]"
        )


async def run_tip_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Show, list, count, or add tips (/tip [n|list|count|add <texto>])."""
    _ensure_tips_loaded()
    raw = args.strip()
    text = raw.lower()

    if text in ("list", "ls"):
        console.print("\n[bold realm]᛭ Consejos disponibles[/]")
        for i, tip in enumerate(LILITH_TIPS, start=1):
            console.print(f"  [bold cyan]{i}.[/] {tip}")
        console.print()
        return

    if text == "count":
        console.print(f"[info]Hay[/info] [bold cyan]{len(LILITH_TIPS)}[/] [info]consejos en LILITH_TIPS.[/info]")
        return

    if text.startswith("add "):
        new_tip = raw[4:].strip()
        if not new_tip:
            render_error("Uso: /tip add <texto del consejo>")
            return
        LILITH_TIPS.append(new_tip)
        _save_user_tips()
        console.print(f"[success]✓ Consejo añadido (total: {len(LILITH_TIPS)})[/success]")
        return

    if text:
        try:
            index = int(text)
            if index < 1 or index > len(LILITH_TIPS):
                render_error(f"Índice fuera de rango: {index}")
                return
            tip = LILITH_TIPS[index - 1]
        except ValueError:
            render_error("Uso: /tip [número|list|count|add <texto>]")
            return
    else:
        tip = random.choice(LILITH_TIPS)

    console.print(f"\n[bold realm]᛭ Consejo[/]\n[tool.result]{tip}[/]\n")


# ── /tour command ───────────────────────────────────────────────────────

# Tour steps. Names + bodies reference real slash commands so the tour
# doesn't drift from what the registry actually exposes; if a command
# disappears, /tour will say so on next launch instead of staying stale.
_TOUR_STEPS: list[tuple[str, str]] = [
    (
        "Bienvenido a Lilith",
        "Lilith es el agente CLI de Yggdrasil. Este recorrido te muestra las funciones principales en 5 pasos.\n"
        "Usá /tour step N para saltar a un paso, o /tour skip para salir.",
    ),
    (
        "Seguridad: confirm_write y undo",
        "Antes de escribir o editar archivos, Lilith puede mostrar un diff para confirmar.\n"
        "Si algo sale mal, /undo deshace el último cambio de archivo automáticamente.",
    ),
    (
        "Herramientas principales",
        "read_file, write_file y patch manejan archivos; /test ejecuta pruebas; "
        "/git cubre git, /search busca en archivos.",
    ),
    (
        "Comandos de barra",
        "/help lista todos los comandos; /tools habilita/deshabilita herramientas; "
        "/cost y /metrics muestran uso de tokens y costos.",
    ),
    (
        "Funciones avanzadas",
        "/export guarda la conversación, /load la restaura; "
        "/bookmark marca puntos de interés; /compact resume el historial.",
    ),
]


async def run_tour_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /tour para iniciar un recorrido interactivo por Lilith.

    Subcomandos disponibles:

    - (sin args)              → muestra todos los pasos seguidos
    - ``list``                → lista los títulos numerados (sin contenido)
    - ``step N``              → salta al paso N
    - ``next`` / ``prev`` / ``n`` / ``p``  → navega un paso (estado en sesión)
    - ``skip``                → cancela el recorrido activo
    - ``reset``               → reinicia el cursor del recorrido al paso 1

    El estado ``next``/``prev`` se guarda en ``session._tour_cursor`` para
    que la navegación sea persistente mientras el REPL siga vivo.

    Examples:
        /tour
        /tour list
        /tour step 2
        /tour next
        /tour prev
        /tour skip
    """
    text = args.strip().lower()

    if text in ("skip", "cancel", "quit", "exit"):
        console.print("\n[dim]Recorrido cancelado.[/]\n")
        _reset_tour_cursor(session)
        return

    if text in ("list", "ls", "index", "titles"):
        _render_tour_index()
        return

    if text in ("reset", "rewind", "restart"):
        _reset_tour_cursor(session)
        console.print(
            f"[dim]Cursor del recorrido reiniciado al paso 1 de {len(_TOUR_STEPS)}.[/dim]"
        )
        return

    if text in ("next", "n", "forward", ">>"):
        cursor = _get_tour_cursor(session)
        if cursor is None:
            cursor = 0
        next_step = cursor + 1
        if next_step > len(_TOUR_STEPS):
            render_error(
                f"Ya estás en el último paso ({len(_TOUR_STEPS)}). "
                "Usá /tour reset para volver al inicio."
            )
            return
        _render_tour_step(next_step)
        _set_tour_cursor(session, next_step)
        return

    if text in ("prev", "previous", "p", "back", "<<"):
        cursor = _get_tour_cursor(session)
        if cursor is None or cursor <= 1:
            render_error(
                "Estás en el primer paso. Usá /tour step 1 para repetirlo."
            )
            return
        prev_step = cursor - 1
        _render_tour_step(prev_step)
        _set_tour_cursor(session, prev_step)
        return

    if text.startswith("step"):
        rest = text[4:].strip()
        try:
            step = int(rest)
        except ValueError:
            render_error("Uso: /tour step <número>")
            return
        if step < 1 or step > len(_TOUR_STEPS):
            render_error(f"Paso inválido: {step}. El recorrido tiene 1-{len(_TOUR_STEPS)}.")
            return
        _render_tour_step(step)
        _set_tour_cursor(session, step)
        return

    if text == "help":
        _render_tour_help()
        return

    if text:
        render_error(
            "Uso: /tour [list|step N|next|prev|reset|skip|help]"
        )
        return

    console.print("\n[bold realm]᛭ Recorrido interactivo de Lilith[/]")
    for i in range(1, len(_TOUR_STEPS) + 1):
        _render_tour_step(i)
    _set_tour_cursor(session, len(_TOUR_STEPS))
    console.print(
        "[dim]Recorrido completado. Usá /tour step N para repetir un paso o "
        "/tour reset para volver al inicio.[/]\n"
    )


def _get_tour_cursor(session: SessionRuntime) -> int | None:
    """Devuelve el cursor actual del recorrido (1-based) o ``None``."""
    return getattr(session, "_tour_cursor", None)


def _set_tour_cursor(session: SessionRuntime, step: int) -> None:
    """Persiste el cursor del recorrido en la sesión."""
    try:
        session._tour_cursor = step  # type: ignore[attr-defined]
    except Exception:
        # En sesiones mock sin __dict__ escribible, ignorar silenciosamente.
        pass


def _reset_tour_cursor(session: SessionRuntime) -> None:
    """Limpia el cursor del recorrido en la sesión."""
    try:
        if hasattr(session, "_tour_cursor"):
            del session._tour_cursor  # type: ignore[attr-defined]
    except Exception:
        pass


def _render_tour_index() -> None:
    """Lista los títulos numerados del recorrido sin volcar el contenido."""
    console.print(
        f"\n[bold realm]᛭ Recorrido de Lilith[/] [dim]({len(_TOUR_STEPS)} pasos)[/]\n"
    )
    for i, (title, _body) in enumerate(_TOUR_STEPS, start=1):
        console.print(f"  [bold cyan]{i}.[/] {title}")
    console.print(
        "\n[dim]Usá /tour step N para ver un paso, o /tour next/prev para navegar.[/]\n"
    )


def _render_tour_help() -> None:
    """Imprime la ayuda del comando /tour."""
    console.print("\n[bold realm]᛭ /tour[/bold realm] [dim]— recorrido interactivo[/dim]\n")
    console.print("[bold]Subcomandos:[/bold]")
    console.print("  [cyan]/tour[/]             [dim]# muestra los 5 pasos seguidos[/dim]")
    console.print("  [cyan]/tour list[/]         [dim]# lista los títulos numerados[/dim]")
    console.print("  [cyan]/tour step <n>[/]     [dim]# salta al paso n (1-{})[/dim]".format(len(_TOUR_STEPS)))
    console.print("  [cyan]/tour next[/]         [dim]# avanza un paso[/dim]")
    console.print("  [cyan]/tour prev[/]         [dim]# retrocede un paso[/dim]")
    console.print("  [cyan]/tour reset[/]        [dim]# vuelve al paso 1[/dim]")
    console.print("  [cyan]/tour skip[/]         [dim]# cancela el recorrido[/dim]")
    console.print("  [cyan]/tour help[/]         [dim]# esta ayuda[/dim]\n")


def _render_tour_step(step: int) -> None:
    """Renderiza un paso del recorrido en la consola."""
    title, body = _TOUR_STEPS[step - 1]
    console.print(f"\n[bold cyan]Paso {step}/{len(_TOUR_STEPS)}: {title}[/]")
    console.print(f"[tool.result]{body}[/]")
    if step < len(_TOUR_STEPS):
        console.print("[dim]Escribí /tour para continuar con el recorrido completo.[/]")
    console.print()


# ── /voice (TTS via PowerShell System.Speech on Windows) ───────────────────


def _speak_text(text: str) -> bool:
    """Speak *text* via TTS. Returns True on success, False on failure.

    Falls back to:
    - Windows: PowerShell with System.Speech.Synthesis
    - macOS:  say command
    - Linux:  espeak-ng or spd-say
    """
    import platform
    import subprocess

    text = (text or "").strip()
    if not text:
        return False

    system = platform.system()
    try:
        if system == "Windows":
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "(New-Object System.Speech.Synthesis.SpeechSynthesizer).SpeakTime = 0; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.Speak([Console]::In.ReadToEnd())"
            )
            # Actually just speak the text
            safe = text.replace('"', '`"').replace('$', '`$')
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak(\"{safe}\")"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                timeout=30,
            )
            return True
        elif system == "Darwin":
            subprocess.run(["say", text], capture_output=True, timeout=30)
            return True
        else:
            # Linux: try espeak-ng, fallback to spd-say
            for cmd in (["espeak-ng"], ["espeak"], ["spd-say", "--"]):
                try:
                    subprocess.run(
                        cmd + [text],
                        capture_output=True,
                        timeout=30,
                    )
                    return True
                except FileNotFoundError:
                    continue
            return False
    except Exception:
        return False


async def run_voice_command(session: SessionRuntime, args: str) -> None:
    """TTS toggle (/voice [on|off|status|test <text>])."""
    text = args.strip()
    state = getattr(session, "_voice_enabled", False)

    if not text or text == "status":
        status = "ON" if state else "OFF"
        console.print(f"[bold]Voice mode:[/] {status}")
        return

    if text == "on":
        session._voice_enabled = True
        console.print("[success]✓ Voice mode activado[/success]")
        # Confirm with TTS
        _speak_text("Voice mode enabled.")
        return

    if text == "off":
        session._voice_enabled = False
        console.print("[warning]✗ Voice mode desactivado[/warning]")
        return

    if text.startswith("test "):
        phrase = text[5:].strip() or "Hola, soy Lilith"
        console.print(f"[info]Reproduciendo: {phrase}[/info]")
        ok = _speak_text(phrase)
        if ok:
            console.print("[success]✓ Audio reproducido[/success]")
        else:
            console.print("[error]No hay motor TTS disponible[/error]")
        return

    # Default: treat whole arg as a phrase and speak
    console.print(f"[info]Reproduciendo: {text}[/info]")
    if _speak_text(text):
        console.print("[success]✓ Audio reproducido[/success]")
    else:
        console.print("[error]No hay motor TTS disponible[/error]")


# ── /hash command ───────────────────────────────────────────────


# ── /lines command ───────────────────────────────────────────────────


# ── /base64 command ────────────────────────────────────────────────────


# ── /uuid command ───────────────────────────────────────────────────────────


# ── /qr command ─────────────────────────────────────────────────────────────────

_QR_LAST_FILE = CONFIG_DIR / "qr_last.json"
_QR_PREFS_FILE = CONFIG_DIR / "qr.json"


def _qr_usage() -> str:
    """Cadena de uso en español para /qr."""
    return (
        "Uso: /qr <texto> [--save <ruta.png>] [--last] "
        "[--error-correction L|M|Q|H] [--box-size N] [--border N] [--help]"
    )


def _load_qr_last() -> dict:
    """Carga el último texto y opciones de QR generados."""
    if not _QR_LAST_FILE.exists():
        return {}
    try:
        data = json.loads(_QR_LAST_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def _save_qr_last(payload: dict) -> None:
    """Persiste el último texto y opciones del QR generado."""
    _QR_LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QR_LAST_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_qr_prefs() -> dict:
    """Carga preferencias persistidas del usuario para /qr."""
    if not _QR_PREFS_FILE.exists():
        return {}
    try:
        data = json.loads(_QR_PREFS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def _save_qr_prefs(prefs: dict) -> None:
    """Persiste preferencias del usuario para /qr."""
    _QR_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QR_PREFS_FILE.write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _qr_resolve_ec(letter: str) -> int:
    """Resuelve el identificador numérico de corrección de errores.

    Se importa ``qrcode.constants`` perezosamente para no penalizar el
    arranque si el usuario nunca invoca /qr.
    """
    import qrcode.constants as _qc  # type: ignore

    return {
        "L": _qc.ERROR_CORRECT_L,
        "M": _qc.ERROR_CORRECT_M,
        "Q": _qc.ERROR_CORRECT_Q,
        "H": _qc.ERROR_CORRECT_H,
    }.get(letter.upper(), _qc.ERROR_CORRECT_M)


def _render_qr_ascii(
    text: str,
    ec: int,
    box_size: int,
    border: int,
) -> str:
    """Genera el ASCII de un código QR para mostrarlo en la terminal."""
    import io

    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=ec,
        box_size=box_size,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, tty=False, invert=False)
    return buf.getvalue()


async def run_qr_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Genera códigos QR en la terminal o como PNG.

    Examples:
        /qr https://example.com
        /qr https://example.com --save qr.png
        /qr --last
        /qr "hola mundo" --error-correction H --box-size 4 --border 2
        /qr --help
    """
    try:
        import qrcode.exceptions
    except ImportError:
        render_error(
            "/qr necesita el paquete qrcode. Instálalo con: "
            "uv sync --package lilith-cli --extra qr"
        )
        return

    text = args.strip()

    # ── Sin args / ayuda ────────────────────────────────────────
    if not text:
        render_error(_qr_usage())
        return

    if text in ("--help", "-h", "help"):
        console.print(f"[info]{_qr_usage()}[/info]")
        console.print()
        console.print("[dim]Genera un QR en la terminal a partir de <texto>.[/dim]")
        console.print("[dim]--save <ruta.png>  Guarda el QR como PNG en la ruta indicada.[/dim]")
        console.print("[dim]--last             Muestra el último QR generado (persiste entre sesiones).[/dim]")
        console.print("[dim]--error-correction Nivel de corrección: L|M|Q|H (por defecto M).[/dim]")
        console.print("[dim]--box-size         Tamaño de caja para terminal (por defecto 2).[/dim]")
        console.print("[dim]--border           Margen en módulos (por defecto 1 en terminal, 4 en PNG).[/dim]")
        console.print("[dim]Preferencias por defecto se guardan en ~/.yggdrasil/qr.json[/dim]")
        console.print()
        return

    # ── /qr --last ───────────────────────────────────────────────
    if text == "--last" or text.startswith("--last "):
        last = _load_qr_last()
        if not last or not last.get("text"):
            render_error("No hay un QR previo guardado. Usa /qr <texto> primero.")
            return
        previous = str(last.get("text", ""))
        saved_path = last.get("saved_path")
        ec_letter = str(last.get("ec", "M"))
        try:
            box_size = int(last.get("box_size", 2))
            border = int(last.get("border", 1))
        except (TypeError, ValueError):
            box_size, border = 2, 1

        console.print(
            f"[info]Re-renderizando último QR (EC={ec_letter}, texto «{previous}»)[/info]"
        )
        try:
            ec = _qr_resolve_ec(ec_letter)
            rendered = _render_qr_ascii(previous, ec, box_size, border)
        except qrcode.exceptions.DataOverflowError:
            render_error(
                "El último texto guardado es demasiado largo para un QR "
                "con los parámetros actuales."
            )
            return
        except Exception as exc:  # pragma: no cover - defensivo
            render_error(f"Error re-renderizando QR: {exc}")
            return

        console.print(rendered)
        if saved_path:
            console.print(f"[dim]Guardado previamente en: {saved_path}[/dim]")
        console.print()
        return

    # ── Parseo de argumentos ────────────────────────────────────
    import argparse as _argparse
    import shlex as _shlex

    parser = _argparse.ArgumentParser(prog="/qr", add_help=False)
    parser.add_argument("text", nargs="?")
    parser.add_argument("--save", dest="save_path", default=None)
    parser.add_argument(
        "--error-correction",
        dest="ec",
        default=None,
        help="Nivel L|M|Q|H",
    )
    parser.add_argument("--box-size", dest="box_size", type=int, default=None)
    parser.add_argument("--border", dest="border", type=int, default=None)

    try:
        # En Windows conservamos las barras invertidas (posix=False) para no
        # romper rutas tipo ``C:\Users\...\qr.png``.
        tokens = _shlex.split(text, posix=not sys.platform.startswith("win"))
    except ValueError as exc:
        render_error(f"Error parseando argumentos: {exc}")
        return

    try:
        parsed, _unknown = parser.parse_known_args(tokens)
    except SystemExit:
        render_error(_qr_usage())
        return

    if not parsed.text:
        render_error(_qr_usage())
        return

    prefs = _load_qr_prefs()
    ec_letter = (parsed.ec or prefs.get("error_correction") or "M").upper()
    if ec_letter not in ("L", "M", "Q", "H"):
        render_error(
            f"Nivel de corrección inválido: {parsed.ec!r}. Usa L, M, Q o H."
        )
        return
    ec = _qr_resolve_ec(ec_letter)

    if parsed.save_path:
        default_box, default_border = 10, 4
    else:
        default_box, default_border = 2, 1

    box_size = (
        parsed.box_size
        if parsed.box_size is not None
        else int(prefs.get("box_size", default_box))
    )
    border = (
        parsed.border
        if parsed.border is not None
        else int(prefs.get("border", default_border))
    )
    if box_size < 1 or border < 0:
        render_error("--box-size debe ser ≥ 1 y --border ≥ 0")
        return

    # ── Render en terminal ───────────────────────────────────────
    if not parsed.save_path:
        try:
            rendered = _render_qr_ascii(parsed.text, ec, box_size, border)
        except qrcode.exceptions.DataOverflowError:
            render_error(
                "El texto es demasiado largo para caber en un QR con la "
                "corrección de errores y el tamaño de caja actuales. "
                "Prueba con --error-correction L o reduce el texto."
            )
            return
        except Exception as exc:  # pragma: no cover - defensivo
            render_error(f"Error generando QR: {exc}")
            return

        console.print(rendered)
        console.print(
            f"[dim]QR ({len(parsed.text)} caracteres, EC={ec_letter}, "
            f"box={box_size}, border={border})[/dim]"
        )

        _save_qr_last(
            {
                "text": parsed.text,
                "ec": ec_letter,
                "box_size": box_size,
                "border": border,
                "saved_path": None,
                "ts": datetime.now(UTC).isoformat(),
            }
        )
        _save_qr_prefs(
            {
                "error_correction": ec_letter,
                "box_size": box_size,
                "border": border,
            }
        )
        console.print()
        return

    # ── Guardar como PNG ─────────────────────────────────────────
    import qrcode

    save_target = Path(parsed.save_path).expanduser()
    try:
        save_target.parent.mkdir(parents=True, exist_ok=True)
        img = qrcode.make(
            parsed.text,
            error_correction=ec,
            border=border,
            box_size=box_size,
        )
        img.save(str(save_target))
    except (OSError, ValueError) as exc:
        render_error(f"No se pudo guardar el PNG en {save_target}: {exc}")
        return
    except Exception as exc:  # pragma: no cover - defensivo
        render_error(f"Error generando PNG: {exc}")
        return

    console.print(
        f"[success]✓ QR guardado en [bold cyan]{save_target}[/bold cyan][/success]"
    )
    console.print(
        f"[dim]({len(parsed.text)} caracteres, EC={ec_letter}, "
        f"box={box_size}, border={border})[/dim]"
    )

    _save_qr_last(
        {
            "text": parsed.text,
            "ec": ec_letter,
            "box_size": box_size,
            "border": border,
            "saved_path": str(save_target),
            "ts": datetime.now(UTC).isoformat(),
        }
    )
    _save_qr_prefs(
        {
            "error_correction": ec_letter,
            "box_size": box_size,
            "border": border,
        }
    )
    console.print()


# ── /log command ───────────────────────────────────────────────────────
#
# Muestra un resumen paginado de la sesión activa, distinto a /history.
# Incluye cabecera con metadatos de sesión, una línea de tiempo compacta
# por turno y conteos agregados (turnos del usuario / asistente, llamadas
# a herramientas con desglose por nombre, errores). Subcomandos: stats,
# clear (archivo de log en disco, no session.history), help, path.

_LOG_FILE = CONFIG_DIR / "session.log"


def _clear_log_file() -> bool:
    """Borra el archivo de log en disco si existe. Devuelve True si borró algo.

    Esta función es deliberadamente no destructiva: si el archivo no
    existe, devuelve False y el caller debe emitir un mensaje amable.
    """
    try:
        if _LOG_FILE.exists():
            _LOG_FILE.unlink()
            return True
    except OSError as exc:  # pragma: no cover — defensivo
        logger = logging.getLogger(__name__)
        logger.warning("No se pudo borrar el log: %s", exc)
    return False


def _append_log_entry(entry: dict[str, Any]) -> None:
    """Añade una entrada al log persistente en disco (best-effort).

    No se usa en el flujo principal de /log; queda como gancho para
    que otros comandos (o hooks) puedan registrar turnos sin tocar
    session.history. Si el archivo no se puede escribir, no rompe.
    """
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:  # pragma: no cover — defensivo
        logger = logging.getLogger(__name__)
        logger.warning("No se pudo escribir en el log: %s", exc)


async def run_log_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /log para mostrar un resumen paginado de la sesión activa.

    A diferencia de /history, /log ofrece una vista agregada de la sesión
    con cabecera de metadatos, una línea de tiempo compacta por turno y
    conteos agregados (turnos del usuario / asistente, llamadas a
    herramientas con desglose por nombre, errores detectados).

    Subcomandos disponibles:

    - (sin args) o ``N`` entero → muestra los últimos 20 (o ``N``) turnos
      seguidos de la cabecera de conteos.
    - ``stats`` → muestra sólo el panel de conteos agregados.
    - ``clear`` → borra el archivo de log en disco (``session.log``).
      No toca ``session.history``.
    - ``help``  → imprime esta ayuda en español.
    - ``path``  → imprime la ruta absoluta del archivo de log.

    Args:
        session: La sesión activa del agente (provee ``history``,
            ``_tool_call_history``, ``config``, ``_session_start``).
        args: Texto crudo con los argumentos del usuario.

    Examples:
        /log
        /log 50
        /log stats
        /log clear
        /log help
        /log path
    """
    text = (args or "").strip()

    # ── Subcomandos sin historial ────────────────────────────────────────
    if text.lower() in ("help", "--help", "-h", "?"):
        console.print("[bold realm]᛭ /log[/bold realm] [dim]— resumen de sesión[/dim]")
        console.print()
        console.print("[bold]Uso:[/bold]")
        console.print("  /log              [dim]# últimos 20 turnos + conteos[/dim]")
        console.print("  /log N            [dim]# últimos N turnos + conteos[/dim]")
        console.print("  /log stats        [dim]# sólo panel de conteos agregados[/dim]")
        console.print("  /log clear        [dim]# borra el log en disco[/dim]")
        console.print("  /log path         [dim]# ruta absoluta del log[/dim]")
        console.print("  /log help         [dim]# esta ayuda[/dim]")
        console.print()
        console.print("[dim]El log persistido vive en:[/dim]")
        console.print(f"  [dim]{_LOG_FILE}[/dim]")
        return

    if text.lower() == "path":
        console.print(f"[info]Ruta del log de sesión:[/info] {_LOG_FILE}")
        return

    if text.lower() == "clear":
        if _clear_log_file():
            console.print("[success]✓ Log de sesión borrado.[/success]")
        else:
            console.print(
                "[dim]No hay archivo de log para borrar (nada que limpiar).[/dim]"
            )
        return

    # ── Determinar límite de la línea de tiempo ──────────────────────────
    stats_only = False
    limit = 20

    if text:
        tokens = text.split()
        first = tokens[0].lower()
        if first == "stats":
            stats_only = True
        elif first.isdigit():
            limit = int(first)
            if limit < 1:
                render_error("Uso: /log [N] [stats|clear|help|path] (N >= 1)")
                return
        else:
            render_error(
                f"Subcomando desconocido: {first!r}. Use: /log [N] [stats|clear|help|path]"
            )
            return

    # ── Recolectar datos de la sesión ──────────────────────────────────
    history = getattr(session, "history", None) or []
    tool_history: list[dict[str, Any]] = (
        getattr(session, "_tool_call_history", None) or []
    )

    # Inicio de sesión: intenta _session_start, _started_at, mtime del log,
    # y finalmente ahora.
    started_at: datetime = datetime.now()
    for attr in ("_session_start", "_started_at"):
        candidate = getattr(session, attr, None)
        if candidate is not None:
            started_at = (
                candidate if isinstance(candidate, datetime) else started_at
            )
            break
    if _LOG_FILE.exists():
        try:
            mtime = datetime.fromtimestamp(_LOG_FILE.stat().st_mtime)
            if mtime < started_at:
                started_at = mtime
        except OSError:  # pragma: no cover
            pass

    model = getattr(getattr(session, "config", None), "model", "?")
    provider = getattr(getattr(session, "config", None), "provider", "?")

    # Conteos agregados.
    user_turns = sum(1 for m in history if m.get("role") == "user")
    assistant_turns = sum(1 for m in history if m.get("role") == "assistant")
    tool_messages = [m for m in history if m.get("role") == "tool"]
    error_count = 0
    for m in tool_messages:
        content = str(m.get("content", ""))
        if not content:
            continue
        lowered = content.lower()
        if (
            content.startswith("Error")
            or content.startswith("Traceback")
            or "traceback" in lowered[:200]
            or ("error" in lowered[:200] and len(content) > 200)
        ):
            error_count += 1

    tool_call_total = len(tool_history)
    tool_breakdown: dict[str, int] = {}
    for entry in tool_history:
        name = entry.get("name", "?") if isinstance(entry, dict) else "?"
        tool_breakdown[name] = tool_breakdown.get(name, 0) + 1

    total_turns = len(history)
    total_tool_calls = tool_call_total

    # ── Cabecera de metadatos ───────────────────────────────────────
    console.print("[bold realm]᛭ Sesión[/bold realm] [dim]— resumen /log[/dim]")
    console.print(
        f"  [dim]Inicio:[/dim]    {started_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    console.print(f"  [dim]Modelo:[/dim]    [model]{model}[/model]")
    console.print(f"  [dim]Proveedor:[/dim] [model]{provider}[/model]")
    console.print(f"  [dim]Turnos totales:[/dim]       {total_turns}")
    console.print(f"  [dim]Llamadas a herramientas:[/dim] {total_tool_calls}")

    # ── Panel de conteos agregados ─────────────────────────────────────
    console.print()
    console.print("[bold cyan]Conteos agregados[/bold cyan]")
    console.print(f"  [green]❯[/green] Turnos de usuario:    {user_turns}")
    console.print(f"  [blue]○[/blue] Turnos de asistente:  {assistant_turns}")
    console.print(f"  [magenta]⚒[/magenta] Mensajes de tool:    {len(tool_messages)}")
    console.print(f"  [red]✗[/red] Errores detectados:   {error_count}")
    if tool_breakdown:
        # Desglose por nombre de herramienta, ordenado por frecuencia.
        console.print("  [dim]Desglose de herramientas:[/dim]")
        for name, count in sorted(
            tool_breakdown.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            console.print(f"    [bold cyan]{name}[/bold cyan]: {count}")

    if stats_only:
        return

    # ── Línea de tiempo compacta ────────────────────────────────────────
    if not history:
        console.print()
        console.print("[dim]No hay turnos para mostrar en la línea de tiempo.[/dim]")
        return

    console.print()
    console.print(f"[bold cyan]Línea de tiempo[/bold cyan] [dim](últimos {limit})[/dim]")

    role_icons = {
        "user": ("❯", "green"),
        "assistant": ("○", "blue"),
        "system": ("⚙", "yellow"),
        "tool": ("⚒", "magenta"),
        "function": ("∫", "cyan"),
        "error": ("✗", "red"),
    }

    # Las primeras N desde el final = últimos N turnos.
    recent = history[-limit:]
    start_index = total_turns - len(recent) + 1
    for offset, msg in enumerate(recent):
        role = msg.get("role", "?")
        icon, color = role_icons.get(role, ("•", "white"))
        raw_content = msg.get("content", "")
        if not isinstance(raw_content, str):
            raw_content = str(raw_content)
        # Limpiar markup de Rich y saltos de línea.
        preview = raw_content.replace("\n", " ").replace("\r", " ").strip()
        # Quitar etiquetas tipo [dim]...[/dim] de forma simple.
        preview = re.sub(r"\[[^\]]{1,40}\]", "", preview)
        if len(preview) > 80:
            preview = preview[:80] + "…"
        ts = _format_history_timestamp(msg.get("timestamp"))
        turn_no = start_index + offset
        console.print(
            f"  [dim]{turn_no:>3}.[/dim] [dim]{ts}[/dim] "
            f"[{color}]{icon} {role}[/{color}] {preview}"
        )


# ── Feedback command ─────────────────────────────────────────────────


async def run_feedback_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Gestiona feedback local con ``/feedback [add|clear|help]``."""
    feedback_path = CONFIG_DIR / "feedback.json"
    text = args.strip()
    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""

    if subcmd == "help":
        console.print(
            "[bold realm]Uso de /feedback[/]\n"
            "  [bold cyan]/feedback[/] — muestra las últimas 5 entradas\n"
            "  [bold cyan]/feedback add <mensaje>[/] — guarda feedback\n"
            "  [bold cyan]/feedback clear[/] — borra todas las entradas\n"
            "  [bold cyan]/feedback help[/] — muestra esta ayuda"
        )
        return

    try:
        if feedback_path.exists():
            entries = json.loads(feedback_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                render_error("El archivo de feedback tiene un formato inválido.")
                return
        else:
            entries = []
    except (OSError, json.JSONDecodeError) as exc:
        render_error(f"No se pudo leer el feedback: {exc}")
        return

    if not text:
        if not entries:
            console.print("[dim]No hay feedback guardado.[/]")
            return

        from rich.table import Table

        table = Table(title="Feedback reciente")
        table.add_column("Fecha", style="cyan", no_wrap=True)
        table.add_column("Mensaje")
        for entry in entries[-5:]:
            if isinstance(entry, dict):
                timestamp = str(entry.get("ts", ""))
                message = str(entry.get("message", ""))
            else:
                timestamp = ""
                message = str(entry)
            table.add_row(timestamp, message)
        console.print(table)
        return

    if subcmd == "add":
        message = parts[1].strip() if len(parts) > 1 else ""
        if not message:
            render_error("Uso: /feedback add <mensaje>")
            return
        entries.append({"ts": datetime.now(UTC).isoformat(), "message": message})
        try:
            feedback_path.parent.mkdir(parents=True, exist_ok=True)
            feedback_path.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            render_error(f"No se pudo guardar el feedback: {exc}")
            return
        console.print("[success]✓ Feedback guardado.[/]")
        return

    if subcmd == "clear":
        count = len(entries)
        if not count:
            console.print("[dim]No hay feedback para borrar.[/]")
            return

        try:
            from rich.prompt import Confirm
        except ImportError:  # pragma: no cover - Rich incluye Confirm normalmente
            confirmed = False
            while True:
                console.print(f"¿Borrar {count} entries? (s/n)")
                try:
                    answer = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = "n"
                if answer in ("s", "sí", "si"):
                    confirmed = True
                    break
                if answer in ("n", "no"):
                    break
        else:
            try:
                confirmed = Confirm.ask(
                    f"¿Borrar {count} entradas de feedback?",
                    default=False,
                )
            except (EOFError, KeyboardInterrupt):
                confirmed = False

        if not confirmed:
            console.print("[dim]Operación cancelada.[/]")
            return

        try:
            feedback_path.write_text("[]\n", encoding="utf-8")
        except OSError as exc:
            render_error(f"No se pudo borrar el feedback: {exc}")
            return
        console.print(f"[success]✓ {count} entradas de feedback borradas.[/]")
        return

    render_error("Uso: /feedback [add <mensaje>|clear|help]")

"""Source for /learn slash command block. Imported as a module from _learn_section.py."""

# Internal state: cached suggestions from the last /learn invocation,
# keyed by state file path so the user can /learn save <n> without
# re-running the analysis.
_LEARN_CACHE: dict[str, list[SkillSuggestion]] = {}


async def run_learn_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Suggest reusable delegation skills from historical post-mortems (/learn).

    Examples:
        /learn              # show candidate skills (table, numbered)
        /learn save 1       # persist suggestion #1 as a real DelegationSkill YAML
        /learn save 2 3     # persist suggestions 2 and 3 in one go
        /learn clear        # drop the cached suggestions for the current session

    Behaviour:
      * Reads post-mortems from the active orchestration state file
        (SQLite transaccional por defecto; override
        via ``YGGDRASIL_ORCHESTRATION_STATE``).
      * Groups successful delegations by preset; presets with ``>=2``
        successes are surfaced as candidates.
      * ``/learn save <n>`` materialises the suggestion via the
        ``DelegationSkillRegistry`` (lilith-skills, ~/.yggdrasil/skills/<n>.yaml).

    This command is the automejora nivel-2 surface: it converts past
    successful delegations into reusable skills without operator intervention.
    """
    import shlex as _shlex

    from ..render import console, render_error

    text = args.strip()
    if not text:
        _render_learn_table()
        return

    try:
        tokens = _shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    sub = tokens[0].lower()
    if sub == "clear":
        _LEARN_CACHE.clear()
        console.print("[dim]caché de /learn vaciada.[/]")
        return

    if sub != "save":
        render_error(
            "Uso: /learn [save <n> [<n> ...] | clear]"
        )
        return

    indices: list[int] = []
    for raw in tokens[1:]:
        try:
            indices.append(int(raw))
        except ValueError:
            render_error(f"Índice inválido: {raw!r} (debe ser un entero)")
            return

    suggestions = _learn_cached_or_refresh()
    if not suggestions:
        render_error(
            "No hay sugerencias en caché; ejecuta /learn primero."
        )
        return

    by_index = {s.index: s for s in suggestions}
    saved: list[str] = []
    for idx in indices:
        suggestion = by_index.get(idx)
        if suggestion is None:
            render_error(
                f"Índice fuera de rango: {idx} (rango válido: 1..{len(suggestions)})"
            )
            continue
        try:
            path = save_suggestion(suggestion)
        except (OSError, TypeError, ValueError) as exc:
            render_error(
                f"No se pudo guardar {suggestion.name!r}: {exc}"
            )
            continue
        saved.append(str(path))

    if saved:
        console.print(
            f"[success]✓ {len(saved)} skill(s) guardadas:[/]"
        )
        for path in saved:
            console.print(f"  [dim]- {path}[/]")


def _learn_state_path() -> Path:
    """Return the active state path, honouring the env override."""
    from lilith_tools.orchestration_state import default_state_path

    return default_state_path()


def _learn_cached_or_refresh() -> list[SkillSuggestion]:
    """Return cached suggestions for the current state path, refreshing if empty."""
    state_path = _learn_state_path()
    key = str(state_path)
    cached = _LEARN_CACHE.get(key)
    if cached:
        return cached
    suggestions = suggest_from_state_path(state_path)
    _LEARN_CACHE[key] = suggestions
    return suggestions


def _render_learn_table() -> None:
    """Render the /learn table (or a clear empty-state message)."""
    from rich.table import Table

    from ..render import console, render_error

    suggestions = _learn_cached_or_refresh()
    state_path = _learn_state_path()
    if not suggestions:
        if state_path.exists():
            render_error(
                "No hay suficientes post-mortems exitosos para proponer skills "
                "(se requieren >=2 por preset). Ejecuta mas delegaciones primero."
            )
        else:
            render_error(
                f"No se encontró el archivo de estado {state_path}. "
                "Ejecuta delegaciones primero para generar post-mortems."
            )
        return

    table = Table(
        title="[bold realm]᛭ /learn — skills sugeridas desde post-mortems[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=True,
        caption=(
            f"[dim]{len(suggestions)} sugerencia(s) · "
            "guarda con /learn save <n>[/dim]"
        ),
    )
    table.add_column("#", style="bold cyan", no_wrap=True)
    table.add_column("Nombre", style="white")
    table.add_column("Preset", style="white")
    table.add_column("Flags", style="dim")
    table.add_column("Éxitos", style="bold", justify="right")
    table.add_column("Descripción", style="white")

    for s in suggestions:
        flags = []
        if s.agentic:
            flags.append("agentic")
        if s.structured:
            flags.append("structured")
        if s.max_tokens is not None:
            flags.append(f"max_tokens={s.max_tokens}")
        flags_str = ", ".join(flags) if flags else "-"
        table.add_row(
            str(s.index),
            s.name,
            s.preset,
            flags_str,
            str(s.success_count),
            s.description,
        )

    console.print(table)
    console.print()


# ── /epoch command ────────────────────────────────────────────────────────────


# ── /timer command ──────────────────────────────────────────────────────────
#
# Cronómetro y cuenta atrás en memoria (un solo timer activo a la vez).
# Estado por proceso: se pierde al reiniciar el REPL, pero es suficiente para
# medir bloques cortos de trabajo durante la sesión.


def _format_elapsed(seconds: float) -> str:
    """Formatea segundos como ``HH:MM:SS.mmm`` (o ``MM:SS.mmm`` si <1h)."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


# Estado módulo: {"started_at": float, "label": str | None} | None.
# Asignación explícita (PEP 526 con `from __future__ import annotations`
# mantiene el valor runtime, pero hacemos la asignación directa para que sea
# inequívoco y ``global`` dentro de ``run_timer_command`` la encuentre sin
# sorpresas).
_TIMER_STATE = None  # type: dict[str, Any] | None


async def run_timer_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Cronómetro y cuenta atrás en memoria.

    Examples:
        /timer                  — muestra ayuda
        /timer start [etiqueta] — arranca cronómetro (etiqueta opcional)
        /timer stop             — detiene cronómetro y muestra tiempo total
        /timer status           — muestra estado actual sin detener
        /timer count <segundos> — cuenta atrás N segundos con progreso
        /timer cancel           — descarta cronómetro activo sin mostrar total
    """
    global _TIMER_STATE

    text = args.strip()
    tokens = text.split()
    sub = tokens[0].lower() if tokens else "help"

    if sub == "start":
        label = " ".join(tokens[1:]) if len(tokens) > 1 else None
        _TIMER_STATE = {"started_at": time.monotonic(), "label": label}
        msg = "Cronómetro iniciado."
        if label:
            msg += f"  [dim]Etiqueta: {label}[/dim]"
        console.print(f"[success]{msg}[/success]")
        console.print("[dim]Usa /timer stop para detenerlo y ver el total.[/dim]")
        return

    if sub == "stop":
        if _TIMER_STATE is None:
            render_error("No hay cronómetro activo. Usa /timer start primero.")
            return
        started_at = _TIMER_STATE["started_at"]
        label = _TIMER_STATE.get("label")
        elapsed = time.monotonic() - started_at
        _TIMER_STATE = None
        formatted = _format_elapsed(elapsed)
        suffix = f"  [dim]({label})[/dim]" if label else ""
        console.print(f"[info]Tiempo total:[/info]  [bold cyan]{formatted}[/bold cyan]{suffix}")
        return

    if sub == "status":
        if _TIMER_STATE is None:
            console.print("[dim]No hay cronómetro activo.[/dim]")
            return
        started_at = _TIMER_STATE["started_at"]
        label = _TIMER_STATE.get("label")
        elapsed = time.monotonic() - started_at
        formatted = _format_elapsed(elapsed)
        label_txt = f"  [dim]({label})[/dim]" if label else ""
        console.print(f"[info]Cronómetro activo:[/info]  [bold cyan]{formatted}[/bold cyan]{label_txt}")
        return

    if sub == "cancel":
        if _TIMER_STATE is None:
            render_error("No hay cronómetro activo que cancelar.")
            return
        _TIMER_STATE = None
        console.print("[dim]Cronómetro descartado.[/dim]")
        return

    if sub == "count":
        if len(tokens) < 2:
            render_error("Uso: /timer count <segundos>  (entero o decimal, ej. 90, 0.5)")
            return
        try:
            seconds = float(tokens[1])
        except ValueError:
            render_error(f"Duración inválida: {tokens[1]!r}")
            return
        if seconds <= 0:
            render_error("La duración debe ser > 0.")
            return
        # Cancelamos cualquier cronómetro activo antes de empezar la cuenta atrás.
        _TIMER_STATE = None
        console.print(f"[info]Cuenta atrás:[/info]  [bold cyan]{seconds:g}s[/bold cyan]  [dim](Ctrl+C para cancelar)[/dim]")
        try:
            # Bucle con pasos de 0.5s para mostrar progreso sin spam.
            step = 0.5
            remaining = seconds
            while remaining > 0:
                await asyncio.sleep(min(step, remaining))
                remaining -= step
                remaining = max(remaining, 0.0)
                console.print(f"  [dim]{remaining:5.1f}s restantes[/dim]", end=chr(13))
            console.print()
            console.print("[success]⏰  ¡Tiempo cumplido![/success]")
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print()
            console.print("[dim]Cuenta atrás cancelada.[/dim]")
        return

    # Default: ayuda (incluye sub == "help" o cualquier entrada desconocida).
    console.print("[bold cyan]/timer[/bold cyan] — cronómetro y cuenta atrás en memoria")
    console.print("  [dim]Uso:[/dim]")
    console.print("    /timer start [etiqueta]   [dim]# arrancar cronómetro[/dim]")
    console.print("    /timer stop               [dim]# detener y mostrar total (mm:ss.mmm)[/dim]")
    console.print("    /timer status             [dim]# ver tiempo parcial sin detener[/dim]")
    console.print("    /timer cancel             [dim]# descartar cronómetro activo[/dim]")
    console.print("    /timer count <segundos>   [dim]# cuenta atrás con progreso[/dim]")
    console.print("  [dim]Sólo puede haber un cronómetro activo a la vez; el estado vive hasta que se detiene o se reinicia el REPL.[/dim]")
