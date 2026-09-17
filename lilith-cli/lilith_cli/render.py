"""Rich-based terminal renderer for Yggdrasil CLI.

Provides themed output helpers: markdown, streaming text, tool-call cards,
thinking panels, turn separators, welcome banners, and a theme system
with Norse / Cyberpunk / Minimal / Lilith presets.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Generator

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from lilith_cli import __version__


# ── Theme system ───────────────────────────────────────────────────


def register_spinner(name: str, frames: list[str], interval: int = 120) -> str:
    """Registra una animacion de spinner en el registro global de Rich.

    ``Spinner`` lanza ``KeyError`` con un nombre desconocido, asi que la clave se
    deriva del nombre del tema en vez de escribirse a mano. Registrar dos veces
    los mismos fotogramas no hace nada; cambiarlos sobreescribe el anterior.
    Ningun tema registra nada hasta que se lee su ``spinner_name``.

    Returns:
        La clave que hay que pasar a ``Spinner`` o a ``Status(spinner=...)``.
    """
    from rich.spinner import SPINNERS

    key = f"lilith-{name}"
    if SPINNERS.get(key, {}).get("frames") != list(frames):
        SPINNERS[key] = {"interval": interval, "frames": list(frames)}
    return key


class CLITheme:
    """A complete CLI theme definition.

    Attributes:
        name: Theme identifier (used in config and /theme command).
        label: Human-readable display name.
        description: One-line description shown in /theme list.
        theme: Rich ``Theme`` dict for console styling.
        banner: ASCII art string for the welcome banner.
        banner_title: Title inside the banner panel.
        banner_subtitle: Subtitle inside the banner panel.
        border_style: Rich style string for panel borders.
        rule_chars: Characters used in ``Rule`` separators.
        prompt_prefix: Unicode rune used as prompt prefix (᛭ by default).
        thinking_label: Label for thinking/reasoning panels.
        spinner_label: Label for the pre-stream spinner.
        spinner_frames: Custom spinner frames; ``None`` uses Rich's ``dots``.
        spinner_interval: Milliseconds between spinner frames.
        pt_style: prompt_toolkit style dict (for the input prompt).

    """

    def __init__(
        self,
        name: str,
        label: str,
        description: str,
        theme: dict[str, str],
        banner: str,
        banner_title: str = "",
        banner_subtitle: str = "",
        border_style: str = "gold1",
        rule_chars: str = "─",
        prompt_prefix: str = "᛭",
        thinking_label: str = "💭 Pensando...",
        spinner_label: str = "Pensando",
        spinner_frames: list[str] | None = None,
        spinner_interval: int = 120,
        pt_style: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.label = label
        self.description = description
        self.theme = theme
        self.banner = banner
        self.banner_title = banner_title
        self.banner_subtitle = banner_subtitle
        self.border_style = border_style
        self.rule_chars = rule_chars
        self.prompt_prefix = prompt_prefix
        self.thinking_label = thinking_label
        self.spinner_label = spinner_label
        self.spinner_frames = list(spinner_frames) if spinner_frames else None
        self.spinner_interval = spinner_interval
        self.pt_style = pt_style or {
            "": "#e0e0e0",
            "prompt": "#ffd700 bold",
            "prompt.dots": "#888888",
            "completion-menu": "bg:#1a1a2e #e0e0e0",
            "completion-menu.completion.current": "bg:#0f3460 #ffd700",
            "auto-suggestion": "#555555 italic",
        }

    @property
    def spinner_name(self) -> str:
        """Clave de spinner de Rich para este tema.

        Los temas sin fotogramas propios caen en el ``dots`` de Rich, asi que un
        tema que no declare nada se comporta exactamente como antes.
        """
        if not self.spinner_frames:
            return "dots"
        return register_spinner(self.name, self.spinner_frames, self.spinner_interval)


# ── Banner art ────────────────────────────────────────────────────
_NORSE_BANNER = f"""
        ᛭          ᛟ          ᛭
  ╔═════════════════════════════════╗
  ║        Y G G D R A S I L        ║
  ║{f'C L I · v{__version__}':^33}║
  ║   Where Ancient Meets Digital   ║
  ╚═════════════════════════════════╝
        ┃        ┃       ┃
   ─────┸────────┸───────┸──────
     Asgard  Midgard  Muspelheim
"""

_CYBERPUNK_BANNER = f"""
  ᛚ  L I L I T H   //   F A B R I C
  {f'coding agent · v{__version__}':<31}
  route · reason · act · verify
"""

_MINIMAL_BANNER = f"""
yggdrasil cli · v{__version__}
────────────────────
"""

_LILITH_BANNER = f"""
              ᛚ
        L I L I T H
    Taller de sagas · v{__version__}
       Memoria · Criterio · Obra
"""

_OBSIDIANA_BANNER = f"""
  ᛏ  L I L I T H
  v{__version__} · obsidiana · quien habla decide el color
  Oro es agencia · Hielo es hecho
"""


# ── Theme presets ──────────────────────────────────────────────────

THEMES: dict[str, CLITheme] = {
    "norse": CLITheme(
        name="norse",
        label="Norse",
        description="Dark-fantasy gold & runes — default theme",
        theme={
            "realm": "gold1",
            "frost": "deep_sky_blue3",
            "grove": "chartreuse3",
            "bark": "tan",
            "rune": "gold1",
            "error": "bold red",
            "success": "green",
            "warning": "yellow",
            "info": "cyan",
            "tool.name": "bold cyan",
            "tool.arg": "dim cyan",
            "tool.result": "green",
            "thinking": "dim italic magenta",
            "usage": "dim",
            "model": "bold gold1",
            "status.ok": "green",
            "status.fail": "red",
            "status.warn": "yellow",
            "turn": "dim gold1",
            "duration": "dim italic",
        },
        banner=_NORSE_BANNER,
        banner_title="[bold red]᛭ Yggdrasil Agent ᛭[/]",
        banner_subtitle="[dim]Where Ancient Meets Digital[/]",
        border_style="gold1",
        rule_chars="─",
        prompt_prefix="᛭",
        thinking_label="[dim]💭 Pensando...[/]",
        spinner_label="Pensando",
        spinner_frames=["ᚠ", "ᚢ", "ᚦ", "ᚨ", "ᚱ", "ᚲ", "ᚷ", "ᚹ"],
        spinner_interval=140,
        pt_style={
            "": "#e0e0e0",
            "prompt": "#ffd700 bold",
            "prompt.dots": "#888888",
            "completion-menu": "bg:#1a1a2e #e0e0e0",
            "completion-menu.completion.current": "bg:#0f3460 #ffd700",
            "auto-suggestion": "#555555 italic",
        },
    ),
    "cyberpunk": CLITheme(
        name="cyberpunk",
        label="Cyberpunk",
        description="Neon cyan & magenta — digital rain vibes",
        theme={
            "realm": "bright_magenta",
            "frost": "cyan",
            "grove": "bright_green",
            "bark": "grey50",
            "rune": "bright_cyan",
            "error": "bold bright_red",
            "success": "bright_green",
            "warning": "bright_yellow",
            "info": "bright_cyan",
            "tool.name": "bold bright_magenta",
            "tool.arg": "dim cyan",
            "tool.result": "bright_green",
            "thinking": "dim italic bright_magenta",
            "usage": "dim",
            "model": "bold bright_cyan",
            "status.ok": "bright_green",
            "status.fail": "bright_red",
            "status.warn": "bright_yellow",
            "turn": "dim cyan",
            "duration": "dim italic",
        },
        banner=_CYBERPUNK_BANNER,
        banner_title="[bold bright_magenta]ᛚ Lilith[/]",
        banner_subtitle="[dim bright_cyan]Yggdrasil Fabric online[/]",
        border_style="bright_magenta",
        rule_chars="═",
        prompt_prefix="⟐",
        thinking_label="[dim bright_magenta]⚡ Procesando...[/]",
        spinner_label="Procesando",
        spinner_frames=["░", "▒", "▓", "█", "▓", "▒"],
        pt_style={
            "": "#00ff9f",
            "prompt": "#ff00ff bold",
            "prompt.dots": "#555555",
            "completion-menu": "bg:#1a002e #00ff9f",
            "completion-menu.completion.current": "bg:#ff00ff #000000",
            "auto-suggestion": "#444444 italic",
        },
    ),
    "minimal": CLITheme(
        name="minimal",
        label="Minimal",
        description="Clean & quiet — no decorations, maximum readability",
        theme={
            "realm": "white",
            "frost": "blue",
            "grove": "green",
            "bark": "grey70",
            "rune": "white",
            "error": "red",
            "success": "green",
            "warning": "yellow",
            "info": "blue",
            "tool.name": "bold white",
            "tool.arg": "dim white",
            "tool.result": "green",
            "thinking": "dim italic",
            "usage": "dim",
            "model": "bold",
            "status.ok": "green",
            "status.fail": "red",
            "status.warn": "yellow",
            "turn": "dim",
            "duration": "dim italic",
        },
        banner=_MINIMAL_BANNER,
        banner_title="[bold]yggdrasil[/]",
        banner_subtitle="[dim]cli[/]",
        border_style="white",
        rule_chars="─",
        prompt_prefix="›",
        thinking_label="[dim]Thinking...[/]",
        spinner_label="Thinking",
        pt_style={
            "": "#cccccc",
            "prompt": "#ffffff",
            "prompt.dots": "#666666",
            "completion-menu": "bg:#222222 #cccccc",
            "completion-menu.completion.current": "bg:#444444 #ffffff",
            "auto-suggestion": "#555555 italic",
        },
    ),
    "lilith": CLITheme(
        name="lilith",
        label="Lilith",
        description="Obsidian, ice blue and aged gold — a Nordic personal workspace",
        theme={
            "realm": "#D5B96D",
            "frost": "#8FD8E8",
            "grove": "chartreuse3",
            "bark": "tan",
            "rune": "#D5B96D",
            "error": "bold red",
            "success": "green",
            "warning": "yellow",
            "info": "bright_cyan",
            "tool.name": "bold #8FD8E8",
            "tool.arg": "dim cyan",
            "tool.result": "green",
            "thinking": "dim italic magenta",
            "usage": "dim",
            "model": "bold #D5B96D",
            "status.ok": "green",
            "status.fail": "red",
            "status.warn": "yellow",
            "turn": "dim cyan",
            "duration": "dim italic",
        },
        banner=_LILITH_BANNER,
        banner_title="[bold #8FD8E8]Lilith[/]",
        banner_subtitle="[dim]Tu trabajo permanece. Retoma la saga.[/]",
        border_style="#D5B96D",
        rule_chars="─",
        prompt_prefix="᛭",
        thinking_label="[dim #8FD8E8]Pensando…[/]",
        spinner_label="Pensando",
        spinner_frames=["◇", "◈", "◆", "◈"],
        pt_style={
            "": "#dce3e8",
            "prompt": "#8fd8e8 bold",
            "prompt.dots": "#d5b96d",
            "completion-menu": "bg:#111820 #dce3e8",
            "completion-menu.completion.current": "bg:#263440 #8fd8e8",
            "auto-suggestion": "#85939d italic",
        },
    ),
    "obsidiana": CLITheme(
        name="obsidiana",
        label="Obsidiana",
        description="Obsidian, aged gold & ice — who speaks decides the color",
        theme={
            # ── Oro envejecido: agencia del operador ──────────
            "realm": "#D5B96D",       # identidad / encabezado → operador decide qué realm
            "rune": "#D5B96D",        # marca de turno → agencia humana
            "model": "bold #D5B96D",  # qué modelo eligió el operador → oro
            "turn": "#D5B96D",        # separador de turno → agencia
            # ── Azul hielo: hecho observado por la máquina ────
            "frost": "#8FD8E8",       # dato concreto → hecho
            "info": "#8FD8E8",        # información objetiva → hielo
            "tool.name": "bold #8FD8E8",   # qué herramienta usó la máquina
            "tool.arg": "#8FD8E8 dim",     # qué recibió la herramienta → hecho
            "tool.result": "#8FD8E8",      # qué devolvió la herramienta → hecho
            "thinking": "dim italic #8FD8E8",  # razonamiento de la máquina
            "usage": "dim #8FD8E8",   # recuento de tokens → dato medido
            "duration": "dim italic #8FD8E8",  # tiempo transcurrido → dato medido
            "status.ok": "#7FA858",   # la máquina confirma éxito → verde salvia
            # ── Rojo / ámbar: fallo y advertencia EXCLUSIVAMENTE
            "error": "bold #E05252",       # fallo → rojo apagado, legible sobre obsidiana
            "status.fail": "#E05252",      # fallo → rojo
            "warning": "#D4A84B",          # advertencia → ámbar (no oro, más cálido)
            "status.warn": "#D4A84B",      # advertencia → ámbar
            # ── Derivados: contraste sobre obsidiana ──────────
            "success": "#7FA858",     # verde salvia: victoria silenciosa
            "grove": "#7FA858",       # la arboleda → verde salvia
            "bark": "#8B8178",        # piedra/corteza → gris cálido
        },
        banner=_OBSIDIANA_BANNER,
        banner_title="[bold #D5B96D]ᛏ Lilith[/]",
        banner_subtitle="[dim #8FD8E8]Agencia · Hecho · Silencio[/]",
        border_style="#D5B96D",
        rule_chars="·",
        prompt_prefix="ᛏ",
        thinking_label="[dim #8FD8E8]Pensando…[/]",
        spinner_label="Pensando",
        spinner_frames=["ᚠ᛫", "᛫ᚢ", "ᚦ᛫", "᛫ᚨ", "ᚱ᛫", "᛫ᚲ"],
        spinner_interval=160,
        pt_style={
            "": "#c8c8c0",
            "prompt": "#D5B96D bold",
            "prompt.dots": "#8B8178",
            "completion-menu": "bg:#0D0D0F #c8c8c0",
            "completion-menu.completion.current": "bg:#1a1a24 #D5B96D",
            "auto-suggestion": "#555555 italic",
        },
    ),
}

# ── Active theme management ────────────────────────────────────────

_active_theme_name: str = "norse"
# Si ya apilamos un tema sobre el de base (ver set_theme).
_theme_pushed: bool = False


def get_theme() -> CLITheme:
    """Return the currently active CLI theme."""
    return THEMES.get(_active_theme_name, THEMES["norse"])


def set_theme(name: str) -> CLITheme:
    """Switch the active theme by name. Returns the new theme.

    Raises ``KeyError`` if the theme name is not found.

    Se MUTA el ``console`` existente en vez de reemplazarlo. Media docena de
    modulos hacen ``from .render import console``, que copia la referencia al
    importar: reasignar la global dejaba a todos ellos escribiendo por la
    Console vieja, asi que el tema nuevo no se aplicaba a la mayor parte de la
    salida (y en los tests el mock del console dejaba de recibir los prints).
    """
    global _active_theme_name, _theme_pushed
    if name not in THEMES:
        raise KeyError(name)
    theme_obj = THEMES[name]
    if _theme_pushed:
        # Solo hay un tema nuestro en la pila: se saca antes de apilar el
        # siguiente para que no crezca sin limite al cambiar varias veces.
        console.pop_theme()
    console.push_theme(Theme(theme_obj.theme))
    _theme_pushed = True
    _active_theme_name = name
    return theme_obj


def list_themes() -> list[CLITheme]:
    """Return all available themes in definition order."""
    return list(THEMES.values())


# ── Initialise console with default theme ──────────────────────────

YGGDRASIL_THEME = Theme(THEMES["norse"].theme)
console = Console(theme=YGGDRASIL_THEME)


def print_json(payload: Any, **dumps_kwargs: Any) -> None:
    """Emitir *payload* como JSON que se pueda parsear del otro lado.

    Los tres flags no son cosmeticos, son obligatorios:

    * ``soft_wrap`` — por defecto Rich envuelve al ancho de la consola, y al
      hacerlo mete un ``\\n`` DENTRO del JSON. En una terminal de 80 columnas
      la salida deja de parsear con "Invalid control character", que es justo
      lo contrario de lo que promete un flag ``--json``. Se detecto en CI,
      donde el runner tiene 80 columnas y dos tests de ``/now --json``
      reventaron; en la maquina de desarrollo no se veia porque la consola
      queda mas ancha que el payload.
    * ``markup`` — Rich interpreta los corchetes como etiquetas de estilo, y
      el JSON esta lleno de ``[`` y ``]``.
    * ``highlight`` — colorea numeros y strings, metiendo secuencias ANSI en
      lo que tiene que ser texto plano.

    Usar SIEMPRE esta funcion para salida legible por maquina, en vez de
    ``console.print(json.dumps(...))``; hay un test que lo verifica sobre el
    AST de todo el paquete.
    """
    dumps_kwargs.setdefault("ensure_ascii", False)
    console.print(
        json.dumps(payload, **dumps_kwargs),
        soft_wrap=True,
        markup=False,
        highlight=False,
    )

# ── Timer ───────────────────────────────────────────────────────────


class Timer:
    """Simple context-manager timer for tracking response duration."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        self.elapsed = time.perf_counter() - self._start

    @property
    def human(self) -> str:
        """Return human-readable duration."""
        if self.elapsed < 1:
            return f"{self.elapsed * 1000:.0f}ms"
        if self.elapsed < 60:
            return f"{self.elapsed:.1f}s"
        mins, secs = divmod(int(self.elapsed), 60)
        return f"{mins}m {secs}s"


# ── Welcome banner ──────────────────────────────────────────────────


def render_welcome(
    model: str = "",
    provider: str = "",
    tools_count: int = 0,
    has_memory: bool = False,
) -> None:
    """Show the welcome banner using the active theme."""
    theme = get_theme()
    # strip("\n") — a bare strip() would eat the first line's leading
    # spaces and break the ASCII-art alignment.
    lines = [line.rstrip() for line in theme.banner.strip("\n").splitlines()]
    banner_text = Text("\n".join(lines), style=f"bold {theme.border_style}")

    console.print()
    console.print(
        Panel(
            banner_text,
            title=theme.banner_title,
            subtitle=theme.banner_subtitle,
            border_style=theme.border_style,
            expand=False,
            padding=(0, 2),
        ),
    )

    # Session info line.
    info_parts: list[str] = []
    if model:
        info_parts.append(f"Modelo: [model]{model}[/]")
    if provider:
        info_parts.append(f"Proveedor: [model]{provider}[/]")
    if tools_count:
        info_parts.append(f"Herramientas: {tools_count}")
    mem_icon = "[status.ok]✓[/]" if has_memory else "[status.fail]✗[/]"
    info_parts.append(f"Memoria: {mem_icon}")

    console.print(f"[dim]{'  ·  '.join(info_parts)}[/]")
    console.print("[dim]Escribe [bold cyan]/help[/] para ver los comandos disponibles.[/]")
    console.print()


# ── Turn separators ─────────────────────────────────────────────────


def render_turn_start(turn: int) -> None:
    """Show a visual separator at the start of a new turn."""
    theme = get_theme()
    console.print()
    console.print(
        Rule(f"[turn]Turno {turn}[/]", style=theme.border_style, characters=theme.rule_chars),
    )
    console.print()


def render_user_separator(text: str) -> None:
    """Show a labeled separator for user input."""
    # Truncate long user messages for the label.
    label = text[:60] + "…" if len(text) > 60 else text
    label = label.replace("\n", " ")
    console.print()
    console.print(Rule(f"[dim]▸ Tú[/]  [turn]{label}[/]", style="dim", characters="·"))
    console.print()


def render_assistant_separator() -> None:
    """Show a labeled separator before the assistant's response."""
    theme = get_theme()
    console.print(Rule("[dim]◂ Lilith[/]", style=theme.border_style, characters=theme.rule_chars))
    console.print()


def render_turn_end(duration: float, usage: dict[str, int] | None = None) -> None:
    """Show turn summary: duration + token usage."""
    parts: list[str] = []
    if duration > 0:
        if duration < 1:
            parts.append(f"[duration]{duration * 1000:.0f}ms[/]")
        elif duration < 60:
            parts.append(f"[duration]{duration:.1f}s[/]")
        else:
            mins, secs = divmod(int(duration), 60)
            parts.append(f"[duration]{mins}m {secs}s[/]")

    if usage and any(v > 0 for v in usage.values()):
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        total = usage.get("total_tokens", 0)
        parts.append(f"[usage]{prompt}↑ {completion}↓ {total}Σ[/]")

    if parts:
        console.print(f"[dim]{'  ·  '.join(parts)}[/]")


# ── Markdown ────────────────────────────────────────────────────────


def render_markdown(text: str) -> None:
    """Render *text* as Markdown to the terminal."""
    console.print(Markdown(text))


class TailView:
    """Renderable that shows only the last *tail_lines* lines of another
    renderable.

    Keeps a ``Live`` frame bounded: if a live frame grows taller than the
    terminal, Rich re-prints the overflow on every refresh and the output
    duplicates (the "texto repetido" bug in cmd.exe). Streaming views wrap
    their content in this so the frame never exceeds the screen; the full
    content gets printed once when the stream closes.
    """

    def __init__(self, renderable: Any, tail_lines: int = 12) -> None:
        self.renderable = renderable
        self.tail_lines = tail_lines

    def __rich_console__(self, console_: Any, options: Any) -> Any:
        from rich.segment import Segment

        lines = console_.render_lines(self.renderable, options, pad=False)
        if len(lines) > self.tail_lines:
            yield Text("…", style="dim")
            lines = lines[-self.tail_lines :]
        for line in lines:
            yield from line
            yield Segment.line()


def build_stream_tail(text: str, tail_lines: int = 12) -> TailView:
    """Live view of a streaming Markdown response, bounded to the last
    *tail_lines* rendered lines."""
    return TailView(Markdown(text), tail_lines=tail_lines)


# ── Error ───────────────────────────────────────────────────────────


def render_error(text: str) -> None:
    """Show an error message in bold red."""
    console.print(f"[error]✗ {text}[/]")


# ── Thinking / reasoning ────────────────────────────────────────────


def build_thinking_panel(text: str, *, tail_lines: int | None = None) -> Text:
    """Build the thinking/reasoning text renderable for the active theme.

    With *tail_lines*, only the last N lines are shown — used by the live
    streaming view so a long reasoning block doesn't fill the screen.
    """
    display = text
    if tail_lines is not None:
        lines = display.splitlines()
        if len(lines) > tail_lines:
            display = "…\n" + "\n".join(lines[-tail_lines:])
    return Text(display, style="thinking")


def render_thinking(text: str) -> None:
    """Show a thinking / reasoning text using the active theme."""
    console.print(Text(text, style="thinking"))


# ── Tool call cards ─────────────────────────────────────────────────


def render_tool_call(name: str, args: dict[str, Any], result: str | None = None) -> None:
    """Show a tool execution card with name, args, and optional result."""
    # Build the header.
    header = Text()
    header.append("⟡ ", style="bold cyan")
    header.append(name, style="tool.name")

    # Args block.
    args_lines: list[str] = []
    for k, v in args.items():
        v_display = v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v
        args_lines.append(f"  {k}: {v_display!r}")
    args_text = "\n".join(args_lines) if args_lines else "  (sin argumentos)"

    body_parts: list[Any] = []
    if args_lines:
        body_parts.append(Syntax(args_text, "python", theme="monokai", line_numbers=False))

    if result is not None:
        # Truncate overly long results.
        display_result = result if len(result) <= 500 else result[:500] + "…"
        body_parts.append(Text())
        body_parts.append(Text("↳ Resultado:", style="tool.result"))
        body_parts.append(Text(display_result))

    renderable = Group(*body_parts) if body_parts else Text(args_text, style="tool.arg")
    console.print(
        Panel(renderable, title=header, border_style="tool.name", expand=False, padding=(0, 1)),
    )


# ── Diff Renderer ───────────────────────────────────────────────────


def render_diff(diff_text: str, path: str | None = None) -> Any:
    """Render a unified diff with a Nordic frame."""
    import re
    from rich.text import Text
    from rich.console import Group

    try:
        diff_text = str(diff_text) if diff_text is not None else ""
        if "@@" not in diff_text and "---" not in diff_text:
            return escape(diff_text)

        lines = diff_text.splitlines()
        adds = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
        subs = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))

        hunks = []
        current_hunk = None
        for line in lines:
            if line.startswith("---") or line.startswith("+++") or line == "\\ No newline at end of file":
                continue
            if line.startswith("@@"):
                if current_hunk is not None:
                    hunks.append(current_hunk)
                current_hunk = []
            if current_hunk is not None:
                current_hunk.append(line)
        if current_hunk is not None:
            hunks.append(current_hunk)

        omitted_lines = 0
        if len(hunks) > 4:
            middle_hunks = hunks[2:-2]
            omitted_lines = sum(len(h) for h in middle_hunks)
            hunks = hunks[:2] + hunks[-2:]

        theme = get_theme()
        border_style = theme.border_style

        parts = []
        
        header = Text()
        header.append("╭── ", style=border_style)
        if path:
            header.append(path, style="tool.name")
            header.append(" ── ", style=border_style)
        header.append(f"+{adds}", style="success")
        header.append(" ", style=border_style)
        # El signo menos va pegado al numero. Antes aqui habia un separador
        # "───" entre las dos cifras y ningun signo, asi que la cabecera salia
        # "+4 ───1": el recuento de borrados quedaba sin signo y el separador se
        # leia como parte del numero. Un recuento sin signo no es un recuento.
        header.append(f"−{subs}", style="error")
        parts.append(header)

        for h_idx, hunk in enumerate(hunks):
            if omitted_lines > 0 and h_idx == 2:
                t = Text()
                t.append("│  ", style=border_style)
                t.append(f"... {omitted_lines} líneas omitidas ...", style="dim")
                parts.append(t)
                
            old_ln = 0
            new_ln = 0
            for line in hunk:
                if line.startswith("@@"):
                    m = re.search(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                    if m:
                        old_ln = int(m.group(1))
                        new_ln = int(m.group(2))
                    
                    t = Text()
                    t.append("│ ", style=border_style)
                    t.append(" " * 11)
                    t.append(line, style="turn")
                    parts.append(t)
                else:
                    # Dos columnas: viejo y nuevo. Antes se mostraba una sola y
                    # las lineas borradas ponian ahi su numero del fichero
                    # VIEJO, asi que la numeracion saltaba hacia atras (33 +,
                    # luego 32 -) y el ojo tropezaba en cada borrado. Con las
                    # dos columnas cada numero dice de que fichero habla, y la
                    # que no aplica queda en blanco: una linea anadida no existe
                    # en el viejo y una borrada no existe en el nuevo.
                    if line.startswith("+"):
                        col_vieja, col_nueva = "", str(new_ln)
                        new_ln += 1
                        sign = "+"
                        code = line[1:]
                        style = "success"
                    elif line.startswith("-"):
                        col_vieja, col_nueva = str(old_ln), ""
                        old_ln += 1
                        sign = "-"
                        code = line[1:]
                        style = "error"
                    else:
                        col_vieja, col_nueva = str(old_ln), str(new_ln)
                        old_ln += 1
                        new_ln += 1
                        sign = " "
                        code = line[1:] if line else ""
                        style = "default"

                    t = Text()
                    t.append("│ ", style=border_style)
                    t.append(f"{col_vieja:>4} ", style="dim")
                    t.append(f"{col_nueva:>4} ", style="dim")
                    t.append(f"{sign} ", style=style)
                    t.append(code, style=style)
                    parts.append(t)
                    
        return Group(*parts)
    except Exception:
        return escape(str(diff_text)) if diff_text is not None else ""


def _grep_matches(data: Any) -> tuple[list[Any], str | None]:
    """Normaliza el resultado de grep_files y devuelve (coincidencias, aviso).

    Historia, porque el cambio de forma es una trampa: hasta el 2026-09-15
    `grep_files` devolvia la lista pelada de coincidencias. Desde entonces
    devuelve un dict, porque un recorrido truncado necesita un sitio donde
    DECIRLO y una lista no lo tiene.

    Se aceptan las dos formas a proposito. Un consumidor que solo entendiera el
    dict dejaria de pintar la tabla sin lanzar ninguna excepcion, y un fallo que
    no grita es el que tarda semanas en verse.
    """
    if isinstance(data, dict):
        matches = data.get("matches")
        aviso = data.get("warning")
        return (matches if isinstance(matches, list) else []), (aviso if isinstance(aviso, str) else None)
    if isinstance(data, list):
        return data, None
    return [], None


def summarize_tool_result(name: str, content: str, args: dict[str, Any] | None = None) -> str:
    """Devuelve un resumen de una línea del resultado de una herramienta."""
    args = args or {}
    content = str(content) if content is not None else ""
    data = _extract_data(content)
    try:
        if name == "file_read":
            from pathlib import Path
            path_str = args.get("path", "")
            filename = Path(path_str).name if path_str else "archivo"
            lines = content.splitlines()
            num_lines = len(lines)
            if content.startswith("Líneas ") or content.startswith("Lineas "):
                first = lines[0].rstrip(":")
                return f"{filename} — {first.lower()}"
            return f"{filename} — {num_lines} líneas de {num_lines}"
        elif name == "grep_files":
            matches, aviso = _grep_matches(data)
            if matches or isinstance(data, (list, dict)):
                n_matches = len(matches)
                n_files = len(set(m.get("file") for m in matches if isinstance(m, dict)))
                if n_files <= 1:
                    resumen = f"{n_matches} coincidencia{'s' if n_matches != 1 else ''}"
                else:
                    resumen = f"{n_matches} coincidencias en {n_files} ficheros"
                # Un cero truncado NO se resume como un cero. Ese silencio es lo
                # que hizo concluir que algo no existia cuando si existia.
                return f"{resumen} (INCOMPLETA)" if aviso else resumen
        elif name == "directory_list":
            if isinstance(data, list):
                n = len(data)
                return f"{n} entrada{'s' if n != 1 else ''}"
        elif name == "batch_edit":
            if isinstance(data, dict):
                edits = data.get("edits", [])
                if edits:
                    n_edits = len(edits)
                    n_files = len(set(e.get("path") for e in edits if isinstance(e, dict)))
                    return f"{n_edits} {'edición' if n_edits == 1 else 'ediciones'}, {n_files} {'fichero' if n_files == 1 else 'ficheros'}"
                else:
                    return "batch_edit completado"
        elif name == "file_write" or name == "file_edit":
            if isinstance(data, dict):
                from pathlib import Path
                path_str = data.get("path", "")
                filename = Path(path_str).name if path_str else "archivo"
                return f"{name} en {filename}"
        elif name == "coding":
            if isinstance(data, dict) and "returncode" in data:
                return f"exit {data['returncode']}"
        elif name == "todo_list":
            if isinstance(data, dict) and "todos" in data:
                todos = data["todos"]
                if isinstance(todos, list):
                    total = len(todos)
                    if total == 0:
                        return "0 tareas"
                    done = sum(1 for t in todos if isinstance(t, dict) and t.get("done"))
                    pend = total - done
                    return (f"{total} tarea{'s' if total != 1 else ''} "
                            f"({pend} pendiente{'s' if pend != 1 else ''})")
        elif name == "bg_status":
            if isinstance(data, dict):
                if "processes" in data and isinstance(data["processes"], list):
                    procs = data["processes"]
                    total = len(procs)
                    alive = sum(1 for p in procs if isinstance(p, dict) and p.get("alive"))
                    # Concordancia, igual que en todo_list: "1 procesos vivos"
                    # se lee mal, y estas lineas las mira el operador en cada turno.
                    plural = alive != 1
                    return (f"{alive} proceso{'s' if plural else ''} "
                            f"viv{'os' if plural else 'o'} de {total}")
                elif "name" in data and "status" in data:
                    alive_str = "vivo" if data.get("alive") else "muerto"
                    return f"proceso {data['name']}: {alive_str}"
        elif name == "cli_jobs_recent":
            if isinstance(data, dict) and "references" in data:
                refs = data["references"]
                if isinstance(refs, list):
                    n = len(refs)
                    return f"{n} delegacion{'es' if n != 1 else 'ón'} reciente{'s' if n != 1 else ''}"
        elif name == "watch_status":
            if isinstance(data, dict) and "watches" in data:
                watches = data["watches"]
                if isinstance(watches, list):
                    total = len(watches)
                    if total == 0:
                        return "0 watchers"
                    events = sum(w.get("event_count", 0) for w in watches if isinstance(w, dict))
                    return f"{total} watchers activos ({events} eventos)"
        elif name in ("cli_job_reference", "cli_job_inspect"):
            if isinstance(data, dict):
                status = data.get("status", "unknown")
                if status == "unresolved_reference":
                    return "referencia no resuelta"
                agent = data.get("agent", "?")
                job_id = data.get("job_id", "?")
                if data.get("job_returncode") is not None:
                    return f"{agent} {job_id} — completado con {data['job_returncode']}"
                return f"{agent} {job_id} — {status}"

        # Generic structured fallback: prefer the field that proves the result
        # contains something useful, rather than displaying JSON's opening.
        if isinstance(data, dict) and data:
            for key in ("processes", "todos", "references", "watches", "results", "edits", "events"):
                value = data.get(key)
                if isinstance(value, list):
                    return f"{len(value)} {key}"
            for key in ("count", "total"):
                if key in data and isinstance(data[key], (int, float)) and not isinstance(data[key], bool):
                    return f"{key}: {data[key]}"
            if "status" in data and not any(isinstance(value, list) for value in data.values()):
                return f"status: {data['status']}"
            lists = {key: value for key, value in data.items() if isinstance(value, list)}
            if lists:
                key, value = max(lists.items(), key=lambda item: len(item[1]))
                return f"{len(value)} {key}"
            n_keys = len(data)
            return f"objeto con {n_keys} clave{'s' if n_keys != 1 else ''}"
        if isinstance(data, list):
            return f"lista de {len(data)} elemento{'s' if len(data) != 1 else ''}"
        if data is None:
            return "(sin salida)"
        if data is not content:
            return f"valor estructurado: {data}"
    except Exception:
        # Rendering is best-effort: malformed or surprising tool output must
        # never take down the turn that is trying to display it.
        pass

    try:
        for line in content.splitlines():
            line = line.strip()
            if line:
                return line[:60] + ("…" if len(line) > 60 else "")
    except Exception:
        pass
    return "(sin salida)"


def render_tool_line(name: str, summary: str, duration: float | None = None) -> None:
    """Renderiza una línea de resumen de herramienta en columnas."""
    from rich.text import Text
    theme = get_theme()
    
    t = Text()
    t.append(f"{theme.prompt_prefix} ", style="rune")
    
    # Columna de nombre (alineada)
    t.append(f"{name:<14} ", style="tool.name")
    
    # Columna de resumen
    t.append(f"{summary:<40} ", style="tool.arg")
    
    if duration is not None:
        if duration < 1:
            dur_str = f"{duration * 1000:.0f}ms"
        elif duration < 60:
            dur_str = f"{duration:.1f}s"
        else:
            mins, secs = divmod(int(duration), 60)
            dur_str = f"{mins}m {secs}s"
        t.append(f"{dur_str:>8}", style="duration")
        
    console.print(t)


# ── Tool result rendering helpers ─────────────────────────────────────


def _extract_data(content: str) -> Any:
    """Try to parse a tool result as JSON; return raw string on failure."""
    try:
        return json.loads(content)
    except Exception:
        return content


# Rich NO acepta un modificador junto a un estilo NOMBRADO del tema:
# "bold tool.name" lo parsea como `bold` + color `tool.name`, y revienta con
# MissingStyle en cuanto se pinta la tabla. Paso el 2026-09-15 y tumbaba
# directory_list y grep_files, o sea casi cada turno. El nombre va SOLO; si hace
# falta negrita, se mete en la definicion del estilo dentro del tema (tool.name
# ya es "bold #8FD8E8" en obsidiana, asi que la negrita ya estaba).


def render_tool_result(name: str, content: str) -> Any:
    """Return a Rich renderable for a tool result based on the tool name.

    - file_read → Syntax-highlighted code block with line numbers.
    - directory_list → Rich Table with Name / Type / Size (JSON list starting with ``[``).
    - grep_files → Rich Table with File / Line / Match (JSON list starting with ``[``).
    - coding → stdout/stderr blocks with green / red styling.
    - default → raw content string.

    The agent can call this helper when it wants a nicer result display.
    """
    if name == "file_read":
        return Syntax(content, "python", theme="monokai", line_numbers=True)

    if name == "directory_list" and content.lstrip().startswith("["):
        data = _extract_data(content)
        table = Table(
            show_header=True,
            header_style="tool.name",
            border_style="tool.name",
            expand=False,
        )
        table.add_column("Nombre", style="tool.name")
        table.add_column("Tipo", style="tool.arg")
        table.add_column("Tamaño", justify="right", style="tool.result")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    size = item.get("size")
                    size_str = f"{size} B" if isinstance(size, int) else "-"
                    table.add_row(
                        str(item.get("name", "-")),
                        str(item.get("type", "-")),
                        size_str,
                    )
        return table

    if name == "grep_files" and content.lstrip()[:1] in ("[", "{"):
        data = _extract_data(content)
        matches, aviso = _grep_matches(data)
        table = Table(
            show_header=True,
            header_style="tool.name",
            border_style="tool.name",
            expand=False,
        )
        table.add_column("Archivo", style="tool.name")
        table.add_column("Línea", justify="right", style="tool.arg")
        table.add_column("Coincidencia", style="tool.result")
        for item in matches:
            if isinstance(item, dict):
                table.add_row(
                    str(item.get("file", "-")),
                    str(item.get("line_number", "-")),
                    item.get("line_text", "-"),
                )
        if aviso:
            # El aviso va pegado a la tabla, no en otra linea que se pueda perder.
            table.caption = aviso
            table.caption_style = "error"
        return table

    if name == "coding":
        data = _extract_data(content)
        parts: list[Any] = [Text("↳ Resultado de ejecución:", style="bold cyan")]
        if isinstance(data, dict):
            stdout = data.get("stdout", "")
            stderr = data.get("stderr", "")
            returncode = data.get("returncode")
            if stdout:
                parts.append(Text("stdout", style="bold green"))
                parts.append(Syntax(stdout, "text", theme="monokai", line_numbers=False))
            if stderr:
                parts.append(Text("stderr", style="bold red"))
                parts.append(Syntax(stderr, "text", theme="monokai", line_numbers=False))
            if returncode is not None:
                parts.append(Text(f"returncode: {returncode}", style="dim"))
        else:
            parts.append(Text(content))
        return Group(*parts)

    return escape(content)


# ── Status panel ─────────────────────────────────────────────────────


def render_status(status_dict: dict[str, Any]) -> None:
    """Show a status panel with realm health, model info, etc."""
    theme = get_theme()
    table = Table(
        show_header=True,
        header_style=f"bold {theme.border_style}",
        border_style=theme.border_style,
        expand=False,
    )
    table.add_column("Reino / Propiedad", style="realm", min_width=20)
    table.add_column("Estado", min_width=16)

    for key, value in status_dict.items():
        if isinstance(value, bool):
            status_str = "[status.ok]✓ ACTIVO[/]" if value else "[status.fail]✗ INACTIVO[/]"
            table.add_row(key, status_str)
        elif isinstance(value, dict):
            sub = ", ".join(f"{k}={v}" for k, v in value.items())
            table.add_row(key, sub)
        else:
            table.add_row(key, str(value))

    console.print(
        Panel(
            table,
            title="[bold realm]⚔ Status Report ⚔[/]",
            border_style=theme.border_style,
            expand=False,
        ),
    )


# ── Token usage ─────────────────────────────────────────────────────


def render_token_usage(usage: dict[str, int]) -> None:
    """Show token usage in dim text after a response."""
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0)
    console.print(
        f"[usage]Tokens — prompt: {prompt} · completion: {completion} · total: {total}[/]",
    )


def render_cost(
    total_usage: dict[str, int],
    per_model_usage: dict[str, dict[str, Any]],
    current_model: str,
    total_cost: float,
) -> None:
    """Render session cost with a per-model breakdown table and next-1K estimate.

    *per_model_usage* is a dict of ``model_name -> {prompt_tokens, completion_tokens,
    total_tokens, cost}``. If more than one model has been used, a Rich table
    lists each model with its token counts and cost. The estimate for the next
    1K tokens is based on the current model's pricing.
    """
    from .providers import estimate_cost

    render_token_usage(total_usage)
    console.print(f"[info]Costo total estimado:[/] [model]${total_cost:.4f} USD[/]")

    if len(per_model_usage) > 1:
        table = Table(
            title="[bold realm]Desglose por modelo[/]",
            show_header=True,
            header_style="bold",
            border_style="usage",
            expand=False,
        )
        table.add_column("Modelo", style="model")
        table.add_column("Prompt", justify="right")
        table.add_column("Completion", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Costo", justify="right")

        for model, stats in sorted(per_model_usage.items()):
            table.add_row(
                model,
                str(stats.get("prompt_tokens", 0)),
                str(stats.get("completion_tokens", 0)),
                str(stats.get("total_tokens", 0)),
                f"${stats.get('cost', 0.0):.4f}",
            )
        console.print(table)
    elif len(per_model_usage) == 1:
        model = next(iter(per_model_usage))
        console.print(f"[info]Modelo:[/] [model]{model}[/]")

    next_1k = estimate_cost(current_model, 1000, 1000)
    if next_1k > 0:
        console.print(f"[info]Estimado para 1K tokens:[/] [model]${next_1k:.4f} USD[/]")
    else:
        console.print("[info]Estimado para 1K tokens:[/] [dim]no disponible para este modelo[/]")


# ── Plan renderer ────────────────────────────────────────────────────


# ── Context usage ───────────────────────────────────────────────────


def render_context(session: Any, *, full: bool = False) -> None:
    """Render context usage as a Rich progress bar.

    Reads ``session._total_usage``, ``session.history``,
    ``session.config.model`` and, when *full=True*, also displays the
    breakdown of the system prompt, tools schema and history sizes.
    """
    from .providers import estimate_context_window

    theme = get_theme()
    model = getattr(session.config, "model", "unknown")
    max_tokens = estimate_context_window(model)

    usage = getattr(session, "_total_usage", {}) or {}
    used = usage.get("total_tokens", 0) or usage.get("prompt_tokens", 0)
    percentage = min(used / max_tokens, 1.0) if max_tokens else 0.0

    history = getattr(session, "history", []) or []
    msg_count = len(history)
    tool_count = sum(len(msg.get("tool_calls", [])) for msg in history)

    bar_width = 30
    filled = int(bar_width * percentage)
    empty = bar_width - filled
    bar = "█" * filled + "░" * empty
    color = "green" if percentage < 0.5 else "yellow" if percentage < 0.8 else "red"

    pct_text = f"[{color}]{bar}[/]  {percentage * 100:.1f}%"
    info = (
        f"[info]Usados:[/] [model]{used:,}[/] / [model]{max_tokens:,}[/] tokens · "
        f"[info]Mensajes:[/] [model]{msg_count}[/] · "
        f"[info]Llamadas a herramientas:[/] [model]{tool_count}[/] · "
        f"[info]Modelo:[/] [model]{model}[/]"
    )

    if not full:
        console.print(pct_text)
        console.print(info)
        return

    # Full breakdown: estimate sizes from the session state.
    system_prompt = getattr(session, "system_prompt", "") or ""
    system_size = len(system_prompt.split()) if isinstance(system_prompt, str) else 0

    tools_cache = getattr(session, "_tools_cache", None) or []
    tools_text = json.dumps(tools_cache, separators=(",", ":")) if tools_cache else ""
    tools_size = len(tools_text.split()) if tools_text else 0

    history_size = used - system_size - tools_size
    if history_size < 0:
        history_size = used

    plan = getattr(session, "current_plan", None)
    plan_summary = ""
    if plan is not None:
        done = sum(1 for s in getattr(plan, "steps", []) if getattr(s, "done", False))
        total = len(getattr(plan, "steps", []))
        plan_summary = f" · [info]Plan:[/] [model]{done}/{total}[/]"

    table = Table(
        title="[bold realm]⚔ Uso de Contexto ⚔[/]",
        show_header=False,
        border_style=theme.border_style,
        expand=False,
    )
    table.add_column("Concepto", style="realm")
    table.add_column("Tokens", justify="right", style="model")

    table.add_row("Prompt del sistema", f"{system_size:,}")
    table.add_row("Descripción de herramientas", f"{tools_size:,}")
    table.add_row("Historial de mensajes", f"{history_size:,}")
    table.add_row("Total usado", f"{used:,}")
    table.add_row("Ventana de contexto", f"{max_tokens:,}")

    console.print(Panel(table, border_style=theme.border_style, expand=False))
    console.print(pct_text)
    console.print(f"{info}{plan_summary}")


# ── Plan renderer ────────────────────────────────────────────────────


def render_plan(plan: Any) -> None:
    """Render an ``AgentPlan`` as a Rich checklist.

    Each step is shown with a checkbox (``✓`` for done, ``·`` for
    pending) and a small progress line at the bottom summarises completion.
    """
    from rich.table import Table as _Table

    from rich.panel import Panel as _Panel

    steps = list(getattr(plan, "steps", []))
    if not steps:
        console.print("[dim](plan vacío)[/]")
        return

    done = sum(1 for s in steps if getattr(s, "done", False))
    total = len(steps)

    table = _Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=False,
    )
    table.add_column(width=3, justify="center")
    table.add_column(width=3, justify="right")
    table.add_column()

    for step in steps:
        if getattr(step, "done", False):
            mark = "[green]✓[/]"
            desc = f"[dim]{step.description}[/]"
        else:
            mark = "[cyan]·[/]"
            desc = step.description
        table.add_row(
            mark,
            f"{step.number}.",
            desc,
        )

    progress = f"[muted]{done}/{total} completados[/]"
    console.print(table)
    console.print(f"\n{progress}")


# ── Streaming context manager ────────────────────────────────────────


@contextmanager
def render_streaming() -> Generator[dict[str, Any], None, None]:
    """Context manager that provides a live-updating Rich panel for
    streaming LLM output.

    Usage::

        with render_streaming() as state:
            for chunk in provider.stream(messages):
                state["text"] += chunk["content"]
                state["live"].update(...)

    The returned dict has keys:
      ``text`` — accumulated text so far,
      ``live``  — the Rich ``Live`` instance (already started).
    """
    state: dict[str, Any] = {"text": "", "usage": {}}

    console.print()  # blank line before streaming output
    with Live(console=console, refresh_per_second=12, vertical_overflow="visible") as live:
        state["live"] = live
        try:
            yield state
        finally:
            # Final render.
            if state["text"]:
                console.print()


# ── Thinking spinner (pre-stream) ──────────────────────────────────


def make_thinking_spinner() -> dict[str, Any]:
    """Create a Rich ``Status`` context manager for the pre-stream spinning
    indicator.  Themed according to the active CLITheme.
    """
    import threading

    from rich.status import Status

    theme = get_theme()
    prefix = theme.prompt_prefix

    label = Text()
    label.append(f"{prefix} ", style=f"bold {theme.border_style}")
    label.append(theme.spinner_label, style="italic cyan")
    label.append("…", style="dim")

    status = Status(
        label,
        spinner=theme.spinner_name,
        console=console,
        speed=0.8,
    )

    stop_event = threading.Event()

    def stop() -> None:
        """Signal the spinner to stop."""
        stop_event.set()

    def set_label(new_text: str) -> None:
        """Update the spinner label text."""
        label = Text()
        label.append(f"{prefix} ", style=f"bold {theme.border_style}")
        label.append(new_text, style="italic cyan")
        label.append("…", style="dim")
        status.update(label)

    return {
        "status": status,
        "stop": stop,
        "set_label": set_label,
    }


# ── Git status / Todos renderers (v4.3.1) ──────────────────────────


def render_git_status(status_dict: dict) -> None:
    """Render a git status dict as a Rich table with columns: file, status, branch.

    Expects ``status_dict`` with keys: branch (str), files (list of
    {path, status}). Status codes: M=modified, A=added, D=deleted,
    ??=untracked, etc.
    """
    from rich.table import Table as _GT

    branch = status_dict.get("branch", "(unknown)")
    files = status_dict.get("files", []) or []

    table = _GT(
        title=f"[bold]⚔ Git status — branch {branch} ⚔[/]",
        show_header=True,
        header_style="bold yellow",
        border_style="turn",
    )
    table.add_column("File", style="frost", overflow="fold")
    table.add_column("Status", width=10, justify="center")

    status_colors = {
        "M": "[yellow]M[/]",
        "A": "[green]A[/]",
        "D": "[red]D[/]",
        "??": "[cyan]??[/]",
        "R": "[blue]R[/]",
    }
    for f in files:
        path = f.get("path", "?") if isinstance(f, dict) else str(f)
        st = f.get("status", "??") if isinstance(f, dict) else "??"
        mark = status_colors.get(st, f"[dim]{st}[/]")
        table.add_row(path, mark)

    if not files:
        table.add_row("[dim](working tree clean)[/]", "")
    console.print(table)
    console.print()


def render_todos(todos: list) -> None:
    """Render a list of todo dicts as a Rich checklist.

    Each todo can be a dict with ``text`` and ``done`` keys, or an
    object with ``.text`` and ``.done`` attributes. Renders a compact
    list with ✓ / · markers and progress at the bottom.
    """
    from rich.table import Table as _TT

    if not todos:
        console.print("[dim](sin tareas)[/]")
        return

    table = _TT(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=False,
    )
    table.add_column(width=3, justify="center")
    table.add_column(width=4, justify="right")
    table.add_column()

    done = 0
    for i, todo in enumerate(todos, start=1):
        if isinstance(todo, dict):
            text = todo.get("text", "")
            is_done = todo.get("done", False)
        else:
            text = getattr(todo, "text", "")
            is_done = getattr(todo, "done", False)

        if is_done:
            done += 1
            mark = "[green]✓[/]"
            desc = f"[dim strike]{text}[/]"
        else:
            mark = "[cyan]·[/]"
            desc = text
        table.add_row(mark, f"{i}.", desc)

    progress = f"[muted]{done}/{len(todos)} completados[/]"
    console.print(table)
    console.print(f"\n{progress}")


# ── Code review renderer ────────────────────────────────────────────


def render_review(review: dict[str, Any]) -> None:
    """Render a code review result as a Rich panel with color-coded issues.

    *review* is expected to contain:
      - ``target``: the reviewed file / diff / commit
      - ``summary``: dict with counts for style, bugs, performance, security
      - ``issues``: list of dicts with keys category, severity, file, line, message
      - ``suggestions``: list of improvement suggestion strings
    """
    theme = get_theme()
    target = review.get("target", "")
    issues = review.get("issues", [])
    summary = review.get("summary", {})
    suggestions = review.get("suggestions", [])

    title = f"[bold realm]᛭ Code Review: {target}[/]"

    if not issues:
        console.print(
            Panel(
                "[success]✓ No se encontraron issues.[/]",
                title=title,
                border_style="success",
                expand=False,
                padding=(0, 2),
            )
        )
        return

    summary_parts = [
        f"[warning]Style:[/] {summary.get('style', 0)}",
        f"[error]Bugs:[/] {summary.get('bugs', 0)}",
        f"[info]Performance:[/] {summary.get('performance', 0)}",
        f"[status.warn]Security:[/] {summary.get('security', 0)}",
    ]
    console.print(
        Panel(
            "\n".join(summary_parts),
            title=title,
            border_style=theme.border_style,
            expand=False,
            padding=(0, 2),
        )
    )

    table = Table(
        show_header=True,
        header_style="tool.result",
        border_style="tool.result",
        expand=False,
    )
    table.add_column("Sev", width=6)
    table.add_column("Cat", width=12)
    table.add_column("Línea", justify="right", width=6)
    table.add_column("Archivo", style="frost")
    table.add_column("Mensaje", style="tool.result")

    severity_style = {
        "high": "[error]Alta[/]",
        "medium": "[warning]Media[/]",
        "low": "[info]Baja[/]",
    }
    category_style = {
        "style": "[warning]Style[/]",
        "bugs": "[error]Bugs[/]",
        "performance": "[info]Performance[/]",
        "security": "[status.warn]Security[/]",
    }

    for issue in issues:
        sev = issue.get("severity", "low")
        cat = issue.get("category", "style")
        table.add_row(
            severity_style.get(sev, sev),
            category_style.get(cat, cat),
            str(issue.get("line", "-")),
            issue.get("file", "-"),
            issue.get("message", ""),
        )

    console.print(table)

    if suggestions:
        suggestion_text = "\n".join(f"[frost]• {s}[/]" for s in suggestions)
        console.print(
            Panel(
                suggestion_text,
                title="[bold grove]Sugerencias de mejora[/]",
                border_style="grove",
                expand=False,
                padding=(0, 2),
            )
        )

    console.print()
