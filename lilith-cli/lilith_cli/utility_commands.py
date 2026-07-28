"""Comandos utilitarios de un solo paso: texto, numeros, fechas y azar.

Son los comandos que no tocan la sesion ni el repositorio y no comparten
estado con nadie: `/now`, `/hash`, `/lines`, `/base64`, `/uuid`, `/reverse`,
`/calc`, `/epoch`, `/random` y `/quote`.

Vivian dentro de `extra_commands.py`, un modulo de 12.000 lineas donde
cualquier `old_string` de una o dos lineas colisiona cientos de veces y la
herramienta `patch` ya dejo el archivo sin compilar mas de una vez. Se
extrajeron aca por ser un grupo autocontenido: no comparten un solo helper
con los comandos que quedaron.

`extra_commands` los reexporta, asi que los imports existentes siguen
funcionando sin cambios.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import math
import operator
import re
import secrets
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from .agent import AgentSession


def _resolve_message_by_index(session: AgentSession, index: int) -> dict[str, Any] | None:
    """Resolve a 1-based index against the session history (most recent = 1)."""
    history = getattr(session, "history", None) or []
    if not history:
        return None
    if index < 1 or index > len(history):
        return None
    return history[-index]


def _resolve_last_assistant_message(session: AgentSession) -> dict[str, Any] | None:
    """Return the most recent assistant message in the history, if any."""
    history = getattr(session, "history", None) or []
    for msg in reversed(history):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return msg
    return None


async def run_now_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Show current timestamps (/now [--utc|--local|--unix|--iso|--rfc|--json]).

    Examples:
        /now
        /now --utc
        /now --unix --iso
        /now --rfc
        /now --json
        /now --unix --iso --json
    """
    from datetime import datetime, timezone

    tokens = args.split()
    show_unix = "--unix" in tokens
    show_utc = "--utc" in tokens
    show_iso = "--iso" in tokens
    show_rfc = "--rfc" in tokens
    as_json = "--json" in tokens
    explicit_local = "--local" in tokens
    # Si ningún flag específico se pasa, mostramos local como antes.
    any_specific = show_unix or show_utc or show_iso or show_rfc or as_json
    show_local = explicit_local or not any_specific

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()

    # Construimos el payload (siempre las 4 formas en UTC + local), y solo
    # emitimos Rich si no se pidio --json. Esto mantiene el modo
    # machine-readable estable para scripts y pipelines.
    payload: dict[str, object] = {
        "unix": int(now_utc.timestamp()),
        "utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "iso": now_utc.isoformat().replace("+00:00", "Z"),
        "rfc": _now_rfc_value(now_utc),
        "local": now_local.strftime("%Y-%m-%d %H:%M:%S %Z (%z)"),
    }

    if as_json:
        import json as _json
        # ``--json`` reemplaza la salida Rich: una sola linea, parseable.
        console.print(_json.dumps(payload, ensure_ascii=False, sort_keys=True))
        console.print()
        return

    if show_local:
        console.print(f"[info]Local:[/info]  [bold cyan]{payload['local']}[/bold cyan]")
    if show_utc:
        console.print(f"[info]UTC:[/info]    [bold cyan]{payload['utc']}[/bold cyan]")
    if show_unix:
        console.print(f"[info]Unix:[/info]   [bold cyan]{payload['unix']}[/bold cyan]")
    if show_iso:
        # ISO 8601 — el formato universal para logs, APIs y versionado.
        console.print(f"[info]ISO:[/info]    [bold cyan]{payload['iso']}[/bold cyan]")
    if show_rfc:
        # RFC 2822 — formato de email / HTTP, útil para tickets y logs de correo.
        console.print(f"[info]RFC:[/info]    [bold cyan]{payload['rfc']}[/bold cyan]")
    console.print()


def _now_rfc_value(now_utc) -> str:
    """Format an aware UTC datetime as RFC 2822, falling back gracefully.

    ``email.utils.format_datetime`` es la implementacion canonica; si por
    algun motivo no estuviera disponible (ejecutable reducido de Python)
    caemos a ``strftime`` con el formato RFC 2822 manual.
    """
    try:
        from email.utils import format_datetime as _fmt_rfc
        return _fmt_rfc(now_utc)
    except ImportError:  # pragma: no cover — email.utils es stdlib siempre disponible.
        return now_utc.strftime("%a, %d %b %Y %H:%M:%S +0000")


async def run_hash_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Compute hashes of text or file (/hash <algo> <text|file>)."""
    import hashlib

    text = args.strip()
    if not text:
        render_error("Uso: /hash <md5|sha1|sha256|sha512> <texto|ruta_archivo>")
        return

    parts = text.split(maxsplit=1)
    algo = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    supported = ("md5", "sha1", "sha256", "sha512")
    if algo not in supported:
        render_error(f"Algoritmo no soportado: {algo}. Use: {', '.join(supported)}")
        return

    if not target:
        render_error("Uso: /hash <algo> <texto|ruta_archivo>")
        return

    # Detect file vs text: try as path first, fall back to literal text
    target_path = Path(target).expanduser()
    if target_path.is_file():
        try:
            data_bytes = target_path.read_bytes()
            source = f"archivo: {target_path}"
        except OSError as exc:
            render_error(f"No se pudo leer {target_path}: {exc}")
            return
    else:
        data_bytes = target.encode("utf-8")
        source = f"texto ({len(data_bytes)} chars)"

    h = hashlib.new(algo)
    h.update(data_bytes)
    digest = h.hexdigest()
    console.print(f"[info]{algo}[/info] [bold cyan]{digest}[/bold cyan]  [dim]({source})[/dim]")
    console.print()


async def run_lines_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Count lines/words/chars of a file (/lines <path>)."""
    text = args.strip()
    if not text:
        render_error("Uso: /lines <archivo>")
        return

    target = Path(text).expanduser()
    if not target.exists():
        render_error(f"No existe: {target}")
        return
    if not target.is_file():
        render_error(f"No es un archivo: {target}")
        return

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        render_error(f"No se pudo leer {target}: {exc}")
        return

    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    words = len(content.split())
    chars = len(content)
    bytes_size = target.stat().st_size
    console.print(f"[info]Líneas:[/info] [bold cyan]{lines}[/bold cyan]  [dim]palabras: {words}  chars: {chars}  bytes: {bytes_size}[/dim]  [dim]({target.name})[/dim]")
    console.print()


async def run_base64_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Base64 encode or decode text (/base64 <encode|decode> <text>)."""
    import base64

    text = args.strip()
    if not text:
        render_error("Uso: /base64 <encode|decode> <texto>")
        return

    parts = text.split(maxsplit=1)
    op = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    if op not in ("encode", "decode"):
        render_error("Uso: /base64 <encode|decode> <texto>")
        return

    if not target:
        render_error(f"Uso: /base64 {op} <texto>")
        return

    try:
        if op == "encode":
            encoded = base64.b64encode(target.encode("utf-8")).decode("ascii")
            console.print(f"[info]encoded:[/info] [bold cyan]{encoded}[/bold cyan]")
        else:
            try:
                decoded = base64.b64decode(target, validate=True).decode("utf-8")
                console.print(f"[info]decoded:[/info] [bold cyan]{decoded}[/bold cyan]")
            except Exception as exc:
                render_error(f"Base64 inválido: {exc}")
                return
    except Exception as exc:
        render_error(f"Error: {exc}")
        return
    console.print()


async def run_uuid_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Generate UUIDs (/uuid [N] [--v1|--v4|--v7])."""
    import uuid

    tokens = args.split()
    count = 1
    version = 4

    for tok in tokens:
        if tok.isdigit():
            count = max(1, min(int(tok), 50))
        elif tok == "--v1":
            version = 1
        elif tok == "--v4":
            version = 4
        elif tok == "--v7":
            version = 7

    if count == 1:
        if version == 1:
            new_id = uuid.uuid1()
        elif version == 7:
            new_id = uuid.uuid7()
        else:
            new_id = uuid.uuid4()
        console.print(f"[bold cyan]{new_id}[/bold cyan]  [dim](v{version})[/dim]")
    else:
        console.print(f"[info]{count} UUIDs (v{version}):[/info]")
        for _ in range(count):
            if version == 1:
                new_id = uuid.uuid1()
            elif version == 7:
                new_id = uuid.uuid7()
            else:
                new_id = uuid.uuid4()
            console.print(f"  [bold cyan]{new_id}[/bold cyan]")
    console.print()


async def run_reverse_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Reverse a string or list lines (/reverse [--lines] <text>)."""
    text = args.strip()
    if not text:
        render_error("Uso: /reverse [--lines] <texto>")
        return

    lines_mode = False
    if text.startswith("--lines "):
        lines_mode = True
        text = text[len("--lines "):].strip()
    elif text == "--lines":
        render_error("Uso: /reverse --lines <texto>")
        return

    if lines_mode:
        lines = text.split("\n")
        reversed_lines = list(reversed(lines))
        result = "\n".join(reversed_lines)
        console.print(f"[info]Líneas invertidas ({len(lines)}):[/info]")
    else:
        result = text[::-1]
        console.print(f"[info]Reverso:[/info]")

    console.print(f"[bold cyan]{result}[/bold cyan]")
    console.print()


# Operadores binarios permitidos (suma, resta, multiplicación, división real,
# división entera, módulo y potencia). Se evalúan con funciones explícitas en
# lugar de ``eval`` para que el comando sea seguro ante entrada arbitraria.
_CALC_BINOPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


# Operadores unarios permitidos (signo positivo/negativo).
_CALC_UNARYOPS: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


# Constantes matemáticas reconocidas por nombre.
_CALC_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


# Funciones matemáticas permitidas (de un argumento, salvo las marcadas).
_CALC_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "min": min,
    "max": max,
}


def _calc_eval_node(node: ast.AST) -> float:
    """Evalúa recursivamente un nodo del AST de una expresión aritmética.

    Solo acepta números, constantes conocidas, operadores binarios/unarios
    permitidos y llamadas a funciones matemáticas de la lista blanca. Cualquier
    otra construcción (nombres desconocidos, atributos, índices, etc.) lanza
    ``ValueError`` para que el llamador muestre un error amigable.
    """
    if isinstance(node, ast.Expression):
        return _calc_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"literal no soportado: {node.value!r}")
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _CALC_CONSTANTS:
            return _CALC_CONSTANTS[node.id]
        raise ValueError(f"nombre desconocido: {node.id!r}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _CALC_BINOPS:
            raise ValueError(f"operador no soportado: {op_type.__name__}")
        left = _calc_eval_node(node.left)
        right = _calc_eval_node(node.right)
        return _CALC_BINOPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _CALC_UNARYOPS:
            raise ValueError(f"operador unario no soportado: {op_type.__name__}")
        return _CALC_UNARYOPS[op_type](_calc_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _CALC_FUNCTIONS:
            raise ValueError("función no permitida")
        if node.keywords:
            raise ValueError("argumentos por nombre no soportados")
        func = _CALC_FUNCTIONS[node.func.id]
        args = [_calc_eval_node(a) for a in node.args]
        return func(*args)
    raise ValueError(f"expresión no soportada: {type(node).__name__}")


def calc_eval(expr: str) -> float:
    """Evalúa una expresión aritmética de forma segura (sin ``eval``).

    Acepta números, ``+ - * / // % **``, paréntesis, las constantes
    ``pi``, ``e``, ``tau`` y funciones como ``sqrt``, ``sin``, ``log``,
    ``min``, ``max``, ``abs`` y ``round``.

    Raises:
        ValueError: si la expresión es inválida o usa construcciones no
            permitidas.
        ArithmeticError: ante errores aritméticos (división por cero, etc.).
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"sintaxis inválida: {expr!r}") from exc
    return _calc_eval_node(tree)


async def run_calc_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /calc para evaluar expresiones aritméticas de forma segura.

    Examples:
        /calc 2 + 3 * 4
        /calc sqrt(2) ** 2
        /calc (10 % 3) + pi
    """
    expr = args.strip()
    if not expr:
        render_error("Uso: /calc <expresión> — por ejemplo /calc 2 + 3 * 4")
        return

    try:
        result = calc_eval(expr)
    except (ValueError, ArithmeticError) as exc:
        render_error(f"No pude evaluar la expresión: {exc}")
        return

    # Formateo amigable: los enteros se muestran sin parte decimal.
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        rendered = str(int(result))
    else:
        rendered = str(result)

    console.print(f"[tool.name]{expr}[/] = [bold cyan]{rendered}[/]")


async def run_epoch_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Convierte entre timestamps Unix y fechas legibles (/epoch [ts | YYYY-MM-DD[ HH:MM:SS]] | now).

    Examples:
        /epoch                     — timestamp Unix actual
        /epoch now                 — idem (explícito)
        /epoch 1700000000          — convierte a fecha UTC y local
        /epoch 2024-01-15          — convierte la fecha a timestamp (medianoche local)
        /epoch 2024-01-15 08:30:00 --utc — interpreta la fecha como UTC
    """
    from datetime import datetime, timezone

    text = args.strip()
    utc_mode = "--utc" in text.split()
    text = text.replace("--utc", "").strip()

    if not text or text.lower() == "now":
        ts = int(datetime.now(timezone.utc).timestamp())
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        dt_local = datetime.fromtimestamp(ts).astimezone()
        console.print(f"[info]Unix:[/info]   [bold cyan]{ts}[/bold cyan]")
        console.print(f"[info]UTC:[/info]    [bold cyan]{dt_utc.strftime('%Y-%m-%d %H:%M:%S')}[/bold cyan]")
        console.print(f"[info]Local:[/info]  [bold cyan]{dt_local.strftime('%Y-%m-%d %H:%M:%S %Z (%z)')}[/bold cyan]")
        return

    # Caso 1: timestamp Unix → fechas legibles.
    if text.isdigit():
        try:
            ts = int(text)
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OverflowError, OSError) as exc:
            render_error(f"Timestamp fuera de rango: {text} ({exc})")
            return
        dt_local = datetime.fromtimestamp(ts).astimezone()
        console.print(f"[info]Unix:[/info]   [bold cyan]{ts}[/bold cyan]")
        console.print(f"[info]UTC:[/info]    [bold cyan]{dt_utc.strftime('%Y-%m-%d %H:%M:%S')}[/bold cyan]")
        console.print(f"[info]Local:[/info]  [bold cyan]{dt_local.strftime('%Y-%m-%d %H:%M:%S %Z (%z)')}[/bold cyan]")
        return

    # Caso 2: fecha legible → timestamp Unix.
    dt: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        render_error(f"Fecha no reconocida: {text!r}. Formato: YYYY-MM-DD[ HH:MM[:SS]]")
        return

    if utc_mode:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone()  # interpreta como hora local
    console.print(f"[info]Unix:[/info]   [bold cyan]{int(dt.timestamp())}[/bold cyan]")
    console.print(f"[dim](interpretado como {'UTC' if utc_mode else 'hora local'})[/dim]")


def _render_random_usage() -> None:
    """Muestra la ayuda concisa de /random."""
    console.print("[bold cyan]/random[/bold cyan] — valores aleatorios seguros")
    console.print("  /random [int [mínimo máximo]]")
    console.print("  /random choice <opción1> <opción2> [...]  [dim]# acepta comillas[/dim]")
    console.print("  /random hex [bytes]  [dim]# 1..1024; por defecto 16[/dim]")
    console.print("  /random uuid")
    console.print("  /random coin")
    console.print("  /random dice [NdM]  [dim]# N: 1..100; M: 2..1000000[/dim]")


async def run_random_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Comando /random: genera valores aleatorios criptográficamente seguros."""
    import secrets
    import uuid

    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    subcommand = tokens[0].lower() if tokens else "int"

    if subcommand == "int":
        if len(tokens) == 1:
            minimum, maximum = 1, 100
        elif len(tokens) == 3:
            try:
                minimum, maximum = int(tokens[1]), int(tokens[2])
            except ValueError:
                render_error("Los límites de /random int deben ser enteros.")
                return
            if minimum > maximum:
                render_error("El mínimo no puede ser mayor que el máximo.")
                return
        else:
            render_error("Uso: /random int [mínimo máximo]")
            return
        value = minimum + secrets.randbelow(maximum - minimum + 1)
        console.print(f"[bold cyan]{value}[/bold cyan]")
        return

    if subcommand == "choice":
        choices = tokens[1:]
        if len(choices) < 2:
            render_error("Uso: /random choice <opción1> <opción2> [...] (mínimo 2)")
            return
        console.print(f"[bold cyan]{secrets.choice(choices)}[/bold cyan]")
        return

    if subcommand == "hex":
        if len(tokens) > 2:
            render_error("Uso: /random hex [bytes]")
            return
        try:
            byte_count = int(tokens[1]) if len(tokens) == 2 else 16
        except ValueError:
            render_error("La cantidad de bytes debe ser un entero entre 1 y 1024.")
            return
        if not 1 <= byte_count <= 1024:
            render_error("La cantidad de bytes debe estar entre 1 y 1024.")
            return
        console.print(f"[bold cyan]{secrets.token_hex(byte_count)}[/bold cyan]")
        return

    if subcommand == "uuid" and len(tokens) == 1:
        console.print(f"[bold cyan]{uuid.uuid4()}[/bold cyan]")
        return

    if subcommand == "coin" and len(tokens) == 1:
        console.print(f"[bold cyan]{secrets.choice(('cara', 'cruz'))}[/bold cyan]")
        return

    if subcommand == "dice":
        if len(tokens) > 2:
            render_error("Uso: /random dice [NdM]")
            return
        notation = tokens[1] if len(tokens) == 2 else "1d6"
        match = re.fullmatch(r"(\d+)[dD](\d+)", notation)
        if match is None:
            render_error("Notación de dados inválida. Usa NdM, por ejemplo 2d6.")
            return
        count, sides = (int(value) for value in match.groups())
        if not 1 <= count <= 100 or not 2 <= sides <= 1_000_000:
            render_error("Dados fuera de rango: N debe ser 1..100 y M 2..1000000.")
            return
        rolls = [1 + secrets.randbelow(sides) for _ in range(count)]
        rendered_rolls = ", ".join(str(roll) for roll in rolls)
        console.print(f"[info]Tiradas:[/info] [bold cyan]{rendered_rolls}[/bold cyan]")
        console.print(f"[info]Total:[/info] [bold cyan]{sum(rolls)}[/bold cyan]")
        return

    _render_random_usage()


def _quote_usage() -> str:
    """Devuelve la ayuda de uso de /quote en español."""
    return (
        "Uso: /quote <last|user|N>\n"
        "  /quote          — cita el último mensaje del asistente.\n"
        "  /quote last     — cita el último mensaje del asistente.\n"
        "  /quote user     — cita el último mensaje del usuario.\n"
        "  /quote N        — cita el N-ésimo mensaje más reciente (1 = último).\n"
        "  /quote help     — muestra esta ayuda."
    )


def _extract_text_from_message(message: dict[str, Any]) -> str:
    """Extrae únicamente el texto visible de un mensaje del historial."""
    content = message.get("content") if "content" in message else message.get("text")
    if content is None:
        return ""
    if isinstance(content, list):
        text_blocks: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_blocks.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if text is not None:
                    text_blocks.append(str(text))
        return "\n".join(text_blocks)
    if content == "":
        return ""
    return str(content)


def _resolve_quote_target(
    session: AgentSession,
    target: str,
) -> tuple[dict[str, Any] | None, int | None]:
    """Resuelve el mensaje objetivo y su índice relativo (1 = el último)."""
    history = getattr(session, "history", None) or []
    if target in ("", "last"):
        message = _resolve_last_assistant_message(session)
        if message is None:
            return None, None
    elif target == "user":
        message = None
        for position in range(len(history) - 1, -1, -1):
            candidate = history[position]
            if isinstance(candidate, dict) and candidate.get("role") == "user":
                message = candidate
                break
        if message is None:
            return None, None
    else:
        try:
            index = int(target)
        except ValueError:
            return None, None
        message = _resolve_message_by_index(session, index)
        if message is None:
            return None, index

    for position in range(len(history) - 1, -1, -1):
        if history[position] is message:
            return message, len(history) - position
    return message, None


async def run_quote_command(session: AgentSession, args: str) -> None:
    """Cita un mensaje del historial como texto plano y copiable."""
    usage_args = args.strip()
    if usage_args.lower() in {"help", "--help", "-h", "?"}:
        console.print(_quote_usage(), markup=False, highlight=False)
        return

    tokens = usage_args.split()
    if len(tokens) > 1:
        render_error(_quote_usage())
        return
    target = tokens[0].lower() if tokens else "last"
    if target not in {"last", "user"}:
        try:
            index = int(target)
        except ValueError:
            render_error(_quote_usage())
            return
        if index < 1:
            render_error(_quote_usage())
            return

    history = getattr(session, "history", None) or []
    if not history:
        render_error("No hay conversación todavía — `/quote` necesita al menos un mensaje.")
        return

    message, index = _resolve_quote_target(session, target)
    if message is None:
        if target == "user":
            render_error("No hay mensajes del usuario para citar.")
        elif target == "last":
            render_error("No hay mensajes del asistente para citar.")
        else:
            render_error(f"Índice fuera de rango: {target} (la sesión tiene {len(history)} mensajes).")
        return

    if index is None:
        index = 1
    text = _extract_text_from_message(message)
    if not text:
        render_error(f"El mensaje #{index} no tiene texto visible para citar.")
        return
    console.print(text, markup=False, highlight=False)
