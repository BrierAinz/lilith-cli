"""Rich-based terminal renderer for Yggdrasil CLI v6.5.

Provides themed output helpers: markdown, streaming text, tool-call cards,
thinking panels, turn separators, welcome banners, and a theme system
with Norse / Cyberpunk / Minimal presets.
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


# ── Theme system ───────────────────────────────────────────────────


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
        self.pt_style = pt_style or {
            "": "#e0e0e0",
            "prompt": "#ffd700 bold",
            "prompt.dots": "#888888",
            "completion-menu": "bg:#1a1a2e #e0e0e0",
            "completion-menu.completion.current": "bg:#0f3460 #ffd700",
            "auto-suggestion": "#555555 italic",
        }


# ── Banner art ────────────────────────────────────────────────────

_NORSE_BANNER = r"""
        ᛭          ᛟ          ᛭
  ╔═════════════════════════════════╗
  ║        Y G G D R A S I L        ║
  ║          C L I · v6.5           ║
  ║   Where Ancient Meets Digital   ║
  ╚═════════════════════════════════╝
        ┃        ┃       ┃
   ─────┸────────┸───────┸──────
     Asgard  Midgard  Muspelheim
"""

_CYBERPUNK_BANNER = r"""
  ╔═════════════════════════════════╗
  ║     ⟐  Y G G D R A S I L  ⟐     ║
  ║          C L I · v6.5           ║
  ║   Signals From The Edge Nodes   ║
  ╚═════════════════════════════════╝
          ╠══╦══╦══╦══╦══╣
          ║▓▓║▒▒║░░║▒▒║▓▓║
          ╚══╩══╩══╩══╩══╝
"""

_MINIMAL_BANNER = r"""
yggdrasil cli · v6.5
────────────────────
"""

_LILITH_BANNER = r"""
             ☾  ✦  ☽
     L I L I T H  ·  C L I
             v6.5
  Demon of Information terminal
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
        banner_title="[bold bright_magenta]⟐ Yggdrasil  CLI ⟐[/]",
        banner_subtitle="[dim bright_cyan]Signals From The Edge Nodes[/]",
        border_style="bright_magenta",
        rule_chars="═",
        prompt_prefix="⟐",
        thinking_label="[dim bright_magenta]⚡ Procesando...[/]",
        spinner_label="Procesando",
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
        description="Lilith CLI theme — Demon of Information assistant terminal",
        theme={
            "realm": "bright_magenta",
            "frost": "deep_sky_blue3",
            "grove": "chartreuse3",
            "bark": "tan",
            "rune": "bright_magenta",
            "error": "bold red",
            "success": "green",
            "warning": "yellow",
            "info": "bright_cyan",
            "tool.name": "bold bright_magenta",
            "tool.arg": "dim cyan",
            "tool.result": "green",
            "thinking": "dim italic magenta",
            "usage": "dim",
            "model": "bold bright_magenta",
            "status.ok": "green",
            "status.fail": "red",
            "status.warn": "yellow",
            "turn": "dim cyan",
            "duration": "dim italic",
        },
        banner=_LILITH_BANNER,
        banner_title="[bold bright_magenta]᛭ Lilith CLI ᛭[/]",
        banner_subtitle="[dim italic]Demon of Information — assistant terminal[/]",
        border_style="bright_magenta",
        rule_chars="─",
        prompt_prefix="᛭",
        thinking_label="[dim bright_magenta]✨ Manifesting...[/]",
        spinner_label="Manifesting",
        pt_style={
            "": "#ff00ff",
            "prompt": "#ff66ff bold",
            "prompt.dots": "#660066",
            "completion-menu": "bg:#1a002e #ff66ff",
            "completion-menu.completion.current": "bg:#ff00ff #000000",
            "auto-suggestion": "#666666 italic",
        },
    ),
}

# ── Active theme management ────────────────────────────────────────

_active_theme_name: str = "norse"


def get_theme() -> CLITheme:
    """Return the currently active CLI theme."""
    return THEMES.get(_active_theme_name, THEMES["norse"])


def set_theme(name: str) -> CLITheme:
    """Switch the active theme by name. Returns the new theme.

    Raises ``KeyError`` if the theme name is not found.
    """
    global _active_theme_name, console
    if name not in THEMES:
        raise KeyError(name)
    _active_theme_name = name
    theme_obj = THEMES[name]
    # Recreate the global Console with the new Rich Theme.
    console = Console(theme=Theme(theme_obj.theme))
    return theme_obj


def list_themes() -> list[CLITheme]:
    """Return all available themes in definition order."""
    return list(THEMES.values())


# ── Initialise console with default theme ──────────────────────────

YGGDRASIL_THEME = Theme(THEMES["norse"].theme)
console = Console(theme=YGGDRASIL_THEME)

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


def build_thinking_panel(text: str, *, tail_lines: int | None = None) -> Panel:
    """Build the thinking/reasoning panel renderable for the active theme.

    With *tail_lines*, only the last N lines are shown — used by the live
    streaming view so a long reasoning block doesn't fill the screen.
    """
    theme = get_theme()
    # Truncate very long thinking blocks.
    display = text if len(text) <= 1000 else text[:1000] + "…"
    if tail_lines is not None:
        lines = display.splitlines()
        if len(lines) > tail_lines:
            display = "…\n" + "\n".join(lines[-tail_lines:])
    return Panel(
        Text(display, style="thinking"),
        title=theme.thinking_label,
        border_style=theme.border_style,
        expand=False,
        padding=(0, 1),
    )


def render_thinking(text: str) -> None:
    """Show a thinking / reasoning panel using the active theme."""
    console.print(build_thinking_panel(text))


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
        Panel(renderable, title=header, border_style="cyan", expand=False, padding=(0, 1)),
    )


# ── Tool result rendering helpers ─────────────────────────────────────


def _extract_data(content: str) -> Any:
    """Try to parse a tool result as JSON; return raw string on failure."""
    try:
        return json.loads(content)
    except Exception:
        return content


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
            header_style="bold cyan",
            border_style="cyan",
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

    if name == "grep_files" and content.lstrip().startswith("["):
        data = _extract_data(content)
        table = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="cyan",
            expand=False,
        )
        table.add_column("Archivo", style="tool.name")
        table.add_column("Línea", justify="right", style="tool.arg")
        table.add_column("Coincidencia", style="tool.result")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    table.add_row(
                        str(item.get("file", "-")),
                        str(item.get("line_number", "-")),
                        item.get("line_text", "-"),
                    )
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
            border_style="dim",
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
        spinner="dots",
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
        border_style="dim cyan",
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
                border_style="green",
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
        header_style="bold cyan",
        border_style="cyan",
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
