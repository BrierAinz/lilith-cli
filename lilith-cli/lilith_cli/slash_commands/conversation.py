"""Conversation slash commands: /compact, /context, /goal, /plan, /pin, /bookmark, /redo, /continue, /summary, /recap, /copy, /last-tool and /conclave."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
import uuid as _uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry
from rich.syntax import Syntax

from ..config import CONFIG_DIR
from ..render import console, render_error

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime
from ..utility_commands import (
    _resolve_last_assistant_message,
    _resolve_message_by_index,
)


def _compact_messages(messages: list) -> str:
    """Compact a list of messages into a brief summary string.

    Naive implementation: concatenate first 80 chars of each message content.
    Used by /compact to produce a placeholder summary that can be replaced by
    an LLM-generated one in the future.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?") if isinstance(msg, dict) else "?"
        content = str(msg.get("content", "")) if isinstance(msg, dict) else str(msg)
        snippet = content[:80].replace("\n", " ").strip()
        parts.append(f"{role}: {snippet}")
    summary = " | ".join(parts)
    if len(summary) > 500:
        summary = summary[:500] + "..."
    return summary or "(empty)"


async def run_compact_command(session: SessionRuntime, args: str) -> None:
    """Compact history (/compact [n] [--dry-run] [--force] [--keep-last N])."""
    tokens = args.split()
    dry_run = "--dry-run" in tokens
    force = "--force" in tokens or "-f" in tokens
    keep_last = 0
    keep_last_set = False
    remaining: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--keep-last" and i + 1 < len(tokens):
            try:
                keep_last = int(tokens[i + 1])
                keep_last_set = True
                i += 2
                continue
            except ValueError:
                render_error("--keep-last requires an integer N")
                return
        if tok.startswith("--keep-last="):
            try:
                keep_last = int(tok.split("=", 1)[1])
                keep_last_set = True
                i += 1
                continue
            except ValueError:
                render_error("--keep-last requires an integer N")
                return
        if tok in ("--dry-run", "--force", "-f"):
            i += 1
            continue
        remaining.append(tok)
        i += 1
    text = " ".join(remaining)

    if not text:
        n = 0
    else:
        try:
            n = int(text)
        except ValueError:
            render_error("Uso: /compact [n] [--dry-run] [--force] [--keep-last N]")
            return

    if not session.history:
        console.print("[dim]No hay historial para compactar.[/dim]")
        return

    total = len(session.history)

    # Resolve n and keep_last consistently:
    # - If keep_last is set but n is not (n==0), compact everything except last N
    # - Otherwise n is the explicit count to compact, keep_last clamps to remaining
    if keep_last_set and n <= 0:
        n = max(0, total - keep_last)
    elif n <= 0:
        n = total

    # Clamp keep_last to total - n (cannot keep more than what remains after compaction)
    max_keep = max(0, total - n)
    if keep_last > max_keep:
        keep_last = max_keep

    to_summarize_count = n
    to_summarize = session.history[:to_summarize_count]
    keep = session.history[total - keep_last:] if keep_last > 0 else []

    if not to_summarize:
        console.print("[dim]Nada que compactar.[/dim]")
        return

    summary = _compact_messages(to_summarize)

    if dry_run:
        console.print(f"[info]Dry-run:[/info] se compactarían {to_summarize_count} mensajes en un resumen de ~{len(summary)} chars.")
        if keep_last > 0:
            console.print(f"[dim]Se conservarán los últimos {keep_last} mensajes sin resumir.[/dim]")
        console.print(f"[dim]Resumen tentativo:[/dim] {summary[:200]}{'...' if len(summary) > 200 else ''}")
        console.print("[dim]Pasá sin --dry-run para aplicar.[/dim]")
        return

    if not force and to_summarize_count >= total // 2:
        console.print(f"[warn]Vas a compactar {to_summarize_count} de {total} mensajes ({100 * to_summarize_count // total}% del historial).[/warn]")
        console.print("[dim]Pasá --force para confirmar o usá un número menor.[/dim]")
        return

    # Apply: replace to_summarize with summary, keep last N as-is
    session.history.clear()
    session.history.append({"role": "system", "content": f"Resumen de la conversación: {summary}"})
    session.history.extend(keep)
    if keep_last > 0:
        console.print(f"[success]✓ {to_summarize_count} mensajes compactados + {keep_last} conservados (total: {len(session.history)}).[/success]")
    else:
        console.print(f"[success]✓ {to_summarize_count} mensajes compactados en un resumen.[/success]")


# ── /last-tool command ──────────────────────────────────────────────


async def run_last_tool_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /last-tool para mostrar detalles de la última llamada a herramienta.

    Examples:
        /last-tool
        /last-tool 2
        /last-tool file_read
    """
    text = args.strip()
    history: list[dict[str, Any]] = getattr(session, "_tool_call_history", []) or []

    if not history:
        console.print("[dim]No hay llamadas a herramientas en esta sesión.[/]")
        return

    if not text:
        entry = history[-1]
    elif text.isdigit():
        n = int(text)
        if n < 1 or n > len(history):
            render_error(f"Índice fuera de rango: {n} (1-{len(history)})")
            return
        entry = history[-n]
    else:
        # Buscar la llamada más reciente a la herramienta indicada.
        matches = [h for h in history if h.get("name") == text]
        if not matches:
            console.print(f"[dim]No hay llamadas registradas para '{text}'.[/]")
            return
        entry = matches[-1]

    name = entry.get("name", "desconocida")
    arguments = entry.get("arguments", {})
    duration = entry.get("duration")
    timestamp = entry.get("timestamp", "?")
    success = entry.get("success")

    console.print(f"[bold cyan]Herramienta:[/] [bold]{name}[/]")
    console.print(f"[dim]Timestamp:[/] {timestamp}")
    if duration is not None:
        console.print(f"[dim]Duración:[/] {duration:.4f}s")
    if success is not None:
        status = "[success]éxito[/]" if success else "[error]error[/]"
        console.print(f"[dim]Estado:[/] {status}")
    console.print("[bold cyan]Argumentos:[/]")
    try:
        args_text = json.dumps(arguments, ensure_ascii=False, indent=2, default=str)
    except Exception:
        args_text = str(arguments)
    console.print(Syntax(args_text, "json", theme="monokai", word_wrap=True))


# ── /cost command ─────────────────────────────────────────────────────


# ── /plan command ─────────────────────────────────────────────────────


async def run_plan_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /plan [create|show|done|clear|list]."""
    text = args.strip()
    if not text or text.lower() in ("show", "status"):
        _get_plan = getattr(session, "get_plan", None)
        plan = _get_plan() if _get_plan else None
        if not plan:
            console.print("[dim]No hay un plan activo. Creá uno con /plan create <tema>.[/]")
            return
        from rich.panel import Panel

        lines = [f"[bold]{plan.title}[/]"]
        for i, step in enumerate(plan.steps, start=1):
            mark = "[success]✓[/]" if step.done else "[dim]○[/]"
            lines.append(f"{mark} {i}. {step.description}")
        console.print(Panel("\n".join(lines), title="[bold realm]᛭ Plan[/]", expand=False))
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("create", "new"):
        if not rest:
            render_error("Uso: /plan create <descripción>")
            return
        _create_plan = getattr(session, "create_plan", None)
        if _create_plan is not None:
            await _create_plan(rest)
        else:
            render_error("/plan create: create_plan no disponible en esta sesión")
            return
        console.print(f"[success]✓ Plan creado: {rest}[/]")
    elif subcmd in ("done", "complete"):
        try:
            step = int(rest)
        except ValueError:
            render_error("Uso: /plan done <número>")
            return
        _m = getattr(session, "mark_plan_done", None)
        if _m is not None:
            await _m(step)
        else:
            render_error("/plan done: mark_plan_done no disponible en esta sesión")
            return
    elif subcmd in ("clear", "reset"):
        _cp = getattr(session, "clear_plan", None)
        if _cp is None:
            render_error("/plan clear: clear_plan no disponible en esta sesión")
            return
        _cp()
        console.print("[success]✓ Plan eliminado.[/]")
    elif subcmd in ("list", "ls"):
        plans = session.list_plans() if hasattr(session, "list_plans") else []
        if not plans:
            console.print("[dim]No hay planes guardados.[/]")
            return
        console.print("\n[bold realm]᛭ Planes guardados[/]")
        for p in plans:
            console.print(f"  [bold cyan]{p.id}[/] {p.title}")
        console.print()
    else:
        # Treat as a new plan description
        _create_plan2 = getattr(session, "create_plan", None)
        if _create_plan2 is not None:
            await _create_plan2(text)
        else:
            render_error("/plan: create_plan no disponible en esta sesión")
            return
        console.print(f"[success]✓ Plan creado: {text}[/]")


# ── /bookmark command ─────────────────────────────────────────────────


async def run_bookmark_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /bookmark para guardar y reutilizar fragmentos de texto.

    Examples:
        /bookmark <clave> <valor>
        /bookmark list
        /bookmark get <clave>
        /bookmark clear
    """
    text = args.strip()
    bookmarks: dict[str, str] = _load_bookmarks()

    if not text or text.lower() in ("list", "ls"):
        if not bookmarks:
            console.print("[dim]No hay bookmarks guardados.[/]")
            return
        console.print("\n[bold realm]᛭ Bookmarks[/]")
        for name, value in sorted(bookmarks.items()):
            console.print(f"  [bold cyan]{name}[/]: {value}")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("set", "add"):
        if len(parts) < 2:
            render_error("Uso: /bookmark set <clave> <valor>")
            return
        key, value = parts[0], parts[1]
        # fix: the subcmd is parts[0], so key is rest when splitting once
        key, value = rest.split(maxsplit=1) if " " in rest else (rest, "")
        bookmarks[key] = value
        _save_bookmarks(bookmarks)
        console.print(f"[success]✓ Bookmark guardado: {key}[/]")
        return

    if subcmd in ("get", "show"):
        if not rest:
            render_error("Uso: /bookmark get <clave>")
            return
        if rest not in bookmarks:
            render_error(f"Bookmark no encontrado: {rest}")
            return
        console.print(f"[tool.name]{rest}[/]: [tool.result]{bookmarks[rest]}[/]")
        return

    if subcmd in ("clear", "reset"):
        bookmarks.clear()
        _save_bookmarks(bookmarks)
        console.print("[success]✓ Bookmarks eliminados.[/]")
        return

    # /bookmark <clave> <valor>
    try:
        key, value = text.split(maxsplit=1)
    except ValueError:
        render_error("Uso: /bookmark <clave> <valor>|get <clave>|list|clear")
        return
    bookmarks[key] = value
    _save_bookmarks(bookmarks)
    console.print(f"[success]✓ Bookmark guardado: {key}[/]")


# ── Bookmark storage helpers ─────────────────────────────────────────

_BOOKMARKS_PATH = CONFIG_DIR / "bookmarks.json"


def _load_bookmarks() -> dict[str, str]:
    """Carga bookmarks desde ~/.yggdrasil/bookmarks.json."""
    if not _BOOKMARKS_PATH.exists():
        return {}
    try:
        data = json.loads(_BOOKMARKS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # pragma: no cover
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando bookmarks: %s", exc)
    return {}


def _save_bookmarks(bookmarks: dict[str, str]) -> None:
    """Guarda bookmarks en ~/.yggdrasil/bookmarks.json."""
    _BOOKMARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BOOKMARKS_PATH.write_text(
        json.dumps(bookmarks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── /redo command ────────────────────────────────────────────────────


async def _stream_agent_reply(session: SessionRuntime, text: str) -> None:
    """Consume process_message_stream y renderiza los chunks de texto."""
    async for event in session.process_message_stream(text):
        if event.get("type") == "text":
            chunk = event.get("content", "")
            if chunk:
                console.print(chunk, end="")
    console.print()


async def run_redo_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /redo para reenviar el último mensaje del usuario.

    Examples:
        /redo
    """
    text = args.strip()
    if text:
        render_error("Uso: /redo")
        return
    last = getattr(session, "_last_user_message", None)
    if not last:
        render_error("No hay un mensaje previo para reenviar.")
        return

    await _stream_agent_reply(session, last)


# ── /continue command ─────────────────────────────────────────────────


async def run_continue_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /continue para pedir al modelo que siga su última respuesta.

    Examples:
        /continue
        /continue <texto adicional>
    """
    text = args.strip()
    prompt = "Continuá la respuesta anterior."
    if text:
        prompt += f"\n{text}"


    await _stream_agent_reply(session, prompt)


# ── /summary command ──────────────────────────────────────────────────


async def run_summary_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /summary para resumir toda la conversación.

    Examples:
        /summary
    """
    text = args.strip()
    if text:
        render_error("Uso: /summary")
        return


    await _stream_agent_reply(session, "Resumí la conversación hasta ahora de forma concisa.")


# ── /recap command ───────────────────────────────────────────────────


async def run_recap_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /recap [n] para resumir las últimas n rondas de la conversación.

    Examples:
        /recap
        /recap 5
    """
    text = args.strip()
    if not text:
        n = 5
    else:
        try:
            n = int(text)
        except ValueError:
            render_error("Uso: /recap [número]  (entero entre 1 y 50)")
            return

    if n < 1 or n > 50:
        render_error(f"Uso: /recap [número]  (entero entre 1 y 50; recibí {n})")
        return

    history_len = len(session.history or [])
    if history_len == 0:
        console.print("[warning]La conversación está vacía; no hay nada que resumir.[/]")
        return

    n = min(n, history_len)

    prompt = f"Resumí las últimas {n} rondas de la conversación de forma concisa."
    await _stream_agent_reply(session, prompt)


# ── /copy command ─────────────────────────────────────────────────────


async def run_copy_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /copy para copiar el último mensaje del asistente al portapapeles.

    Examples:
        /copy
        /copy last
    """
    text = args.strip()
    if text and text.lower() != "last":
        render_error("Uso: /copy [last]")
        return

    last_assistant = ""
    for msg in reversed(session.history or []):
        if msg.get("role") == "assistant":
            last_assistant = str(msg.get("content", ""))
            break

    if not last_assistant:
        render_error("No hay un mensaje del asistente para copiar.")
        return

    # Try to copy via platform utilities
    copied = False
    try:
        import subprocess

        if os.name == "nt":
            subprocess.run(["clip"], input=last_assistant.encode("utf-8"), check=True, capture_output=True)
            copied = True
        else:
            for cmd in ["xclip", "xsel", "pbcopy"]:
                try:
                    subprocess.run([cmd], input=last_assistant.encode("utf-8"), check=True, capture_output=True)
                    copied = True
                    break
                except Exception:
                    continue
    except Exception:
        copied = False

    if copied:
        console.print("[success]✓ Última respuesta copiada al portapapeles.[/]")
    else:
        console.print("[warning]No se pudo copiar al portapapeles. Última respuesta:[/]")
        console.print(last_assistant)


# ── /pin command ─────────────────────────────────────────────────────────


async def run_pin_command(session: SessionRuntime, args: str) -> None:
    """Gestiona mensajes fijados de la conversación actual.

    Examples:
        /pin                 — muestra ayuda y estado
        /pin <n>             — fija el n-ésimo mensaje del historial (1-based)
        /pin list | --list   — lista los mensajes fijados
        /pin remove <n>      — elimina el fijado en el índice n
        /pin clear | --clear — elimina todos los fijados
    """
    text = args.strip()
    pins = _load_pin_entries(session)
    parts = text.split()

    def _persist() -> None:
        """Guarda los fijados exclusivamente en la sesión actual."""
        _save_pin_entries(session, pins)

    if not parts:
        console.print("[bold realm]Uso de /pin[/]")
        console.print("  [cyan]/pin <n>[/]   — fija el mensaje n de la conversación")
        console.print("  [cyan]/pin list[/]  — lista los mensajes fijados")
        console.print("  [cyan]/pin clear[/] — elimina todos los mensajes fijados")
        _print_pinned_messages(pins)
        return

    head = parts[0].lower()
    if head in ("--list", "-l", "list", "ls"):
        _print_pinned_messages(pins)
        return

    if head in ("--clear", "clear", "reset"):
        count = len(pins)
        pins.clear()
        _persist()
        console.print(f"[success]✓ Mensajes fijados eliminados: {count}[/]")
        return

    # Se conservan los alias históricos para no romper scripts existentes.
    if head in ("--unpin", "-u", "remove", "rm", "unpin"):
        if len(parts) < 2:
            render_error("Uso: /pin remove <índice>")
            return
        try:
            index = int(parts[1])
        except ValueError:
            render_error("remove requiere un índice entero")
            return
        if index < 1 or index > len(pins):
            render_error(f"Índice fuera de rango: {index} (hay {len(pins)} fijados)")
            return
        removed = pins.pop(index - 1)
        _persist()
        role = removed.get("role", "?")
        preview = str(removed.get("content") or removed.get("text", ""))[:40]
        console.print(f"[warning]✗ Desfijado [#{index}] {role}: {preview}[/]")
        return

    if len(parts) != 1:
        render_error("Uso: /pin | /pin <n> | /pin list | /pin clear")
        return
    try:
        index = int(head)
    except ValueError:
        render_error("Uso: /pin | /pin <n> | /pin list | /pin clear")
        return

    result = _pin_message_at_index(session, pins, index)
    if not result.get("ok"):
        render_error(result.get("error", "Error fijando mensaje"))
        return
    _persist()
    msg = result["entry"]
    preview = str(msg.get("content") or msg.get("text", ""))[:80]
    console.print(f"📌 Mensaje fijado en el índice {index}: {preview}")


def _print_pinned_messages(pinned: list[dict[str, Any]]) -> None:
    """Renderiza los mensajes fijados con su índice."""
    if not pinned:
        console.print("[dim]No hay mensajes fijados.[/]")
        return

    console.print("\n[bold realm]᛭ Mensajes fijados[/]")
    for i, msg in enumerate(pinned, start=1):
        role = msg.get("role", "?")
        content = str(msg.get("content") or msg.get("text", ""))
        preview = content[:80]
        if len(preview) == 80:
            preview += "…"
        console.print(f"  [bold cyan]{i}.[/] [{role}] {preview}")
    console.print()


# ---------------------------------------------------------------------------
# /pin storage + helpers + tool (pin_message)
# ---------------------------------------------------------------------------


def _pin_storage_path() -> Path:
    """Ruta de ``~/.lilith/pins.json`` (la carpeta se crea al vuelo)."""
    base = Path.home() / ".lilith"
    base.mkdir(parents=True, exist_ok=True)
    return base / "pins.json"


def _get_session_id(session: SessionRuntime) -> str:
    """Id estable de la sesión, generando un uuid si no lo tiene."""
    sid = getattr(session, "session_id", None) or getattr(session, "_session_id", None)
    if not sid:
        sid = str(_uuid.uuid4())
        try:
            session._session_id = sid
        except Exception:
            pass
    return str(sid)


def _load_pin_entries(session: SessionRuntime) -> list[dict[str, Any]]:
    """Mensajes fijados de la sesión: espejo en memoria y, si no, el de disco.

    El espejo ``session._pinned_messages`` lo consumen la serialización de la
    sesión, ``fork`` y el QR; el JSON en disco es lo que hace que los fijados
    sobrevivan al reinicio del REPL.
    """
    mirror = getattr(session, "_pinned_messages", None)
    if isinstance(mirror, list) and mirror:
        return [dict(entry) for entry in mirror if isinstance(entry, dict)]

    path = _pin_storage_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return []
    entries = payload.get(_get_session_id(session)) or []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _save_pin_entries(session: SessionRuntime, entries: list[dict[str, Any]]) -> None:
    """Guarda los fijados en el espejo de la sesión y en disco."""
    session._pinned_messages = [dict(entry) for entry in entries]

    path = _pin_storage_path()
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            existente = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(existente, dict):
                payload = existente
        except (json.JSONDecodeError, OSError):
            payload = {}
    payload[_get_session_id(session)] = list(entries)
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # El fijado ya vale en memoria: no romper el comando por el disco.
        pass


def _make_pin_entry(message: dict[str, Any], index: int) -> dict[str, Any]:
    """Build a pin entry dict from a session message and its 1-based index."""
    text = str(message.get("content", message.get("text", "")))
    role = str(message.get("role", "assistant"))
    return {
        "index": int(index),
        "content": text,
        "text": text,  # backward-compat alias for older readers
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "role": role,
    }


def _pin_message_at_index(
    session: SessionRuntime,
    pins: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    """Pin the n-th most recent message and append it to *pins* (in-memory)."""
    msg = _resolve_message_by_index(session, index)
    if msg is None:
        return {"ok": False, "error": f"Índice fuera de rango: {index}"}
    entry = _make_pin_entry(msg, index)
    pins.append(entry)
    return {"ok": True, "entry": entry}


def _pin_default_message(
    session: SessionRuntime, pins: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pin the most recent assistant message (or last history message)."""
    msg = _resolve_last_assistant_message(session)
    history = getattr(session, "history", None) or []
    if msg is None:
        if not history:
            return {"ok": False, "error": "No hay mensajes en el historial para fijar."}
        msg = history[-1]
    index = len(history) if history else 1
    entry = _make_pin_entry(msg, index)
    pins.append(entry)
    return {"ok": True, "entry": entry, "index": index}


def _print_pin_result(session: Any, result: dict[str, Any], pins: list[dict[str, Any]]) -> None:
    """Render the result of a default ``/pin`` invocation."""
    if not result.get("ok"):
        render_error(result.get("error", "Error fijando mensaje"))
        return
    entry = result["entry"]
    index = result.get("index", entry.get("index", len(pins)))
    text = str(entry.get("text", ""))
    preview = text[:80]
    if len(preview) == 80:
        preview += "…"
    _save_pin_entries(session, pins)
    console.print(
        f"📌 Mensaje fijado en el índice {index}: {preview}"
    )


@ToolRegistry.register
class PinMessageTool(BaseTool):
    """Fija (pin) un mensaje de la conversación para tenerlo siempre a mano.

    Esta herramienta es el equivalente invocable por el agente del comando
    ``/pin`` del REPL. Permite fijar el último mensaje del asistente
    (sin argumentos), un mensaje concreto por índice 1-based, listar
    los mensajes fijados, desfijar uno por índice o limpiar todos.
    """

    name = "pin_message"
    description = (
        "Fija un mensaje importante de la conversación para consultarlo "
        "después. Por defecto fija el último mensaje del asistente."
    )
    parameters = {
        "index": {
            "type": "integer",
            "required": False,
            "default": 0,
            "description": (
                "Índice 1-based del mensaje a fijar (0 = último del asistente)"
            ),
        },
        "list": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Si es True, lista los mensajes fijados.",
        },
        "unpin": {
            "type": "integer",
            "required": False,
            "default": -1,
            "description": "Índice 1-based del mensaje a desfijar (-1 = ninguno).",
        },
        "clear": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Si es True, elimina todos los mensajes fijados.",
        },
    }

    def execute(
        self,
        session: SessionRuntime,
        index: int = 0,
        list: bool = False,
        unpin: int = -1,
        clear: bool = False,
        **_: Any,
    ) -> ToolResult:
        """Ejecuta la operación de pin solicitada."""
        try:
            pins = _load_pin_entries(session)
        except Exception as exc:
            return ToolResult(success=False, data=None, error=str(exc))

        if clear:
            count = len(pins)
            try:
                _save_pin_entries(session, [])
            except Exception as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            return ToolResult(
                success=True,
                data={"action": "clear", "removed": count},
            )

        if list:
            return ToolResult(success=True, data={"action": "list", "pins": pins})

        if unpin and unpin > 0:
            if unpin < 1 or unpin > len(pins):
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"Índice fuera de rango: {unpin}",
                )
            removed = pins.pop(unpin - 1)
            try:
                _save_pin_entries(session, pins)
            except Exception as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            return ToolResult(
                success=True,
                data={"action": "unpin", "removed": removed, "index": unpin},
            )

        if index and index > 0:
            result = _pin_message_at_index(session, pins, index)
            if not result.get("ok"):
                return ToolResult(
                    success=False,
                    data=None,
                    error=str(result.get("error", "Error fijando mensaje")),
                )
            try:
                _save_pin_entries(session, pins)
            except Exception as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            return ToolResult(
                success=True,
                data={"action": "pin", "entry": result["entry"]},
            )

        # Default: pin the most recent assistant message.
        result = _pin_default_message(session, pins)
        if not result.get("ok"):
            return ToolResult(
                success=False,
                data=None,
                error=str(result.get("error", "Error fijando mensaje")),
            )
        try:
            _save_pin_entries(session, pins)
        except Exception as exc:
            return ToolResult(success=False, data=None, error=str(exc))
        return ToolResult(
            success=True,
            data={"action": "pin", "entry": result["entry"]},
        )


_PIN_TOOL_REGISTERED = False


def _register_pin_tool() -> None:
    """Idempotently register :class:`PinMessageTool` in the global registry."""
    global _PIN_TOOL_REGISTERED
    if _PIN_TOOL_REGISTERED:
        return
    if ToolRegistry.get("pin_message") is not None:
        _PIN_TOOL_REGISTERED = True
        return
    _PIN_TOOL_REGISTERED = True


# Ensure the tool is registered when this module is imported.
_register_pin_tool()

# ── /json command ───────────────────────────────────────────────────────────────


# ── /reverse command ───────────────────────────────────────────────────────────────


# ── /conclave command ─────────────────────────────────────────────────────


async def run_conclave_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Fan-out the same question across 2-4 Hlidskjalf presets (/conclave).

    Examples:
        /conclave ¿Qué motor usa Lilith?
        /conclave "¿Qué motor usa Lilith?" --presets investigador-minimax,grok-research
        /conclave "resume X" --presets a,b --structured --max-tokens 1024 --timeout 30

    Behaviour:
        * Delegates to :class:`lilith_tools.conclave.ConclaveTool`, which
          already implements the parallel fan-out + per-preset 60s
          timeout contract.
        * Renders a Rich panel per preset with model, content (truncated
          to ~15 lines), and the per-preset error (if any). A failing
          preset never takes down the rest.
        * The conclave call is sync internally (it spins its own
          ``asyncio.run`` per preset), so we run it through
          ``loop.run_in_executor`` to avoid nested event loops.
    """
    import asyncio as _asyncio

    text = args.strip()
    if not text:
        render_error(
            "Uso: /conclave <pregunta> "
            "[--presets a,b,c] [--structured] [--max-tokens N] [--timeout N]"
        )
        return

    presets: list[str] | None = None
    structured = False
    max_tokens: int | None = None
    per_timeout: float | None = None

    import shlex as _shlex
    try:
        tokens = _shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    filtered: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--presets" and i + 1 < len(tokens):
            presets = [
                p.strip() for p in tokens[i + 1].split(",") if p.strip()
            ]
            i += 2
            continue
        if tok == "--structured":
            structured = True
            i += 1
            continue
        if tok == "--max-tokens" and i + 1 < len(tokens):
            try:
                max_tokens = int(tokens[i + 1])
            except ValueError:
                render_error("--max-tokens requiere un entero")
                return
            i += 2
            continue
        if tok == "--timeout" and i + 1 < len(tokens):
            try:
                per_timeout = float(tokens[i + 1])
            except ValueError:
                render_error("--timeout requiere un número")
                return
            i += 2
            continue
        filtered.append(tok)
        i += 1

    question = " ".join(filtered).strip()
    if not question:
        render_error("La pregunta no puede estar vacía.")
        return

    try:
        from lilith_tools.conclave import ConclaveTool  # type: ignore[import-not-found]
    except Exception as exc:
        render_error(f"No se pudo cargar la tool 'conclave': {exc}")
        return

    def _invoke() -> Any:
        kwargs: dict[str, Any] = {
            "question": question,
            "structured": structured,
        }
        if presets is not None:
            kwargs["presets"] = presets
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if per_timeout is not None:
            kwargs["timeout"] = per_timeout
        return ConclaveTool().execute(**kwargs)

    loop = _asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        # The REPL must survive a per-command crash. Surface the exception
        # as a synthetic ToolResult so the renderer can describe it.
        from lilith_tools.base import ToolResult  # type: ignore[import-not-found]

        result = ToolResult(
            success=False,
            data=None,
            error=f"conclave raised: {type(exc).__name__}: {exc}",
        )

    _render_conclave_panel(question, presets, result)


def _render_conclave_panel(
    question: str,
    presets: list[str] | None,
    result: Any,
) -> None:
    """Pretty-print the ConclaveTool result as one panel per preset."""
    from rich.panel import Panel
    from rich.text import Text

    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        render_error(
            f"conclave devolvió resultado inesperado: {type(result).__name__}"
        )
        return

    requested = data.get("presets_requested") or presets or []
    responses = data.get("responses") or []
    ok = data.get("ok_count", 0)
    failed = data.get("failed_count", 0)

    header_status = (
        "[success]OK[/success]" if getattr(result, "success", False)
        else "[error]FALLO[/error]"
    )
    console.print(
        f"\n[bold realm]᛭ Conclave[/] {header_status} · "
        f"pregunta: [italic]{question!r}[/italic] · "
        f"presets={len(requested)} · ok={ok} fallaron={failed}"
    )
    if getattr(result, "error", "") and not getattr(result, "success", False):
        console.print(f"  [error]{result.error}[/error]")

    if not responses:
        console.print("  [dim](sin respuestas)[/dim]\n")
        return

    for row in responses:
        preset_name = row.get("preset", "?")
        model = row.get("model") or "?"
        content = row.get("content") or ""
        error = row.get("error") or ""
        usage = row.get("usage") or {}

        title_parts = [f"preset={preset_name}", f"model={model}"]
        if usage.get("total_tokens"):
            title_parts.append(f"tokens={usage['total_tokens']}")
        title = " · ".join(title_parts)

        body = Text()
        if error:
            body.append(f"ERROR: {error}\n", style="bold red")
            body.append("(este preset no tumba al resto)\n", style="dim")
        if content:
            body.append(_truncate_content(content, max_lines=15))
        else:
            body.append("(sin contenido)", style="dim")

        style = "red" if error else "cyan"
        console.print(Panel(body, title=title, border_style=style, expand=True))

    console.print()


def _truncate_content(content: str, *, max_lines: int = 15) -> str:
    """Return *content* truncated to at most *max_lines* lines."""
    lines_in = content.splitlines()
    if len(lines_in) <= max_lines:
        return content
    head = lines_in[:max_lines]
    hidden = len(lines_in) - max_lines
    return "\n".join(head) + f"\n[dim]… (+{hidden} líneas más)[/dim]"


# ── /goal command ─────────────────────────────────────────────────────

_GOAL_MARKER = "[LILITH_SESSION_GOAL]"
_GOAL_STATES = {"active", "paused", "completed"}


def _goal_from_session(session: SessionRuntime) -> dict[str, Any] | None:
    """Return the session goal, recovering it from saved conversation history."""
    # History is the source of truth: /resume and /fork replace it in-place,
    # so consulting a cache first could leak the previous conversation's goal.
    for message in getattr(session, "history", []) or []:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = str(message.get("content", ""))
        first_line = content.splitlines()[0] if content else ""
        if not first_line.startswith(_GOAL_MARKER):
            continue
        try:
            goal = json.loads(first_line[len(_GOAL_MARKER) :].strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(goal, dict)
            and goal.get("objective")
            and goal.get("status") in _GOAL_STATES
        ):
            session._session_goal = goal
            return goal
    session._session_goal = None
    return None


def _sync_goal_message(session: SessionRuntime, goal: dict[str, Any] | None) -> None:
    """Keep one model-visible goal message in history (and therefore in saves)."""
    history = [
        message
        for message in (getattr(session, "history", []) or [])
        if not (
            isinstance(message, dict)
            and message.get("role") == "system"
            and str(message.get("content", "")).startswith(_GOAL_MARKER)
        )
    ]
    session._session_goal = goal
    if goal is not None:
        budget = goal.get("budget_tokens")
        budget_line = f"\nToken budget: {budget}" if budget else ""
        instructions = {
            "active": (
                "Keep this objective in focus. Do not claim it is complete until "
                "the relevant verification tools confirm the result."
            ),
            "paused": "This objective is paused. Do not continue it until it is resumed.",
            "completed": "This objective is recorded as completed; do not restart it implicitly.",
        }
        content = (
            f"{_GOAL_MARKER} {json.dumps(goal, ensure_ascii=False, separators=(',', ':'))}\n"
            f"SESSION GOAL ({goal['status']}): {goal['objective']}"
            f"{budget_line}\n{instructions[goal['status']]}"
        )
        history.insert(0, {"role": "system", "content": content})
    session.history = history


def _parse_goal_budget(value: str) -> int:
    """Parse positive token budgets such as 8000, 8k, or 1.5m."""
    match = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)([km]?)", value.strip())
    if not match:
        raise ValueError("el presupuesto debe ser un número, por ejemplo 8000 o 8k")
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()]
    budget = int(float(match.group(1)) * multiplier)
    if budget <= 0:
        raise ValueError("el presupuesto debe ser mayor que cero")
    return budget


def _goal_snapshot(session: SessionRuntime, goal: dict[str, Any]) -> dict[str, Any]:
    """Add current token accounting to a goal without mutating it."""
    total = int((getattr(session, "_total_usage", {}) or {}).get("total_tokens", 0))
    used = max(0, total - int(goal.get("started_tokens", 0)))
    budget = goal.get("budget_tokens")
    return {
        **goal,
        "used_tokens": used,
        "remaining_tokens": max(0, int(budget) - used) if budget else None,
        "over_budget": bool(budget and used > int(budget)),
    }


def _render_goal(session: SessionRuntime, goal: dict[str, Any] | None) -> None:
    """Render the current goal and its lifecycle state in Spanish."""
    if goal is None:
        console.print("[dim]No hay un objetivo de sesión activo.[/]")
        console.print(
            "[dim]Uso: /goal <objetivo> [--budget 8k] · "
            "/goal pause|resume|complete|clear|json[/]"
        )
        return

    snapshot = _goal_snapshot(session, goal)
    labels = {"active": "activo", "paused": "en pausa", "completed": "completado"}
    console.print("\n[bold realm]᛭ Objetivo de sesión[/]")
    console.print(f"  [bold cyan]{snapshot['objective']}[/]")
    console.print(f"  [info]Estado:[/] {labels[snapshot['status']]}")
    if snapshot.get("budget_tokens"):
        console.print(
            f"  [info]Tokens:[/] {snapshot['used_tokens']:,} / "
            f"{snapshot['budget_tokens']:,} · restantes {snapshot['remaining_tokens']:,}"
        )
        if snapshot["over_budget"]:
            console.print("  [warning]⚠ Se superó el presupuesto del objetivo.[/]")
    console.print()


async def run_goal_command(session: SessionRuntime, args: str) -> None:
    """Gestiona un objetivo persistente y visible para el modelo por sesión."""
    text = args.strip()
    goal = _goal_from_session(session)
    command = text.lower()

    if not text or command in {"status", "show"}:
        _render_goal(session, goal)
        return
    if command == "json":
        payload = _goal_snapshot(session, goal) if goal else None
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        sys.stdout.flush()
        return
    if command in {"clear", "reset"}:
        _sync_goal_message(session, None)
        console.print("[success]✓ Objetivo de sesión eliminado.[/]")
        return
    if command in {"pause", "resume", "complete"}:
        if goal is None:
            render_error("No hay un objetivo de sesión. Usa /goal <objetivo>.")
            return
        target_state = {"pause": "paused", "resume": "active", "complete": "completed"}[
            command
        ]
        goal = dict(goal)
        goal["status"] = target_state
        if target_state == "completed":
            goal["completed_at"] = datetime.now(UTC).isoformat()
        else:
            goal.pop("completed_at", None)
        _sync_goal_message(session, goal)
        _render_goal(session, goal)
        return

    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return
    if tokens and tokens[0].lower() == "set":
        tokens.pop(0)

    budget: int | None = None
    if "--budget" in tokens:
        index = tokens.index("--budget")
        if index + 1 >= len(tokens):
            render_error("Uso: /goal <objetivo> [--budget 8k]")
            return
        try:
            budget = _parse_goal_budget(tokens[index + 1])
        except ValueError as exc:
            render_error(str(exc))
            return
        del tokens[index : index + 2]

    objective = " ".join(tokens).strip()
    if not objective:
        render_error("Uso: /goal <objetivo> [--budget 8k]")
        return
    total = int((getattr(session, "_total_usage", {}) or {}).get("total_tokens", 0))
    goal = {
        "objective": objective,
        "status": "active",
        "created_at": datetime.now(UTC).isoformat(),
        "started_tokens": total,
        "budget_tokens": budget,
    }
    _sync_goal_message(session, goal)
    console.print("[success]✓ Objetivo de sesión fijado y compartido con el modelo.[/]")
    _render_goal(session, goal)


# ── /context ───────────────────────────────────────────────────────────
#
# Orphan gap surfaced by the 2026-07-26 audit: ``render.render_context()``
# already implements a Rich progress bar for the session's current context
# window usage, and the docstring on ``providers.estimate_context_window``
# literally says "Used for the /context progress bar" — but no slash
# command ever wired the two together. Both Claude Code (``/context``) and
# Gemini CLI (``/context``) ship a context-window progress command as a
# de-facto standard, and Reddit r/ClaudeAI / r/LocalLLaMA threads
# routinely ask "how much room do I have left?" mid-session. So this
# command finally exposes it to the REPL.
#
# Usage:
#   /context            — progress bar + one-line summary
#   /context full       — breakdown of system / tools / history / plan
#   /context json       — machine-readable snapshot (bypasses Rich)
#   /context <n>%       — set the warning threshold for `/context` itself
#                        (informational; the bar always uses the model
#                        window from providers.estimate_context_window())


# Default warning threshold; 0 disables the heads-up. Persists per-user
# so users can dial it down once they learn their model's sweet spot.
_CONTEXT_WARN_FILE = CONFIG_DIR / "context_warn.json"
_DEFAULT_WARN_PCT = 80.0


def _load_warn_pct() -> float:
    """Return the persisted context-warn threshold (0 disables)."""
    try:
        if _CONTEXT_WARN_FILE.exists():
            data = json.loads(_CONTEXT_WARN_FILE.read_text(encoding="utf-8"))
            pct = float(data.get("warn_pct", _DEFAULT_WARN_PCT))
            if 0.0 <= pct <= 100.0:
                return pct
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return _DEFAULT_WARN_PCT


def _save_warn_pct(pct: float) -> None:
    """Persist the context-warn threshold; clamped to [0, 100]."""
    pct = max(0.0, min(100.0, float(pct)))
    _CONTEXT_WARN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONTEXT_WARN_FILE.write_text(
        json.dumps({"warn_pct": pct}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _context_snapshot(session: SessionRuntime) -> dict:
    """Build a snapshot of the session's current context usage.

    Mirrors the logic in :func:`render.render_context` so the JSON path
    and the rendered panel agree on numbers. Returns a plain dict with
    the model window, the five usage buckets (system / tools / history /
    pinned / total), the bar percentage, and a flag indicating whether
    the configured warn threshold has been crossed.
    """
    from ..providers import estimate_context_window

    model = getattr(session.config, "model", "") or ""
    max_tokens = estimate_context_window(model) or 0

    usage = getattr(session, "_total_usage", {}) or {}
    used = int(usage.get("total_tokens", 0) or usage.get("prompt_tokens", 0) or 0)

    # Per-bucket estimates. We split based on the same heuristic
    # render.render_context uses (system_prompt words, tool schema JSON
    # words, history = remainder) so the rendered panel and JSON agree.
    system_prompt = getattr(session, "system_prompt", "") or ""
    system_size = len(system_prompt.split()) if isinstance(system_prompt, str) else 0

    tools_cache = getattr(session, "_tools_cache", None) or []
    if tools_cache:
        tools_text = json.dumps(tools_cache, separators=(",", ":"))
        tools_size = len(tools_text.split()) if tools_text else 0
    else:
        tools_size = 0

    history_size = used - system_size - tools_size
    if history_size < 0:
        # Provider-reported usage can be smaller than the local heuristic
        # when tools are disabled. Fall back to whatever is left.
        history_size = max(0, used)

    pinned = getattr(session, "_pinned_messages", None) or []
    pinned_count = len(pinned)
    pinned_size = sum(
        len(str(m.get("content", "")).split()) for m in pinned if isinstance(m, dict)
    )

    percentage = (used / max_tokens) if max_tokens else 0.0
    warn_pct = _load_warn_pct()
    over_warn = bool(warn_pct and max_tokens and percentage * 100 >= warn_pct)

    plan = getattr(session, "current_plan", None)
    plan_done: int | None = None
    plan_total: int | None = None
    if plan is not None:
        plan_total = len(getattr(plan, "steps", []) or [])
        plan_done = sum(1 for s in getattr(plan, "steps", []) if getattr(s, "done", False))

    return {
        "model": model,
        "max_tokens": max_tokens,
        "used": used,
        "remaining": max(0, max_tokens - used),
        "percentage": round(percentage, 4),
        "warn_pct": warn_pct,
        "over_warn": over_warn,
        "buckets": {
            "system": system_size,
            "tools": tools_size,
            "history": history_size,
            "pinned": pinned_size,
        },
        "pinned_count": pinned_count,
        "plan": {
            "done": plan_done,
            "total": plan_total,
        },
    }


async def run_context_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /context para inspeccionar el uso de la ventana de contexto.

    Examples:
        /context                   — barra de progreso + resumen en una línea
        /context full              — desglose por buckets (sistema / tools / historial / pins / plan)
        /context json              — snapshot JSON (omite Rich markup)
        /context warn <n>          — fija el umbral de aviso (0..100, 0 desactiva)
    """
    text = args.strip()

    # /context warn <n>  ── configure the warning threshold and return.
    if text.lower().startswith("warn"):
        parts = text.split()
        if len(parts) < 2:
            current = _load_warn_pct()
            console.print(
                f"[info]Umbral actual:[/info] [bold cyan]{current:.0f}%[/bold cyan]  "
                f"[dim](pasá `/context warn <0-100>` para cambiarlo)[/dim]"
            )
            return
        try:
            pct = float(parts[1])
        except ValueError:
            render_error("Uso: /context warn <0-100>  (número entre 0 y 100)")
            return
        _save_warn_pct(pct)
        if pct <= 0:
            console.print("[success]✓ Aviso de contexto desactivado.[/]")
        else:
            console.print(
                f"[success]✓ Umbral fijado a {pct:.0f}% — "
                f"un aviso aparecerá cuando /context supere esa fracción.[/]"
            )
        return

    snap = _context_snapshot(session)

    if text.lower() == "json":
        import sys as _sys

        _sys.stdout.write(
            json.dumps(snap, ensure_ascii=False, indent=2) + "\n"
        )
        _sys.stdout.flush()
        return

    # Both "default" and "full" share the same progress bar; "full" adds
    # the per-bucket table underneath. We render the bar ourselves so
    # colour thresholds and post-line warnings stay consistent across
    # the two paths (render.render_context has its own renderer but we
    # need the JSON-mode contract to share these numbers exactly).
    from rich.table import Table

    max_tokens = snap["max_tokens"]
    used = snap["used"]
    pct = snap["percentage"]
    pct_pct = pct * 100.0

    bar_width = 30
    filled = int(bar_width * pct)
    empty = bar_width - filled
    if pct < 0.5:
        color = "green"
    elif pct < 0.8:
        color = "yellow"
    else:
        color = "red"
    bar = "█" * filled + "░" * empty
    pct_text = f"[{color}]{bar}[/]  {pct_pct:.1f}%"

    console.print(pct_text)
    console.print(
        f"  [info]Usados:[/] [model]{used:,}[/] / [model]{max_tokens:,}[/] tokens  ·  "
        f"[info]Restantes:[/] [model]{snap['remaining']:,}[/]  ·  "
        f"[info]Modelo:[/] [model]{snap['model'] or '?'}[/]"
    )

    if snap["over_warn"]:
        console.print(
            f"\n  [warning]⚠ Contexto al {pct_pct:.0f}% — "
            f"superó el umbral configurado ({snap['warn_pct']:.0f}%). "
            f"Considerá /compact o /clear.[/]"
        )

    if text.lower() != "full":
        return

    # Full breakdown — same buckets as the JSON path.
    buckets = snap["buckets"]
    table = Table(
        title="[bold realm]⚔ Desglose de contexto ⚔[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Bucket", style="tool.name")
    table.add_column("Tokens", justify="right", style="model")
    table.add_column("% del total", justify="right", style="dim")

    total_accounted = sum(int(v) for v in buckets.values()) or 1
    for label, key in (
        ("Prompt del sistema", "system"),
        ("Descripción de herramientas", "tools"),
        ("Historial de mensajes", "history"),
        ("Mensajes fijados (/pin)", "pinned"),
    ):
        value = int(buckets.get(key, 0))
        share = (value / total_accounted) * 100 if total_accounted else 0.0
        table.add_row(label, f"{value:,}", f"{share:.1f}%")

    plan_info = snap["plan"]
    plan_str = ""
    if plan_info["total"]:
        plan_str = f" · [info]Plan:[/] [model]{plan_info['done']}/{plan_info['total']}[/]"
    if snap["pinned_count"]:
        plan_str += f"  · [info]Pins:[/] [model]{snap['pinned_count']}[/]"

    console.print(table)
    if plan_str:
        console.print(f"  {plan_str}")
    console.print(
        f"  [dim]Ventana del modelo: {max_tokens:,} tokens. "
        "Para cambiar el umbral: `/context warn <0-100>`.[/dim]"
    )
    console.print()
