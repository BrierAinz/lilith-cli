"""Saved-session slash commands: /history, /capture, /export, /replay, /fork, /recent, /file and /template."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import CONFIG_DIR
from ..render import console, get_theme, render_error
from ._shared import _format_history_timestamp, _get_editor

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime


async def run_template_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /template para listar y aplicar plantillas de prompts.

    Examples:
        /template
        /template list
        /template apply <nombre>
    """
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        templates = _list_templates()
        if not templates:
            console.print("[dim]No hay plantillas definidas.[/]")
            return
        console.print("\n[bold realm]᛭ Plantillas disponibles[/]")
        for name in templates:
            console.print(f"  [bold cyan]{name}[/]")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("apply", "use"):
        if not rest:
            render_error("Uso: /template apply <nombre>")
            return
        template = _get_template(rest)
        if template is None:
            render_error(f"Plantilla no encontrada: {rest}")
            return
        console.print(f"[success]✓ Plantilla aplicada: {rest}[/]")
        console.print(f"[dim]{template}[/]")
        return

    if subcmd in ("show", "view"):
        if not rest:
            render_error("Uso: /template show <nombre>")
            return
        template = _get_template(rest)
        if template is None:
            render_error(f"Plantilla no encontrada: {rest}")
            return
        console.print(f"\n[bold realm]᛭ Plantilla {rest}[/]")
        console.print(template)
        console.print()
        return

    render_error("Uso: /template [list|apply <nombre>|show <nombre>]")


# ── Template storage helpers ──────────────────────────────────────────

_TEMPLATES_DIR = CONFIG_DIR / "templates"


def _list_templates() -> list[str]:
    """Lista nombres de plantillas guardadas."""
    if not _TEMPLATES_DIR.exists():
        return []
    return sorted(p.stem for p in _TEMPLATES_DIR.glob("*.txt"))


def _get_template(name: str) -> str | None:
    """Carga una plantilla por nombre."""
    path = _TEMPLATES_DIR / f"{name}.txt"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None

# ── Replay helpers ───────────────────────────────────────────────────

_REPLAY_DIR = CONFIG_DIR / "replays"


async def run_replay_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /replay para repetir una secuencia de comandos guardada.

    Examples:
        /replay
        /replay <id>
        /replay save <nombre>
    """
    _REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        replays = sorted(_REPLAY_DIR.glob("*.json"))
        if not replays:
            console.print("[dim]No hay replays guardados.[/]")
            return
        console.print("\n[bold realm]᛭ Replays guardados[/]")
        for r in replays:
            console.print(f"  [bold cyan]{r.stem}[/]")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("save", "store"):
        if not rest:
            render_error("Uso: /replay save <nombre>")
            return
        if not session.history:
            render_error("No hay historial para guardar como replay.")
            return
        path = _REPLAY_DIR / f"{rest}.json"
        path.write_text(json.dumps(session.history, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[success]✓ Replay guardado: {rest}[/]")
        return

    if subcmd in ("load", "play"):
        name = rest if rest else subcmd
        path = _REPLAY_DIR / f"{name}.json"
        if not path.exists():
            render_error(f"Replay no encontrado: {name}")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            render_error(f"Error cargando replay: {exc}")
            return
        if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
            render_error(f"Formato de replay inválido: {name}")
            return
        session.history = data
        console.print(f"[success]✓ Replay cargado: {name} ({len(data)} mensajes)[/]")
        return

    # /replay <id>
    path = _REPLAY_DIR / f"{text}.json"
    if not path.exists():
        render_error(f"Replay no encontrado: {text}")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        render_error(f"Error cargando replay: {exc}")
        return
    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
        render_error(f"Formato de replay inválido: {text}")
        return
    session.history = data
    console.print(f"[success]✓ Replay cargado: {text} ({len(data)} mensajes)[/]")


# ── /file command ────────────────────────────────────────────────────


async def run_file_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /file para añadir el contenido de un archivo al contexto del usuario.

    Examples:
        /file src/main.py
        /file --list
    """
    text = args.strip()

    # Accept one quoted path so projects in directories with spaces work on
    # Windows, while preserving ordinary unquoted paths.
    if text and text.lower() not in ("list", "ls", "--list", "clear", "reset"):
        try:
            parsed = shlex.split(text, posix=False)
        except ValueError as exc:
            render_error(f"Ruta entre comillas inválida: {exc}")
            return
        if len(parsed) == 1:
            text = parsed[0]
            if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
                text = text[1:-1]

    if not text or text.lower() in ("list", "ls", "--list"):
        files: list[str] = getattr(session, "_user_files", [])
        if not files:
            console.print("[dim]No hay archivos adjuntos en el contexto.[/]")
            return
        console.print("\n[bold realm]᛭ Archivos en el contexto[/]")
        for f in files:
            console.print(f"  [bold cyan]{f}[/]")
        console.print()
        return

    if text.lower() in ("clear", "reset"):
        session._user_files = []
        console.print("[success]✓ Archivos adjuntos eliminados.[/]")
        return

    path = Path(text)
    if not path.exists():
        render_error(f"Archivo no encontrado: {text}")
        return
    if not path.is_file():
        render_error(f"La ruta no es un archivo: {text}")
        return

    files = getattr(session, "_user_files", None)
    if files is None:
        session._user_files = []
        files = session._user_files

    files.append(text)
    console.print(f"[success]✓ Archivo añadido al contexto: {text}[/]")


# ── /export command ──────────────────────────────────────────────────


async def run_export_command(session: SessionRuntime, args: str) -> None:
    """Export conversation (/export [name] [--format json|md] [--output <path>])."""
    # Manual parser to preserve paths with backslashes/spaces
    # argparse + shlex destroys Windows paths, so we parse flags manually.
    name: str | None = None
    fmt = "json"
    output_path: str | None = None
    tokens = args.split()
    positional: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--format" and i + 1 < len(tokens):
            fmt = tokens[i + 1]
            i += 2
        elif tok.startswith("--format="):
            fmt = tok.split("=", 1)[1]
            i += 1
        elif tok == "--output" and i + 1 < len(tokens):
            output_path = tokens[i + 1]
            i += 2
        elif tok.startswith("--output="):
            output_path = tok.split("=", 1)[1]
            i += 1
        else:
            positional.append(tok)
            i += 1
    if positional:
        name = positional[0]

    if fmt not in ("json", "md"):
        render_error("Formato inv\u00e1lido. Use: json o md")
        return

    # Determine output path
    conversations_dir = CONFIG_DIR / "conversations"
    if output_path:
        filepath = Path(output_path).expanduser()
    else:
        conversations_dir.mkdir(parents=True, exist_ok=True)
        name = name or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        ext = "md" if fmt == "md" else "json"
        filepath = conversations_dir / f"{name}.{ext}"

    # Build content based on format
    if fmt == "md":
        lines_md = []
        lines_md.append(f"# Conversación exportada {datetime.now(UTC).isoformat()}")
        lines_md.append(f"\n**Model:** {session.config.model}  ")
        lines_md.append(f"**Provider:** {session.config.provider}\n")
        for msg in session.history:
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            lines_md.append(f"## {role}\n")
            lines_md.append(str(content))
            lines_md.append("")
        content_str = "\n".join(lines_md)
    else:
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "model": session.config.model,
            "provider": session.config.provider,
            "messages": session.history,
            "usage": session.total_usage,
        }
        content_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content_str, encoding="utf-8")
    except OSError as exc:
        render_error(f"No se pudo escribir {filepath}: {exc}")
        return

    console.print(f"[success]✓ Conversación exportada:[/success] [bold cyan]{filepath}[/bold cyan]  [dim]({fmt})[/dim]")


# ── /capture command ─────────────────────────────────────────────────


def _capture_usage() -> str:
    """Devuelve la línea de uso de /capture en español."""
    return "Uso: /capture [nombre] [--output <ruta>] [--include-tools] [--no-usage] [--tags <tags>] [--exclude-system] [--first N | --last N]"


# Flags whose value is a variadic list that should stop at the next flag.
# We join their value tokens with ``,`` during preprocessing so argparse
# (with ``nargs="*"``) doesn't greedily swallow the next flag's tokens.
_CAPTURE_VARIADIC_FLAGS = frozenset({"--tags", "--output"})
_CAPTURE_KNOWN_FLAGS = frozenset({
    "--output", "--include-tools", "--no-usage", "--tags",
    "--exclude-system", "--first", "--last",
})


def _capture_positive_int(value: str) -> int:
    """argparse ``type=`` for ``--first``/``--last``: must be a positive integer.

    Raises ``argparse.ArgumentTypeError`` so argparse wraps it into the standard
    error path (``parser.error``), which we then surface as ``render_error`` +
    usage hint at the top level.
    """
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"debe ser entero positivo, recibí: {value!r}"
        )
    if n < 1:
        raise argparse.ArgumentTypeError(
            f"debe ser entero positivo, recibí: {value!r}"
        )
    return n


def _capture_parse_args(args: str) -> tuple[str | None, str | None, bool, bool, list[str], bool, int | None, int | None] | None:
    """Parsea argumentos de /capture con ``argparse`` preservando rutas Windows.

    La firma pública y el orden de la tupla retornada coinciden con la
    implementación manual anterior::

        (name, output_path, include_tools, include_usage, tags,
         exclude_system, first_n, last_n)

    ``--first N`` y ``--last N`` son mutuamente excluyentes (``--first N``
    conserva las primeras N, ``--last N`` las últimas N; ``None`` significa
    "sin límite"). ``--tags`` acepta una lista separada por espacios o por
    comas (incluyendo la forma ``--tags=foo,bar``), se le quita el ``#``
    inicial y se descartan las piezas vacías. ``--output`` acepta tanto
    ``--output ruta`` como ``--output=ruta``.

    Devuelve ``None`` y emite ``render_error`` + línea de uso ante cualquier
    argumento inválido (flag desconocido, entero no positivo, ``--tags``
    vacío, etc.).
    """
    raw_tokens = (args or "").split()

    # Preprocess: ``--tags`` y ``--output`` son variádicos pero deben detenerse
    # en la próxima flag. Unimos sus valores con ``,`` en un solo token para
    # que ``argparse`` (con ``nargs="*"``) no se los trague por codicia y
    # respete las flags vecinas. La normalización posterior hace split por
    # coma igual que antes.
    tokens: list[str] = []
    i = 0
    while i < len(raw_tokens):
        tok = raw_tokens[i]
        if tok in _CAPTURE_VARIADIC_FLAGS:
            tokens.append(tok)
            i += 1
            value_tokens: list[str] = []
            while i < len(raw_tokens) and raw_tokens[i] not in _CAPTURE_KNOWN_FLAGS:
                value_tokens.append(raw_tokens[i])
                i += 1
            if value_tokens:
                tokens.append(",".join(value_tokens))
        else:
            tokens.append(tok)
            i += 1

    parser = argparse.ArgumentParser(
        prog="/capture", add_help=False, exit_on_error=False,
    )
    parser.add_argument("--output", nargs="*", default=None)
    parser.add_argument("--include-tools", action="store_true")
    parser.add_argument("--no-usage", action="store_true")
    parser.add_argument("--exclude-system", action="store_true")
    parser.add_argument("--tags", nargs="*", default=None)
    parser.add_argument("--first", type=_capture_positive_int, default=None, dest="first_n")
    parser.add_argument("--last", type=_capture_positive_int, default=None, dest="last_n")

    try:
        parsed, unknown = parser.parse_known_args(tokens)
    except argparse.ArgumentError as exc:
        msg = str(exc).strip() or "argumento inválido"
        render_error(msg)
        console.print(_capture_usage())
        return None

    # Cualquier flag desconocida cae en ``unknown``; el resto es el nombre
    # posicional (puede contener espacios y, en Windows, rutas con guiones).
    stray_flags = [tok for tok in unknown if tok.startswith("--")]
    if stray_flags:
        render_error(f"Opción desconocida: {stray_flags[0]}. {_capture_usage()}")
        return None

    name = " ".join(unknown).strip() or None

    # Normalización de ``--tags``: coma-separado, ``#`` inicial fuera, vacíos
    # descartados. Idéntica a la implementación manual anterior.
    tags_raw = parsed.tags or []
    flat: list[str] = []
    for t in tags_raw:
        flat.extend(t.split(","))
    tags = [t.lstrip("#").strip(",").strip() for t in flat]
    tags = [t for t in tags if t]
    if parsed.tags is not None and not tags:
        render_error(_capture_usage())
        return None

    # Normalización de ``--output``: une los tokens con espacio (preserva
    # rutas con espacios en Windows) y rechaza valores vacíos.
    if parsed.output is not None:
        output_path = " ".join(parsed.output).strip() or None
        if output_path is None:
            render_error(_capture_usage())
            return None
    else:
        output_path = None

    # ``--no-usage`` es ``store_true``; ``include_usage`` es su negación
    # (``True`` por defecto).
    include_usage = not parsed.no_usage

    return (
        name,
        output_path,
        parsed.include_tools,
        include_usage,
        tags,
        parsed.exclude_system,
        parsed.first_n,
        parsed.last_n,
    )


def _capture_usage_dict(session: SessionRuntime) -> dict[str, Any]:
    """Devuelve el uso total de la sesión como diccionario seguro."""
    usage = getattr(session, "total_usage", {}) or {}
    return dict(usage) if isinstance(usage, dict) else {"uso": usage}


def _capture_message_text(content: Any) -> str:
    """Convierte contenido de mensaje a texto legible para Markdown."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, (dict, list, tuple)):
        return json.dumps(content, ensure_ascii=False, indent=2, default=str)
    return str(content)


def _capture_role_heading(role: str) -> str:
    """Mapea roles internos a encabezados humanos en español."""
    role_key = role.lower()
    if role_key == "user":
        return "👤 Usuario"
    if role_key == "assistant":
        return "🤖 Lilith"
    if role_key == "tool":
        return "🔧 Herramienta"
    if role_key == "system":
        return "⚙️ Sistema"
    return f"📝 {role or 'Mensaje'}"


def _capture_tool_args_preview(entry: dict[str, Any]) -> str:
    """Devuelve una vista previa compacta de argumentos de herramienta."""
    raw_args = entry.get("arguments", entry.get("args", entry.get("input", "")))
    if isinstance(raw_args, str):
        preview = raw_args
    else:
        preview = json.dumps(raw_args, ensure_ascii=False, default=str)
    preview = " ".join(preview.split())
    return preview if len(preview) <= 80 else preview[:77] + "..."


async def run_capture_command(session: SessionRuntime, args: str) -> None:
    """Guarda una transcripción Markdown limpia de la sesión activa."""
    theme = get_theme()
    text = (args or "").strip()

    if text.lower() in ("help", "--help", "-h", "?"):
        console.print(
            f"\n[bold realm]{theme.prompt_prefix} /capture[/bold realm] "
            "[dim]— transcripción Markdown de la sesión[/dim]"
        )
        console.print(_capture_usage())
        console.print("  [cyan]/capture[/]                         [dim]# nombre automático[/dim]")
        console.print("  [cyan]/capture sesión[/]                  [dim]# ~/.yggdrasil/transcripts/sesión.md[/dim]")
        console.print("  [cyan]/capture --output <ruta>[/]         [dim]# ruta exacta[/dim]")
        console.print("  [cyan]/capture --include-tools[/]         [dim]# incluye herramientas[/dim]")
        console.print("  [cyan]/capture --no-usage[/]              [dim]# omite uso de tokens[/dim]")
        console.print("  [cyan]/capture --tags <tags>[/]          [dim]# ej. --tags work,urgent[/dim]")
        console.print("  [cyan]/capture --exclude-system[/]       [dim]# omite mensajes system/tool[/dim]")
        console.print("  [cyan]/capture --first N | --last N[/]   [dim]# limita a N mensajes[/dim]")
        return

    history = getattr(session, "history", None) or []
    if not history:
        render_error("No hay conversación para capturar todavía.")
        return

    parsed = _capture_parse_args(text)
    if parsed is None:
        return
    name, output_path, include_tools, include_usage, tags, exclude_system, first_n, last_n = parsed

    transcripts_dir = CONFIG_DIR / "transcripts"
    if output_path:
        filepath = Path(output_path).expanduser()
    else:
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        name = name or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filepath = transcripts_dir / f"{name}.md"

    timestamp = datetime.now(UTC).isoformat()
    usage = _capture_usage_dict(session)
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens) or 0

    lines: list[str] = [
        f"# Lilith transcript — {timestamp}",
        "",
        f"- **Modelo:** {getattr(session.config, 'model', '?')}",
        f"- **Proveedor:** {getattr(session.config, 'provider', '?')}",
        f"- **Mensajes:** {len(history)}",
        f"- **Tokens:** {total_tokens} (prompt {prompt_tokens} + completion {completion_tokens})",
    ]
    if tags:
        lines.append(f"- **Tags:** {', '.join('#' + t for t in tags)}")
    lines.extend(["", "---", ""])

    messages_to_render = history
    if exclude_system:
        messages_to_render = [
            m for m in history
            if (m.get("role") if isinstance(m, dict) else "") not in ("system", "tool")
        ]
    if first_n is not None:
        messages_to_render = messages_to_render[:first_n]
    elif last_n is not None:
        messages_to_render = messages_to_render[-last_n:]
    for msg in messages_to_render:
        if isinstance(msg, dict):
            role = str(msg.get("role", "Mensaje"))
            content = msg.get("content", "")
        else:
            role = "Mensaje"
            content = msg
        lines.append(f"## {_capture_role_heading(role)}")
        lines.append("")
        lines.append(_capture_message_text(content))
        lines.append("")

    if include_tools:
        lines.append("## 🔧 Herramientas llamadas")
        lines.append("")
        tool_history = getattr(session, "_tool_call_history", None) or []
        if tool_history:
            for entry in tool_history:
                name_tool = str(entry.get("name", "herramienta"))
                duration = entry.get("duration", 0) or 0
                try:
                    duration_text = f"{float(duration):.3f}"
                except (TypeError, ValueError):
                    duration_text = str(duration)
                preview = _capture_tool_args_preview(entry)
                lines.append(f"- **{name_tool}** — {duration_text}s — {preview}")
        else:
            lines.append("_No hubo herramientas llamadas._")
        lines.append("")

    if include_usage:
        lines.append("## 📊 Uso")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(usage, ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")

    content_str = "\n".join(lines).rstrip() + "\n"

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content_str, encoding="utf-8")
    except OSError as exc:
        render_error(f"No se pudo escribir {filepath}: {exc}")
        return

    console.print(
        f"[success]✓ Transcripción capturada:[/success] "
        f"[bold frost]{filepath}[/bold frost]  [dim]({theme.name})[/dim]"
    )

# ── /history command ─────────────────────────────────────────────────


def _select_saved_conversation(
    conversations: list[dict[str, Any]], selector: str
) -> dict[str, Any] | None:
    """Resolve a saved conversation by 1-based index or unique text match."""
    try:
        index = int(selector) - 1
    except ValueError:
        needle = selector.casefold()
        matches = [
            conversation
            for conversation in conversations
            if needle in str(conversation.get("name", "")).casefold()
            or needle in str(conversation.get("preview", "")).casefold()
        ]
        if not matches:
            render_error(f"No encontré una sesión guardada que coincida con {selector!r}.")
            return None
        if len(matches) > 1:
            render_error(
                f"{selector!r} coincide con {len(matches)} sesiones; "
                "usá el número mostrado por /history sessions."
            )
            return None
        return matches[0]

    if 0 <= index < len(conversations):
        return conversations[index]
    render_error(
        f"Índice fuera de rango: {selector} "
        f"(hay {len(conversations)} sesiones guardadas)."
    )
    return None


def _saved_session_slug(name: str) -> str:
    """Return a filesystem-safe, human-readable saved-session name."""
    slug = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("-._")
    return slug[:60]


def _saved_session_payload(conversations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the stable JSON response for ``/history sessions --json``."""
    return {
        "kind": "saved_sessions",
        "count": len(conversations),
        "sessions": [
            {
                "index": index,
                "name": conversation.get("name", ""),
                "timestamp": conversation.get("timestamp", ""),
                "model": conversation.get("model", "unknown"),
                "provider": conversation.get("provider", "unknown"),
                "message_count": conversation.get("message_count", 0),
                "preview": conversation.get("preview", ""),
            }
            for index, conversation in enumerate(conversations, start=1)
        ],
    }


def _run_saved_sessions_command(args: str) -> None:
    """List, rename, or delete persisted conversations from ``/history``."""
    from ..repl import _list_saved_conversations

    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    action = tokens[0].lower() if tokens else "sessions"
    conversations = _list_saved_conversations()

    if action in ("sessions", "saved"):
        unknown = [token for token in tokens[1:] if token != "--json"]
        if unknown:
            render_error("Uso: /history sessions [--json]")
            return
        if "--json" in tokens[1:]:
            _print_history_json(_saved_session_payload(conversations))
            return
        if not conversations:
            console.print("[dim]No hay sesiones guardadas.[/]")
            return

        from rich.table import Table

        table = Table(
            title="[bold realm]᛭ Sesiones guardadas[/]",
            border_style="cyan",
            header_style="bold cyan",
        )
        table.add_column("#", justify="right", style="dim")
        table.add_column("Nombre", style="bold frost")
        table.add_column("Modelo")
        table.add_column("Mensajes", justify="right")
        table.add_column("Vista previa", style="dim", max_width=50)
        for index, conversation in enumerate(conversations, start=1):
            table.add_row(
                str(index),
                str(conversation.get("name", "")),
                str(conversation.get("model", "unknown")),
                str(conversation.get("message_count", 0)),
                str(conversation.get("preview", "")) or "(sin mensajes de usuario)",
            )
        console.print(table)
        console.print(
            "[dim]/history rename <número|texto> <nombre> · "
            "/history delete <número|texto> --yes[/]"
        )
        return

    if action not in ("rename", "delete"):
        render_error(
            "Uso: /history [número] [--tool <nombre>] [--json] | "
            "sessions [--json] | rename <selector> <nombre> | "
            "delete <selector> --yes"
        )
        return
    if not conversations:
        console.print("[dim]No hay sesiones guardadas.[/]")
        return

    if action == "rename":
        if len(tokens) < 3:
            render_error("Uso: /history rename <número|texto> <nuevo nombre>")
            return
        conversation = _select_saved_conversation(conversations, tokens[1])
        if conversation is None:
            return
        slug = _saved_session_slug(" ".join(tokens[2:]))
        if not slug:
            render_error("El nuevo nombre debe contener letras o números.")
            return

        source = Path(conversation["file"])
        base_match = re.match(r"^(conv_\d{8}_\d{6})", source.stem)
        base = base_match.group(1) if base_match else source.stem.split("__", 1)[0]
        target = source.with_name(f"{base}__{slug}{source.suffix}")
        if target.exists() and target != source:
            render_error(f"Ya existe una sesión llamada {target.stem!r}.")
            return
        try:
            source.rename(target)
        except OSError as exc:
            render_error(f"No pude renombrar la sesión: {exc}")
            return
        console.print(f"[success]✓ Sesión renombrada: {target.stem}[/]")
        return

    selector_tokens = [token for token in tokens[1:] if token != "--yes"]
    if len(selector_tokens) != 1:
        render_error("Uso: /history delete <número|texto> --yes")
        return
    conversation = _select_saved_conversation(conversations, selector_tokens[0])
    if conversation is None:
        return
    if "--yes" not in tokens[1:]:
        console.print(
            f"[warning]Se eliminará {conversation['name']!r}. "
            f"Confirmá con /history delete {selector_tokens[0]} --yes[/]"
        )
        return
    try:
        Path(conversation["file"]).unlink()
    except OSError as exc:
        render_error(f"No pude eliminar la sesión: {exc}")
        return
    console.print(f"[success]✓ Sesión eliminada: {conversation['name']}[/]")


async def run_history_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /history para mostrar los últimos mensajes de la conversación.

    Examples:
        /history
        /history 10
        /history --tool file_read
        /history 20 --tool file_read
        /history 20 --json
        /history sessions
        /history rename 2 refactor-auth
        /history delete refactor-auth --yes
    """
    # ── Parse args: optional <limit>, --tool <name>, and --json ──────────
    text = args.strip()
    first = text.split(maxsplit=1)[0].lower() if text else ""
    if first in ("sessions", "saved", "rename", "delete"):
        _run_saved_sessions_command(text)
        return

    limit: int | None = None
    tool_filter: str | None = None
    json_mode = False

    if text:
        tokens = text.split()
        remaining: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--tool" and i + 1 < len(tokens):
                tool_filter = tokens[i + 1]
                i += 2
            elif tok.startswith("--tool="):
                tool_filter = tok.split("=", 1)[1]
                i += 1
            elif tok == "--json":
                json_mode = True
                i += 1
            else:
                remaining.append(tok)
                i += 1

        if remaining:
            try:
                limit = int(remaining[0])
                if limit < 1:
                    raise ValueError
            except ValueError:
                render_error("Uso: /history [número] [--tool <nombre>] [--json]")
                return

    history = session.history or []

    # ── --tool filter: pull matching tool calls from session._tool_call_history ──
    if tool_filter:
        tool_history: list[dict[str, Any]] = (
            getattr(session, "_tool_call_history", []) or []
        )
        matching = [h for h in tool_history if h.get("name") == tool_filter]
        if not matching:
            if json_mode:
                _print_history_json(
                    {
                        "kind": "tool_calls",
                        "tool": tool_filter,
                        "count": 0,
                        "calls": [],
                    }
                )
            else:
                console.print(
                    f"[dim]No hay llamadas registradas para ‘{tool_filter}’.[/dim]"
                )
            return
        if limit is None:
            limit = len(matching)
        else:
            limit = min(limit, len(matching))

        selected = matching[-limit:]
        if json_mode:
            _print_history_json(
                {
                    "kind": "tool_calls",
                    "tool": tool_filter,
                    "count": len(selected),
                    "calls": selected,
                }
            )
            return

        console.print(
            f"[info]Historial (filtrado por: {tool_filter})[/info]"
        )
        for entry in selected:
            ts = _format_history_timestamp(entry.get("timestamp"))
            name = entry.get("name", "?")
            arguments = entry.get("arguments", {})
            try:
                arg_preview = json.dumps(arguments, ensure_ascii=False, default=str)
            except Exception:
                arg_preview = str(arguments)
            if len(arg_preview) > 100:
                arg_preview = arg_preview[:100] + "…"
            console.print(
                f"[dim]{ts}[/dim] [bold cyan]✦[/bold cyan] {name}({arg_preview})"
            )
        return

    # ── Default: show conversation history with role colors and icons ──
    if limit is None:
        limit = 10

    if not history:
        if json_mode:
            _print_history_json({"kind": "messages", "count": 0, "messages": []})
            return
        console.print("[dim]No hay historial para mostrar.[/dim]")
        return

    role_colors = {
        "user": "green",
        "assistant": "blue",
        "system": "yellow",
        "tool": "magenta",
        "function": "cyan",
        "error": "red",
    }
    role_icons = {
        "user": "❯",
        "assistant": "○",
        "system": "⚙",
        "tool": "⚒",
        "function": "∫",
        "error": "✗",
    }

    selected_messages = history[-limit:]
    if json_mode:
        _print_history_json(
            {
                "kind": "messages",
                "count": len(selected_messages),
                "messages": [
                    {
                        "index": index,
                        "role": msg.get("role", "?"),
                        "content": str(msg.get("content", "")),
                        "timestamp": msg.get("timestamp"),
                    }
                    for index, msg in enumerate(
                        selected_messages,
                        start=max(0, len(history) - len(selected_messages)),
                    )
                ],
            }
        )
        return

    console.print("[info]᛭ Historial[/info]")
    for i, msg in enumerate(
        selected_messages,
        start=max(0, len(history) - len(selected_messages)) + 1,
    ):
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:200]
        if len(content) == 200:
            content += "…"
        ts = _format_history_timestamp(msg.get("timestamp"))
        color = role_colors.get(role, "white")
        icon = role_icons.get(role, "•")
        console.print(
            f"[dim]{ts}[/dim] [{color}]{icon} {role}:[/{color}] {content}"
        )


def _print_history_json(payload: dict[str, Any]) -> None:
    """Escribe una respuesta JSON limpia para automatización y scripts."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


# ── /fork command ─────────────────────────────────────────────────────

_FORKS_DIR = Path.home() / ".yggdrasil" / "forks"


def _serialize_session(session: SessionRuntime) -> dict[str, Any]:
    """Return a JSON-serializable snapshot of the session state."""
    return {
        "version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "config": session.config.model_dump(),
        "history": list(session.history),
        "system_prompt": session.system_prompt,
        "total_usage": dict(session._total_usage),
        "per_model_usage": dict(session._per_model_usage),
        "last_user_message": session._last_user_message,
        "agent_mode": session.agent_mode,
        "agent_allow_writes": session._agent_allow_writes,
        "agent_plan_first": session._agent_plan_first,
        "auto_execute": session._auto_execute,
        "auto_approved_patterns": list(session._auto_approved_patterns),
        "stream_enabled": session._stream_enabled,
        "disabled_tools": sorted(session._disabled_tools),
        "pinned_messages": list(session._pinned_messages),
        "tool_call_history": list(session._tool_call_history),
        "command_history": list(session._command_history),
        "file_edit_history": list(session._file_edit_history),
    }


def _deserialize_session(session: SessionRuntime, data: dict[str, Any]) -> None:
    """Restore a session snapshot produced by `_serialize_session`."""
    from ..config import YggdrasilConfig

    cfg_data = data.get("config", session.config.model_dump())
    session.config = YggdrasilConfig(**cfg_data)
    session.system_prompt = data.get("system_prompt", session.config.system_prompt)
    session.history = list(data.get("history", []))
    session._total_usage = dict(data.get("total_usage", session._total_usage))
    session._per_model_usage = dict(data.get("per_model_usage", session._per_model_usage))
    session._last_user_message = data.get("last_user_message", "")
    session.agent_mode = data.get("agent_mode", "default")
    session._agent_allow_writes = data.get("agent_allow_writes", True)
    session._agent_plan_first = data.get("agent_plan_first", False)
    session._auto_execute = data.get("auto_execute", False)
    session._auto_approved_patterns = list(data.get("auto_approved_patterns", []))
    session._stream_enabled = data.get("stream_enabled", True)
    session._disabled_tools = set(data.get("disabled_tools", []))
    session._pinned_messages = list(data.get("pinned_messages", []))
    session._tool_call_history = list(data.get("tool_call_history", []))
    session._command_history = list(data.get("command_history", []))
    session._file_edit_history = list(data.get("file_edit_history", []))


def _fork_path(name: str) -> Path:
    """Return the file path for a named fork."""
    safe_name = re.sub(r"[^\w\-]", "_", name.strip())
    return _FORKS_DIR / f"{safe_name}.json"


def _list_forks() -> list[str]:
    """Return sorted list of fork names currently stored."""
    if not _FORKS_DIR.exists():
        return []
    return sorted(
        p.stem for p in _FORKS_DIR.glob("*.json") if p.is_file()
    )


def _fork_editor_command(editor: str, draft_path: Path) -> list[str]:
    """Build a blocking editor command for a conversation draft."""
    if os.name == "nt":
        raw_command = shlex.split(editor, posix=False)
        command = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}
            else token
            for token in raw_command
        ]
    else:
        command = shlex.split(editor)
    if not command:
        raise ValueError("el comando del editor está vacío")
    executable = Path(command[0]).name.lower()

    wait_editors = {
        "code",
        "code.exe",
        "code-oss",
        "code-oss.exe",
        "cursor",
        "cursor.exe",
        "subl",
        "subl.exe",
    }
    if executable in wait_editors and "--wait" not in command and "-w" not in command:
        command.append("--wait")
    command.append(str(draft_path))
    return command


def _edit_fork_history(snapshot: dict[str, Any], draft_path: Path) -> str | None:
    """Edit a snapshot's messages as JSON, returning an error or ``None``."""
    editor = _get_editor()
    if not editor:
        return "No se encontró un editor. Definí $EDITOR o usá /editor set <comando>."

    draft = {
        "instructions": "Editá solo la lista messages; guardá JSON válido para continuar.",
        "messages": snapshot["history"],
    }
    try:
        draft_path.write_text(
            json.dumps(draft, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        result = subprocess.run(_fork_editor_command(editor, draft_path), check=False)
        if result.returncode != 0:
            return f"El editor terminó con código {result.returncode}; no se guardó el fork."
        edited = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"No se pudo editar la conversación: {exc}"
    finally:
        try:
            draft_path.unlink(missing_ok=True)
        except OSError:
            pass

    messages = edited.get("messages") if isinstance(edited, dict) else None
    if not isinstance(messages, list) or any(
        not isinstance(message, dict)
        or message.get("role") not in {"system", "user", "assistant", "tool"}
        or "content" not in message
        for message in messages
    ):
        return (
            "El borrador debe contener un objeto JSON con una lista messages; "
            "cada mensaje requiere role válido y content."
        )
    snapshot["history"] = messages
    return None


async def run_fork_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /fork para ramificar la sesión actual.

    Examples:
        /fork <nombre>          — Guarda el estado actual en una nueva sesión y vuelve al original
        /fork <nombre> --edit   — Permite editar mensajes antes de guardar la bifurcación
        /fork list              — Lista las sesiones bifurcadas
        /fork switch <nombre>   — Cambia a una sesión bifurcada
        /fork delete <nombre>   — Elimina una sesión bifurcada
    """
    text = args.strip()
    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if subcmd in ("list", "ls"):
        forks = _list_forks()
        if not forks:
            console.print("[dim]No hay sesiones bifurcadas.[/]")
            return
        console.print("\n[bold realm]᛭ Sesiones bifurcadas[/]")
        for name in forks:
            console.print(f"  [bold cyan]{name}[/]")
        console.print()
        return

    if subcmd == "delete":
        if not rest:
            render_error("Uso: /fork delete <nombre>")
            return
        path = _fork_path(rest)
        if not path.exists():
            render_error(f"No existe la sesión bifurcada: {rest}")
            return
        path.unlink()
        console.print(f"[success]✓ Sesión eliminada: [bold cyan]{rest}[/][/]")
        return

    if subcmd == "switch":
        if not rest:
            render_error("Uso: /fork switch <nombre>")
            return
        path = _fork_path(rest)
        if not path.exists():
            render_error(f"No existe la sesión bifurcada: {rest}")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            render_error(f"Error cargando la sesión bifurcada: {exc}")
            return
        _deserialize_session(session, data)
        console.print(
            f"[success]✓ Sesión activa cambiada a [bold cyan]{rest}[/] "
            f"({len(session.history)} mensajes)[/]"
        )
        return

    # /fork <nombre> [--edit] — save a snapshot without mutating the active session
    try:
        create_tokens = shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return
    edit_history = "--edit" in create_tokens
    create_tokens = [token for token in create_tokens if token != "--edit"]
    unknown_flags = [token for token in create_tokens if token.startswith("--")]
    if unknown_flags:
        render_error(f"Opción desconocida: {unknown_flags[0]}")
        return
    name = " ".join(create_tokens).strip()
    if not name:
        render_error("Uso: /fork <nombre> [--edit] | /fork list | /fork switch <nombre> | /fork delete <nombre>")
        return
    path = _fork_path(name)
    _FORKS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = _serialize_session(session)
    if edit_history:
        edit_error = _edit_fork_history(snapshot, path.with_suffix(".edit.tmp"))
        if edit_error:
            render_error(edit_error)
            return
    try:
        path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        console.print(
            f"[success]✓ Sesión bifurcada guardada: [bold cyan]{name}[/] "
            f"({len(snapshot['history'])} mensajes)[/]"
        )
    except Exception as exc:
        render_error(f"Error guardando la sesión bifurcada: {exc}")


# ── /recent command ──────────────────────────────────────────────────


async def run_recent_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """List files edited or written during the current session.

    Reads ``session._file_edit_history`` (populated by agent.py when
    file_write / file_edit tools succeed) and renders the most recent
    entries first. Each entry shows the file path, the tool used, a
    short timestamp, and the file's current size on disk.

    Examples:
        /recent                — show last 10 edits (default)
        /recent 25             — show last 25
        /recent clear          — wipe the in-session history
    """
    history = getattr(session, "_file_edit_history", None)
    if history is None:
        console.print(
            "[warning]Telemetría de ediciones no activa en esta sesión.[/]"
        )
        return

    text = args.strip().lower()

    if text == "clear":
        history.clear()
        console.print("[success]✓ Historial de archivos recientes vaciado.[/]")
        return

    # Parse optional count (default 10, max 50).
    limit = 10
    if text:
        try:
            limit = max(1, min(50, int(text)))
        except ValueError:
            render_error(f"Uso: /recent [N | clear]  ·  N entre 1 y 50, recibí: {text!r}")
            return

    if not history:
        console.print("[dim]No hay archivos editados en esta sesión todavía.[/]")
        return

    # Most recent first, deduped by path so multiple edits to the same
    # file collapse to one entry with the latest timestamp.
    seen: dict[str, dict] = {}
    for entry in reversed(history):
        path = entry.get("path", "")
        if path and path not in seen:
            seen[path] = entry

    items = list(seen.values())[:limit]

    from rich.table import Table

    table = Table(
        title="[bold realm]᛭ Archivos editados recientemente[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("#", style="dim", justify="right", width=4)
    # no_wrap + overflow='ignore' preserves long paths verbatim (no
    # '…' truncation), letting the terminal's own wrap handle them.
    table.add_column("Archivo", style="tool.name", no_wrap=True, overflow="ignore")
    table.add_column("Tool", justify="center", width=10)
    table.add_column("Cuándo", style="dim", width=19)
    table.add_column("Tamaño", justify="right", style="dim", width=10)

    for i, entry in enumerate(items, start=1):
        path_str = entry.get("path", "?")
        tool = entry.get("tool", "?")
        ts = entry.get("timestamp", "")
        if "T" in ts:
            ts = ts.replace("T", " ")[:19]

        # Resolve size from disk; if the file was deleted, show "—".
        try:
            size_bytes = Path(path_str).stat().st_size
            size_str = _format_size(size_bytes)
        except OSError:
            size_str = "[dim]—[/]"

        table.add_row(str(i), path_str, tool, ts, size_str)

    console.print(table)
    if len(seen) > limit:
        console.print(
            f"[dim]Mostrando {limit} de {len(seen)} archivos únicos. "
            f"Usá /recent {limit * 2} para ver más.[/]"
        )
    console.print()


def _format_size(num_bytes: int) -> str:
    """Compact human-readable file size."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.1f} GB"
