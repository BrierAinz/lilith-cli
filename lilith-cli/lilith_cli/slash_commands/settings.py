"""Settings slash commands: /theme, /config, /agent, /auto, /status, /profile, /model-info, /json-mode, /hooks, /stream, /alias, /editor and /macro."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import CONFIG_DIR
from ..json_store import preserve_corrupt
from ..render import console, get_theme, render_error, set_theme
from ._shared import _get_editor, _set_editor

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime


async def run_macro_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /macro [record|stop|play|list|stats|show|edit|copy|rename|import|validate|delete].

    Las macros se guardan en ~/.yggdrasil/macros.json como secuencias de
    comandos de barra. El REPL se encarga de almacenar los comandos mientras
    se graba; esta función simplemente delega a MacroCommand para las
    operaciones manuales de control/playback.
    """
    # Visual status indicator BEFORE delegating (record/stop show a clear
    # panel; play/list/delete are unchanged). The actual recording / playback
    # is still performed by MacroCommand.
    text = args.strip()
    if text:
        parts = text.split(maxsplit=1)
        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if subcmd == "record":
            _render_macro_status("record", rest.strip())
        elif subcmd == "stop":
            _render_macro_status("stop")

    from ..commands import MacroCommand

    cmd = MacroCommand(session)
    await cmd.execute(args)

# ── Alias helpers ────────────────────────────────────────────────────

_ALIAS_FILE = CONFIG_DIR / "aliases.json"


async def run_alias_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /alias para crear atajos de comandos de barra.

    Examples:
        /alias
        /alias set <nombre> <comando>
        /alias remove <nombre>
    """
    text = args.strip()

    aliases: dict[str, str] = _load_aliases()

    if not text or text.lower() in ("list", "ls"):
        if not aliases:
            console.print("[dim]No hay alias definidos.[/]")
            return
        console.print("\n[bold realm]᛭ Alias definidos[/]")
        for name, cmd in sorted(aliases.items()):
            console.print(f"  [bold cyan]/{name}[/] → {cmd}")
        console.print()
        return

    parts = text.split(maxsplit=2)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("set", "add"):
        if len(parts) < 3:
            render_error("Uso: /alias set <nombre> <comando>")
            return
        name, cmd = parts[1], parts[2]
        aliases[name] = cmd
        _save_aliases(aliases)
        console.print(f"[success]✓ Alias guardado: [bold cyan]/{name}[/] → {cmd}[/]")
        return

    if subcmd in ("get", "show"):
        if not rest:
            render_error("Uso: /alias get <nombre>")
            return
        cmd = aliases.get(rest)
        if cmd is None:
            render_error(f"Alias no encontrado: {rest}")
            return
        console.print(f"[bold cyan]/{rest}[/] \u2192 {cmd}")
        return

    if subcmd in ("remove", "rm", "delete"):
        if not rest:
            render_error("Uso: /alias remove <nombre>")
            return
        if rest not in aliases:
            render_error(f"Alias no encontrado: {rest}")
            return
        del aliases[rest]
        _save_aliases(aliases)
        console.print(f"[success]✓ Alias eliminado: {rest}[/]")
        return

    render_error("Uso: /alias [set|get|remove|list]")


def _load_aliases() -> dict[str, str]:
    """Carga alias desde ~/.yggdrasil/aliases.json."""
    if not _ALIAS_FILE.exists():
        return {}
    try:
        data = json.loads(_ALIAS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("se esperaba un dict JSON")
    except Exception as exc:
        preserve_corrupt(_ALIAS_FILE, exc)
    return {}


def _save_aliases(aliases: dict[str, str]) -> None:
    """Guarda alias en ~/.yggdrasil/aliases.json."""
    _ALIAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ALIAS_FILE.write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Stream helpers ──────────────────────────────────────────────────

_STREAM_CONFIG_FILE = CONFIG_DIR / "stream_config.json"


async def run_stream_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /stream para mostrar o cambiar opciones de streaming.

    Examples:
        /stream
        /stream on
        /stream off
    """
    text = args.strip()
    if not text or text.lower() in ("show", "status"):
        mode = _load_stream_config().get("enabled", True)
        console.print(f"[info]Streaming: [model]{'activado' if mode else 'desactivado'}[/]")
        return

    if text.lower() in ("on", "true", "1"):
        _save_stream_config({"enabled": True})
        console.print("[success]✓ Streaming activado.[/]")
    elif text.lower() in ("off", "false", "0"):
        _save_stream_config({"enabled": False})
        console.print("[success]✓ Streaming desactivado.[/]")
    else:
        render_error("Uso: /stream [on|off]")


def _load_stream_config() -> dict[str, Any]:
    """Carga configuración de streaming."""
    if not _STREAM_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(_STREAM_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("se esperaba un dict JSON")
    except Exception as exc:
        preserve_corrupt(_STREAM_CONFIG_FILE, exc)
    return {}


def _save_stream_config(config: dict[str, Any]) -> None:
    """Guarda configuración de streaming."""
    _STREAM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STREAM_CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── /auto command ────────────────────────────────────────────────────


async def run_auto_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /auto [on|off] para activar el modo auto (ejecución automática de herramientas).

    Examples:
        /auto
        /auto on
        /auto off
    """
    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        enabled = getattr(session, "auto_mode", False)
        console.print(f"[info]Modo auto: [model]{'activado' if enabled else 'desactivado'}[/]")
        return

    if text.lower() in ("on", "true", "1"):
        session.auto_mode = True
        console.print("[success]✓ Modo auto activado.[/]")
    elif text.lower() in ("off", "false", "0"):
        session.auto_mode = False
        console.print("[success]✓ Modo auto desactivado.[/]")
    else:
        render_error("Uso: /auto [on|off]")


# ── /load command ───────────────────────────────────────────────────


# ── /theme command ──────────────────────────────────────────────────


async def run_theme_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /theme [name|current|preview <name>] para cambiar o inspeccionar el tema.

    Examples:
        /theme                      — listar temas disponibles
        /theme cyberpunk            — cambiar al tema
        /theme current              — mostrar el tema activo + atributos
        /theme preview cyberpunk    — muestra cómo se ve un tema sin aplicarlo
        /theme list                 — alias explícito de listar
    """
    from ..render import get_theme, list_themes, set_theme

    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        themes = list_themes()
        console.print("\n[bold realm]᛭ Temas disponibles[/]")
        for theme in themes:
            console.print(f"  [bold cyan]{theme.name}[/] — {theme.description}")
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "current":
        active = get_theme()
        console.print(
            f"\n[bold realm]᛭ Tema activo[/] "
            f"[bold cyan]{active.name}[/]\n"
        )
        console.print(f"  [info]Label:[/]            [bold]{active.label}[/]")
        console.print(f"  [info]Prefijo prompt:[/]    [bold]{active.prompt_prefix}[/]")
        console.print(f"  [info]Bordes:[/]           [bold {active.border_style}]{active.border_style}[/]")
        console.print(f"  [info]Descripción:[/]      {active.description}")
        console.print()
        return

    if subcmd == "preview":
        if not rest:
            render_error("Uso: /theme preview <nombre>")
            return
        from ..render import THEMES as _themes_dict
        target_name = rest.strip().lower()
        if target_name not in _themes_dict:
            render_error(
                f"Tema desconocido: {rest}. Usá /theme list para ver los disponibles."
            )
            return
        target = _themes_dict[target_name]

        # ── Render a truthful preview WITHOUT calling set_theme ──
        # We temporarily push the target theme onto the console, render
        # sample output using the REAL renderers, then pop it back.
        # This never touches _active_theme_name, so get_theme() still
        # returns the user's actual theme.
        from rich.theme import Theme as _RichTheme

        console.push_theme(_RichTheme(target.theme))
        try:
            console.print(
                f"\n[bold realm]᛭ Preview de '{target_name}' (sin aplicar)[/]\n"
            )
            # 1. Banner
            from rich.text import Text as _Text
            lines = [l.rstrip() for l in target.banner.strip("\n").splitlines()]
            console.print(_Text("\n".join(lines), style=f"bold {target.border_style}"))
            console.print()

            # 2. Tool line
            from ..render import render_tool_line
            render_tool_line("file_read", "render.py — 42 líneas", duration=0.23)

            # 3. Diff
            from ..render import render_diff
            sample_diff = (
                "--- render.py\n+++ render.py\n"
                "@@ -10,3 +10,3 @@\n"
                " def render():\n"
                '-    return "old"\n'
                '+    return "new"\n'
            )
            console.print(render_diff(sample_diff, "render.py"))

            # 4. Error
            from ..render import render_error as _render_err
            _render_err("Ejemplo de error — esto es rojo solo si algo falla")

            # 5. Prompt prefix
            console.print(
                f"\n  [info]Prefijo prompt:[/]  [bold]{target.prompt_prefix}[/]"
            )
            console.print(
                f"  [info]Bordes:[/]          "
                f"[bold {target.border_style}]{target.border_style}[/]"
            )
            console.print(f"  [info]Descripción:[/]    {target.description}")
            console.print()
        finally:
            console.pop_theme()
        return

    try:
        set_theme(text)
        console.print(f"[success]✓ Tema cambiado a: {text}[/]")
    except Exception as exc:
        render_error(f"Error cambiando tema: {exc}")


# ── /config command ───────────────────────────────────────────────────


async def run_config_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /config para mostrar o editar la configuración de la sesión.

    Examples:
        /config
        /config model gpt-4o
        /config provider openai
    """
    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        cfg = session.config
        console.print(f"[info]Modelo: [model]{cfg.model}[/]")
        console.print(f"[info]Proveedor: [model]{cfg.provider}[/]")
        console.print(f"[info]Base URL: [model]{cfg.base_url}[/]")
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        render_error("Uso: /config <clave> <valor>")
        return
    key, value = parts[0], parts[1]
    if not hasattr(session.config, key):
        render_error(f"Clave de configuración desconocida: {key}")
        return
    setattr(session.config, key, value)
    console.print(f"[success]✓ {key} = {value}[/]")


# ── /agent command ───────────────────────────────────────────────────


async def run_agent_command(session: SessionRuntime, args: str) -> None:
    """Ejecuta /agent [mode|start|stop|status] para controlar el agente.

    Examples:
        /agent
        /agent mode <modo>
        /agent start
        /agent stop
    """
    from ..agent_modes import apply_agent_mode, list_agent_modes

    text = args.strip()

    if not text or text.lower() in ("show", "status"):
        current = getattr(session, "agent_mode", "default")
        console.print(f"[info]Modo agente: [model]{current}[/]")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "mode":
        if not rest:
            render_error("Uso: /agent mode <modo>")
            return
        available = [m.name for m in list_agent_modes()]
        if rest not in available:
            render_error(f"Modo desconocido: {rest}. Opciones: {', '.join(available)}")
            return
        mode_obj = next((m for m in list_agent_modes() if m.name == rest), None)
        if mode_obj is None:
            render_error(f"Modo desconocido: {rest}")
            return
        apply_agent_mode(session, mode_obj)
        console.print(f"[success]✓ Modo agente cambiado a: {rest}[/]")
        return

    if subcmd in ("start", "on"):
        session.agent_mode = getattr(session, "agent_mode", "default")
        console.print(f"[success]✓ Agente activado: {session.agent_mode}[/]")
        return

    if subcmd in ("stop", "off"):
        console.print("[success]✓ Agente detenido.[/]")
        return

    render_error("Uso: /agent [mode <modo>|start|stop|status]")


# ── /status command ───────────────────────────────────────────────────


async def run_status_command(session: SessionRuntime, args: str) -> None:
    """Show session status with color-coded usage levels (/status)."""
    from rich.table import Table

    text = args.strip()
    if text:
        render_error("Uso: /status")
        return

    total = session.total_usage or {}
    prompt = total.get("prompt_tokens", 0) or 0
    completion = total.get("completion_tokens", 0) or 0
    tokens_total = prompt + completion
    history = session.history or []

    # Color-code token usage: green < 4k, yellow < 16k, red >= 16k
    if tokens_total < 4000:
        usage_style = "green"
    elif tokens_total < 16000:
        usage_style = "yellow"
    else:
        usage_style = "red"

    table = Table(
        title="[bold realm]᛭ Estado de la sesión[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
        caption=f"[dim]{len(history)} mensajes en historial[/dim]",
    )
    table.add_column("Propiedad", style="tool.name")
    table.add_column("Valor", style="tool.result")
    table.add_row("Modelo", str(session.config.model))
    table.add_row("Proveedor", str(session.config.provider))
    table.add_row("Prompt tokens", f"[{usage_style}]{prompt}[/{usage_style}]")
    table.add_row("Completion tokens", f"[{usage_style}]{completion}[/{usage_style}]")
    table.add_row("Total tokens", f"[bold {usage_style}]{tokens_total}[/bold {usage_style}]")
    table.add_row("Mensajes", str(len(history)))

    # Session start time if available
    start_time = getattr(session, "_start_time", None)
    if start_time:
        import time as _time
        elapsed = int(_time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            uptime = f"{hours}h {mins}m {secs}s"
        elif mins:
            uptime = f"{mins}m {secs}s"
        else:
            uptime = f"{secs}s"
        table.add_row("Uptime", f"[dim]{uptime}[/dim]")

    # Last command if available
    last_cmd = getattr(session, "_last_command", None)
    if last_cmd:
        table.add_row("Último comando", f"[dim]/{last_cmd}[/dim]")

    console.print(table)
    console.print()

# ── /profile command ─────────────────────────────────────────────────


async def run_profile_command(session: SessionRuntime, args: str) -> None:
    """Gestiona perfiles de configuración (/profile [list|save|show|load|delete])."""
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        profiles = _load_profiles()
        if not profiles:
            console.print("[dim]No hay perfiles guardados.[/dim]")
            return
        console.print("\n[bold realm]᛭ Perfiles de agente[/]\n")
        for name, profile in sorted(profiles.items()):
            desc = profile.get("description", "")
            provider = profile.get("provider", "—")
            model = profile.get("model", "—")
            theme = profile.get("theme", "—")
            console.print(
                f"  [bold cyan]{name}[/]"
                f" — [dim]provider={provider} · model={model} · theme={theme}[/]"
                f"{f' — [dim]{desc}[/]' if desc else ''}"
            )
        console.print()
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    profiles = _load_profiles()

    if subcmd == "save":
        if not rest:
            render_error("Uso: /profile save <nombre>")
            return
        profiles[rest] = {
            "provider": session.config.provider,
            "model": session.config.model,
            "theme": get_theme().name,
            "description": f"Basado en {session.config.provider}/{session.config.model}",
        }
        _save_profiles(profiles)
        console.print(f"[success]✓ Perfil guardado: {rest}[/]")
        return

    if subcmd == "show":
        if not rest:
            render_error("Uso: /profile show <nombre>")
            return
        if rest not in profiles:
            render_error(f"Perfil no encontrado: {rest}")
            return
        console.print(
            f"[dim]{json.dumps({rest: profiles[rest]}, indent=2, ensure_ascii=False)}[/]"
        )
        return

    if subcmd == "load":
        if not rest:
            render_error("Uso: /profile load <nombre>")
            return
        if rest not in profiles:
            render_error(f"Perfil no encontrado: {rest}")
            return
        profile = profiles[rest]
        profile_theme = profile.get("theme")
        if profile_theme:
            try:
                set_theme(str(profile_theme))
            except KeyError:
                render_error(
                    f"El perfil {rest} referencia un tema desconocido: {profile_theme}"
                )
                return
        if hasattr(session.config, "provider"):
            session.config.provider = profile.get("provider", session.config.provider)
        if hasattr(session.config, "model"):
            session.config.model = profile.get("model", session.config.model)
        console.print(f"[success]✓ Perfil cargado: {rest}[/]")
        return

    if subcmd == "delete":
        if not rest:
            render_error("Uso: /profile delete <nombre>")
            return
        if rest not in profiles:
            render_error(f"Perfil no encontrado: {rest}")
            return
        del profiles[rest]
        _save_profiles(profiles)
        console.print(f"[warning]✗ Perfil eliminado: {rest}[/]")
        return

    render_error(
        "Uso: /profile [list|save <nombre>|show <nombre>|load <nombre>|delete <nombre>]"
    )


# ── /model-info command ──────────────────────────────────────────────────


# Provider hint derived from model-name prefixes or known families.
_MODEL_PROVIDER_HINTS: dict[str, str] = {
    "claude": "Anthropic",
    "gpt": "OpenAI",
    "o3": "OpenAI",
    "kimi": "Moonshot",
    "moonshot": "Moonshot",
    "seed": "BytePlus",
    "glm": "BytePlus",
    "grok": "xAI",
    "local-model": "Local",
}

# Capabilities are broad tags useful for REPL display.
_MODEL_CAPABILITIES: dict[str, list[str]] = {
    "claude-sonnet-4": ["chat", "tool-calling", "vision", "long-context", "streaming"],
    "claude-opus-4": ["chat", "tool-calling", "vision", "long-context", "streaming", "reasoning"],
    "claude-opus-5": ["chat", "tool-calling", "vision", "long-context", "streaming", "reasoning"],
    "claude-haiku-4": ["chat", "tool-calling", "vision", "streaming"],
    "gpt-4o": ["chat", "tool-calling", "vision", "streaming"],
    "gpt-4o-mini": ["chat", "tool-calling", "vision", "streaming"],
    "o3": ["chat", "reasoning", "tool-calling", "streaming"],
    "kimi-for-coding": ["chat", "tool-calling", "long-context", "streaming"],
    "moonshot-v1-128k": ["chat", "long-context", "streaming"],
    "seed-1-6-250915": ["chat", "tool-calling", "streaming"],
    "glm-4-7-251222": ["chat", "tool-calling", "streaming"],
    "grok-4.20-0309-non-reasoning": ["chat", "tool-calling", "streaming"],
    "grok-4": ["chat", "tool-calling", "long-context", "streaming"],
    "grok-3": ["chat", "tool-calling", "long-context", "streaming"],
    "local-model": ["chat", "local"],
}


def _provider_hint(model: str) -> str:
    """Return a human-readable provider hint for *model*."""
    lower = model.lower()
    for prefix, provider in _MODEL_PROVIDER_HINTS.items():
        if lower.startswith(prefix):
            return provider
    return "Unknown"


def _model_capabilities(model: str) -> list[str]:
    """Return capability tags for *model*."""
    return _MODEL_CAPABILITIES.get(model, ["chat"])


def _format_price(rate: float) -> str:
    """Format a price-per-million-tokens rate as USD."""
    if rate == 0.0:
        return "—"
    return f"${rate:.2f}"


def _format_context_window(tokens: int) -> str:
    """Format a context-window size in a human-readable way."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}K"
    return str(tokens)


def _print_model_info_table(model: str, *, is_current: bool = False) -> None:
    """Render detailed info for a single model as a Rich table."""
    from rich.table import Table

    from ..providers import _MODEL_CONTEXTS, _MODEL_PRICING

    context_window = _MODEL_CONTEXTS.get(model, 128_000)
    input_price, output_price = _MODEL_PRICING.get(model, (0.0, 0.0))
    provider = _provider_hint(model)
    capabilities = _model_capabilities(model)

    title = f"[bold realm]᛭ Modelo {model}[/]"
    if is_current:
        title += " [dim](actual)[/]"

    table = Table(
        title=title,
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Propiedad", style="tool.name")
    table.add_column("Valor", style="tool.result")

    table.add_row("Nombre", model)
    table.add_row("Proveedor", provider)
    table.add_row("Ventana de contexto", f"{context_window} tokens ({_format_context_window(context_window)})")
    table.add_row("Precio entrada", f"{_format_price(input_price)} / 1M tokens")
    table.add_row("Precio salida", f"{_format_price(output_price)} / 1M tokens")
    table.add_row("Capacidades", ", ".join(capabilities))
    console.print(table)


def _print_model_list() -> None:
    """Render a compact list of all known models with pricing."""
    from rich.table import Table

    from ..providers import _MODEL_CONTEXTS, _MODEL_PRICING

    table = Table(
        title="[bold realm]᛭ Modelos conocidos[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Modelo", style="tool.name")
    table.add_column("Proveedor", style="tool.result")
    table.add_column("Contexto", justify="right")
    table.add_column("Entrada / 1M", justify="right")
    table.add_column("Salida / 1M", justify="right")

    for model in sorted(_MODEL_CONTEXTS.keys()):
        provider = _provider_hint(model)
        context_window = _MODEL_CONTEXTS.get(model, 0)
        input_price, output_price = _MODEL_PRICING.get(model, (0.0, 0.0))
        table.add_row(
            model,
            provider,
            f"{_format_context_window(context_window)}",
            _format_price(input_price),
            _format_price(output_price),
        )

    console.print(table)


async def run_model_info_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /model-info para mostrar información detallada de modelos.

    Examples:
        /model-info                 — información del modelo actual
        /model-info <modelo>        — información de un modelo específico
        /model-info list            — lista todos los modelos conocidos con precios
    """
    from ..providers import _MODEL_CONTEXTS

    text = args.strip()

    if not text or text.lower() == "current":
        model = session.config.model
        _print_model_info_table(model, is_current=True)
        return

    if text.lower() in ("list", "ls", "all"):
        _print_model_list()
        return

    model = text
    if model.lower() not in {m.lower() for m in _MODEL_CONTEXTS}:
        render_error(f"Modelo desconocido: {model}. Usá /model-info list para ver los conocidos.")
        return

    # Use canonical casing from the registry.
    canonical = next(m for m in _MODEL_CONTEXTS if m.lower() == model.lower())
    _print_model_info_table(canonical, is_current=(canonical == session.config.model))


async def run_json_mode_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /json-mode para alternar la salida estructurada JSON del LLM.

    Examples:
        /json-mode on     — habilita JSON output
        /json-mode off    — deshabilita JSON output
        /json-mode status — muestra el estado actual
    """
    text = args.strip().lower()

    if not text or text == "status":
        state = "ON" if getattr(session, "_json_mode", False) else "OFF"
        console.print(f"[bold realm]᛭ JSON mode:[/] [cyan]{state}[/]")
        return

    if text == "on":
        session._json_mode = True
        console.print("[success]✓ JSON mode habilitado.[/]")
        return

    if text == "off":
        session._json_mode = False
        console.print("[success]✓ JSON mode deshabilitado.[/]")
        return

    render_error("Uso: /json-mode [on|off|status]")


async def run_hooks_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Gestiona hooks del lifecycle (/hooks [list|add <event> <file>|remove <event> <file>])."""

    from ..hooks import _EVENTS, _HOOKS_DIR, list_hooks

    text = args.strip()
    if not text or text == "list":
        installed = list_hooks()
        console.print("\n[bold realm]᛭ Hooks de Lilith ⚔[/]\n")
        for event in _EVENTS:
            files = installed.get(event, [])
            count = len(files)
            mark = f"[green]{count} script(s)[/]" if count > 0 else "[dim](ninguno)[/]"
            console.print(f"  [bold cyan]{event}[/]: {mark}")
            for f in files:
                console.print(f"    [dim]└─ {f}[/]")
        console.print(f"\n[muted]Directorio: {_HOOKS_DIR}[/muted]")
        console.print("[dim]Eventos: " + ", ".join(_EVENTS) + "[/dim]")
        return

    if text == "help":
        console.print(
            "[dim]Eventos: pre-tool-call, post-tool-call, on-error, on-cancel, on-compact[/dim]"
        )
        return

    parts = text.split(maxsplit=2)
    if len(parts) >= 3 and parts[0] == "add":
        event = parts[1]
        if event not in _EVENTS:
            console.print(f"[error]Evento desconocido: {event}[/error]")
            return
        script_path = Path(parts[2]).expanduser()
        if not script_path.exists():
            console.print(f"[error]Script no existe: {script_path}[/error]")
            return
        target_dir = _HOOKS_DIR / event
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / script_path.name
        target.write_text(script_path.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            target.chmod(0o755)
        except Exception:
            pass
        console.print(f"[success]✓ Hook registrado: {target}[/success]")
        return

    if len(parts) >= 3 and parts[0] == "remove":
        event = parts[1]
        if event not in _EVENTS:
            console.print(f"[error]Evento desconocido: {event}[/error]")
            return
        target = _HOOKS_DIR / event / parts[2]
        if not target.exists():
            console.print(f"[error]Hook no existe: {target}[/error]")
            return
        target.unlink()
        console.print(f"[warning]✗ Hook eliminado: {target}[/warning]")
        return

    console.print(
        "[dim]Uso: /hooks [list|add <event> <file>|remove <event> <file>|help][/dim]"
    )


async def run_editor_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /editor para abrir archivos en el editor preferido.

    Examples:
        /editor <archivo>              — abre el archivo
        /editor <archivo>:<línea>      — abre el archivo en una línea específica
        /editor set <comando>          — establece el editor preferido
        /editor current                — muestra el editor configurado
    """
    text = args.strip()

    if not text:
        render_error("Uso: /editor <archivo>[:línea] | /editor set <comando> | /editor current")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "set":
        command = rest.strip()
        if not command:
            render_error("Uso: /editor set <comando>")
            return
        _set_editor(command)
        console.print(f"[success]✓ Editor configurado: [bold cyan]{command}[/][/]")
        return

    if subcmd == "current":
        editor = _get_editor()
        if editor:
            console.print(f"[info]Editor actual: [bold cyan]{editor}[/][/]")
        else:
            console.print("[warning]⚠ No hay editor configurado. Usa /editor set <comando> o define $EDITOR.[/]")
        return

    # /editor <archivo>[:línea]
    line: int | None = None
    target = text
    if ":" in text:
        # Split from the last colon to handle Windows paths with drive letters.
        #
        # Antes esto exigia ademas `not target.startswith("/")`, lo que
        # desactivaba el parseo de linea para CUALQUIER ruta absoluta de
        # Unix: `/editor /ruta/archivo.txt:42` intentaba abrir un archivo
        # llamado "archivo.txt:42" y cortaba con "Archivo no encontrado".
        # El guard sobraba: rpartition ya parte por el ULTIMO ":", asi que
        # "C:/x/f.txt" da line_part="/x/f.txt" (no es digito, no se toca) y
        # "C:/x/f.txt:42" da line_part="42". Las unidades de Windows quedan
        # cubiertas sin romper Unix.
        path_part, _, line_part = text.rpartition(":")
        if path_part and line_part.isdigit():
            target = path_part
            line = int(line_part)

    path = Path(target).expanduser()
    if not path.exists():
        render_error(f"Archivo no encontrado: {path}")
        return
    if not path.is_file():
        render_error(f"La ruta no es un archivo: {path}")
        return

    editor = _get_editor()
    if editor is None:
        render_error("No se encontró un editor. Define $EDITOR o usa /editor set <comando>.")
        return

    # Build command line preserving the editor command as a single token if possible.
    cmd = shlex.split(editor)
    if line is not None:
        # Common line-number syntaxes. Try the simplest first; if the editor is known
        # to use a specific flag, use that. Otherwise append +N for vi/vim/nano style.
        editor_base = os.path.basename(cmd[0]).lower() if cmd else ""
        if editor_base in {"code", "code.exe", "code-oss", "code-oss.exe", "cursor", "cursor.exe"}:
            cmd.extend(["--goto", f"{path}:{line}"])
        elif editor_base in {"subl", "subl.exe", "sublime_text", "sublime_text.exe"}:
            cmd.extend([f"{path}:{line}"])
        elif editor_base in {"idea", "idea.exe", "idea64", "idea64.exe"}:
            cmd.extend(["--line", str(line), str(path)])
        elif editor_base in {"atom", "atom.exe"}:
            cmd.extend([f"{path}:{line}"])
        else:
            # vi/vim/nano/emacs fallback: +N
            cmd.append(f"+{line}")
            cmd.append(str(path))
    else:
        cmd.append(str(path))

    try:
        subprocess.Popen(cmd, stdin=None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        render_error(f"Error abriendo el editor: {exc}")
        return

    console.print(f"[success]✓ Abriendo [bold cyan]{path}[/] en {editor}[/]")


# ── /profile (saved agent config profiles) ──────────────────────────────────


_PROFILES_PATH: Path = Path.home() / ".yggdrasil" / "profiles.json"

_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "provider": "opencode-go",
        "model": "glm-5.2",
        "theme": "norse",
        "description": "Rápido y económico",
    },
    "reasoning": {
        "provider": "anthropic",
        "model": "claude-opus-4",
        "theme": "norse",
        "description": "Razonamiento profundo (costoso)",
    },
    "local": {
        "provider": "local",
        "model": "local-model",
        "theme": "norse",
        "description": "Modelo local, sin costo de API",
    },
}


def _profiles_path() -> Path:
    global _PROFILES_PATH
    if _PROFILES_PATH is None:
        home = Path(os.environ.get("HOME", os.path.expanduser("~")))
        _PROFILES_PATH = home / ".yggdrasil" / "profiles.json"
    return _PROFILES_PATH


def _ensure_profiles() -> Path:
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _PROFILES_PATH.exists():
        _save_profiles(_DEFAULT_PROFILES)
    return _PROFILES_PATH


def _load_profiles() -> dict[str, dict[str, Any]]:
    _ensure_profiles()
    try:
        import json as _json
        data = _json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("se esperaba un dict JSON")
    except Exception as exc:
        # Don't hide corruption: keep a copy before the next save replaces it.
        preserve_corrupt(_PROFILES_PATH, exc)
    return dict(_DEFAULT_PROFILES)


def _save_profiles(profiles: dict[str, dict[str, Any]]) -> None:
    import json as _json
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES_PATH.write_text(
        _json.dumps(profiles, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _render_macro_status(subcmd: str, name: str = "") -> None:
    """Print a Rich status indicator before delegating to MacroCommand.

    Adds visual framing only — does NOT change behavior. The MacroCommand
    inside still does the actual recording / stopping / playback.
    """
    try:
        from rich.panel import Panel
        from rich.table import Table
    except Exception:
        return

    if subcmd == "record":
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column(style="white")
        grid.add_row("Accion", "[bold green]Iniciar grabacion[/bold green]")
        grid.add_row("Nombre", f"[bold cyan]{name or '?'}[/bold cyan]")
        grid.add_row("Estado", "[bold red]\u25cf REC[/bold red]")
        grid.add_row(
            "Tip",
            "Cada comando de barra que escribas se anadira a la macro. "
            "Usa /macro stop para finalizar.",
        )
        console.print(Panel(
            grid,
            title="[bold realm]\u16ed /macro record[/]",
            border_style="green",
            expand=False,
        ))
        console.print()
    elif subcmd == "stop":
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column(style="white")
        grid.add_row("Accion", "[bold red]Detener grabacion[/bold red]")
        grid.add_row("Estado", "[dim]\u25cb STOP[/dim]")
        console.print(Panel(
            grid,
            title="[bold realm]\u16ed /macro stop[/]",
            border_style="red",
            expand=False,
        ))
        console.print()
