"""Slash command registry for Yggdrasil CLI v6.0.

Each command is a class with ``name``, ``description``, and an async
``execute(args)`` method.  The registry discovers and manages all commands
and provides routing by command name.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from .config import CONFIG_DIR, CONFIG_FILE, find_project_config, load_config
from .render import (
    console,
    get_theme,
    list_themes,
    render_error,
    render_status,
    set_theme,
)

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table


# ── Feedback storage helpers ─────────────────────────────────────────

_FEEDBACK_PATH = CONFIG_DIR / "feedback.json"


def _load_feedback() -> list[dict[str, Any]]:
    """Load feedback entries from ``~/.yggdrasil/feedback.json``."""
    if not _FEEDBACK_PATH.exists():
        return []
    try:
        data = json.loads(_FEEDBACK_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as exc:  # pragma: no cover — defensive
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando feedback: %s", exc)
    return []


def _save_feedback(entries: list[dict[str, Any]]) -> None:
    """Persist feedback entries to ``~/.yggdrasil/feedback.json``."""
    _FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FEEDBACK_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


if TYPE_CHECKING:
    from .agent import AgentSession


# ── Bookmark storage helper ─────────────────────────────────────────

_BOOKMARKS_PATH = CONFIG_DIR / "bookmarks.json"


def _load_bookmarks() -> list[dict[str, Any]]:
    """Load bookmarks from ``~/.yggdrasil/bookmarks.json``."""
    if not _BOOKMARKS_PATH.exists():
        return []
    try:
        data = json.loads(_BOOKMARKS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as exc:  # pragma: no cover — defensive
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando bookmarks: %s", exc)
    return []


def _save_bookmarks(bookmarks: list[dict[str, Any]]) -> None:
    """Persist bookmarks to ``~/.yggdrasil/bookmarks.json``."""
    _BOOKMARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BOOKMARKS_PATH.write_text(
        json.dumps(bookmarks, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ── Base command ────────────────────────────────────────────────────


class BaseCommand:
    """Abstract base for a slash command."""

    name: str = ""
    description: str = ""
    aliases: list[str] = []

    def __init__(self, session: AgentSession) -> None:
        self.session = session

    async def execute(self, args: str) -> None:
        """Run the command. *args* is everything after the command name."""
        raise NotImplementedError

    def session_console_capture(self):
        """Expose Rich's capture context for command tests and embedders."""
        return console.capture()


# ── Command implementations ─────────────────────────────────────────


class HelpCommand(BaseCommand):
    name = "help"
    description = "Mostrar comandos disponibles"
    aliases = ["h", "?"]

    async def execute(self, _args: str) -> None:
        from .commands import CommandRegistry

        registry = CommandRegistry(self.session)
        registry.discover()

        console.print("\n[bold realm]᛭ Comandos de Yggdrasil[/]\n")
        for cmd in sorted(registry._commands.values(), key=lambda c: c.name):
            aliases = f" ({', '.join(f'/{a}' for a in cmd.aliases)})" if cmd.aliases else ""
            console.print(f"  [bold cyan]/{cmd.name}[/]{aliases}  [dim]— {cmd.description}[/]")
        console.print()


class QuickstartCommand(BaseCommand):
    """30-second tour for new users.

    ``/quickstart`` shows the brief tour; ``/quickstart full`` shows a
    detailed guide with examples for each major command.
    """

    name = "quickstart"
    description = "Tour de 30 segundos para nuevos usuarios"
    aliases = ["qs", "start"]

    async def execute(self, args: str) -> None:
        if args.strip().lower() == "full":
            await self._full_guide()
        else:
            await self._brief_tour()

    async def _brief_tour(self) -> None:
        from .commands import CommandRegistry

        registry = CommandRegistry(self.session)
        registry.discover()
        cmd_count = len(registry._commands)

        all_tools = self.session._all_tool_names()
        tool_count = len(all_tools) if all_tools else 14  # fallback for new users

        sections: list[str] = []
        sections.append(
            "[bold realm]Bienvenido a Lilith[/] — el agente CLI de Yggdrasil. "
            f"[dim]({tool_count} herramientas · {cmd_count} comandos)[/]"
        )
        sections.append("\n[bold cyan]Top 5 comandos[/]")
        sections.append("  [bold cyan]/help[/]      — lista todos los comandos")
        sections.append("  [bold cyan]/plan[/]      — crea o revisa un plan de trabajo")
        sections.append("  [bold cyan]/undo[/]      — deshace la última operación de archivo")
        sections.append("  [bold cyan]/tools[/]     — lista, habilita o deshabilita herramientas")
        sections.append("  [bold cyan]/cost[/]      — muestra costo y uso de tokens")

        sections.append("\n[bold cyan]Seguridad[/]")
        sections.append("  [status.ok]✓[/] [bold]diff-preview[/] — revisa cambios antes de aplicarlos")
        sections.append("  [status.ok]✓[/] [bold]undo[/] — respalda automáticamente archivos antes de editar")
        sections.append("  [status.ok]✓[/] [bold]smart retry[/] — reintentos automáticos en errores transitorios")
        sections.append("  [status.ok]✓[/] [bold]confirmación[/] — preguntas antes de operaciones destructivas")

        sections.append("\n[bold cyan]Herramientas destacadas[/]")
        sections.append("  [tool.name]read_file[/] / [tool.name]write_file[/] / [tool.name]patch[/] — edición de archivos")
        sections.append("  [tool.name]terminal[/] — ejecuta comandos del sistema")
        sections.append("  [tool.name]git_operation[/] — status, commit, diff, push")
        sections.append("  [tool.name]search_files[/] — búsqueda en archivos")

        sections.append("\n[bold cyan]Ejemplos rápidos[/]")
        sections.append("  [bold cyan]/plan[/] crear tests para el módulo de facturación")
        sections.append("  [bold cyan]/undo[/] list")
        sections.append("  [bold cyan]/tools[/] disable web_search")
        sections.append("  [bold cyan]/cost[/]")
        sections.append("  [bold cyan]/commands[/] tokens")

        sections.append(
            "\n[dim]Escribe [bold cyan]/quickstart full[/] para una guía detallada con ejemplos.[/]"
        )

        console.print()
        console.print(
            Panel(
                "\n".join(sections),
                title="[bold realm]᛭ Guía rápida de Lilith[/]",
                border_style=get_theme().border_style,
                expand=False,
                padding=(0, 2),
            )
        )
        console.print()

    async def _full_guide(self) -> None:
        from .commands import CommandRegistry

        registry = CommandRegistry(self.session)
        registry.discover()
        cmd_count = len(registry._commands)

        all_tools = self.session._all_tool_names()
        tool_count = len(all_tools) if all_tools else 14

        sections: list[str] = []
        sections.append(
            f"Lilith incluye [model]{tool_count}[/] herramientas y [model]{cmd_count}[/] comandos de barra. "
            "Aquí tienes una guía detallada con ejemplos de cada grupo."
        )

        sections.append("\n[bold cyan]1. Sesión y mensajes[/]")
        sections.append("  [bold cyan]/clear[/]     — limpia el historial de la conversación")
        sections.append("  [bold cyan]/history[/]   — muestra el historial reciente")
        sections.append("  [bold cyan]/compact[/]   — resume mensajes largos para ahorrar contexto")
        sections.append("  [bold cyan]/save[/]      — guarda la conversación en un archivo")
        sections.append("  [bold cyan]/copy[/]      — copia la última respuesta al portapapeles")

        sections.append("\n[bold cyan]2. Planificación[/]")
        sections.append("  [bold cyan]/plan[/] <tarea>       — crea un plan paso a paso")
        sections.append("  [bold cyan]/plan[/] progress       — muestra el progreso del plan actual")
        sections.append("  [bold cyan]/continue[/]           — reanuda el plan en curso")
        sections.append("  [bold cyan]/redo[/]               — repite el último mensaje del usuario")
        sections.append("  [bold cyan]/retry[/]              — reintenta la última respuesta fallida")

        sections.append("\n[bold cyan]3. Herramientas[/]")
        sections.append("  [bold cyan]/tools[/]              — lista todas las herramientas")
        sections.append("  [bold cyan]/tools[/] enabled      — muestra las herramientas activas")
        sections.append("  [bold cyan]/tools[/] disable <name>  — desactiva una herramienta")
        sections.append("  [bold cyan]/tools[/] enable <name>   — reactiva una herramienta")

        sections.append("\n[bold cyan]4. Seguridad y control[/]")
        sections.append("  [bold cyan]/undo[/]               — deshace el último cambio de archivo")
        sections.append("  [bold cyan]/undo[/] list          — muestra backups pendientes")
        sections.append("  [bold cyan]/diff[/]                — previsualiza cambios antes de aplicarlos")
        sections.append("  [bold cyan]/confirm[/] on|off     — activa o desactiva confirmaciones")

        sections.append("\n[bold cyan]5. Información y utilidades[/]")
        sections.append("  [bold cyan]/cost[/]                — costo estimado de la sesión")
        sections.append("  [bold cyan]/usage[/]               — estadísticas de uso (tokens, herramientas, tiempo)")
        sections.append("  [bold cyan]/tokens[/]              — uso de tokens")
        sections.append("  [bold cyan]/status[/]              — estado general de la sesión")
        sections.append("  [bold cyan]/model[/] <modelo>      — cambia el modelo activo")
        sections.append("  [bold cyan]/provider[/] <provider> — cambia el proveedor LLM")
        sections.append("  [bold cyan]/theme[/] <nombre>      — cambia el tema visual")
        sections.append("  [bold cyan]/search[/] <query>      — busca en historial, archivos o proyectos")
        sections.append("  [bold cyan]/git[/] <subcomando>    — ejecuta operaciones de git")
        sections.append("  [bold cyan]/todos[/] add <tarea>   — gestiona tareas pendientes")
        sections.append("  [bold cyan]/env[/]                 — muestra variables de entorno")

        sections.append("\n[bold cyan]6. Configuración del proyecto[/]")
        sections.append("  [bold cyan]/init[/]               — crea un proyecto Lilith (.lilith/CLAUDE.md)")
        sections.append("  [bold cyan]/config[/]             — muestra la configuración actual")
        sections.append("  [bold cyan]/ygg[/]                 — contexto del proyecto Yggdrasil")
        sections.append("  [bold cyan]/where[/]               — muestra la ruta del proyecto actual")
        sections.append("  [bold cyan]/doctor[/]              — diagnostica el entorno y dependencias")

        sections.append("\n[bold cyan]Ejemplos de flujo de trabajo[/]")
        sections.append("  [dim]Iniciar un proyecto:[/]")
        sections.append("    [bold cyan]/init[/] mi-proyecto")
        sections.append("    [bold cyan]/plan[/] analizar requerimientos, diseñar API, implementar endpoints, tests")
        sections.append("  [dim]Escribir código con seguridad:[/]")
        sections.append("    [bold cyan]/diff[/]")
        sections.append("    [bold cyan]/undo[/] list")
        sections.append("  [dim]Revisar costos y contexto:[/]")
        sections.append("    [bold cyan]/cost[/]")
        sections.append("    [bold cyan]/tokens[/]")
        sections.append("  [dim]Buscar y refactorizar:[/]")
        sections.append("    [bold cyan]/search[/] across 'TODO' src/")
        sections.append("    [bold cyan]/git[/] status")

        sections.append(
            "\n[dim]Para salir escribe [bold cyan]/quit[/] o [bold cyan]/exit[/]. "
            "Usa [bold cyan]/commands[/] para ver todos los comandos agrupados.[/]"
        )

        console.print()
        console.print(
            Panel(
                Markdown("\n".join(sections)),
                title="[bold realm]᛭ Guía completa de Lilith[/]",
                border_style=get_theme().border_style,
                expand=False,
                padding=(0, 2),
            )
        )
        console.print()


class CommandsCommand(BaseCommand):
    """List all slash commands in a compact, grouped format.

    ``/commands`` shows every available command grouped by category.
    ``/commands <patrón>`` filters commands by name, alias, or description.
    """

    name = "commands"
    description = "Listar comandos disponibles agrupados por categoría"
    aliases = ["cmds"]

    # Category mapping for the built-in command set. Any command not listed
    # here falls back to "Otros" so the list stays complete even if new
    # commands are added and not yet categorized.
    _CATEGORIES: dict[str, set[str]] = {
        "Sesión": {
            "clear",
            "save",
            "history",
            "compact",
            "resume",
            "redo",
            "retry",
            "continue",
            "copy",
            "export",
            "replay",
            "macro",
        },
        "Herramientas": {"tools"},
        "Seguridad": {"confirm", "diff", "undo", "agent"},
        "Plan": {"plan"},
        "Memoria": {"memory"},
        "Info": {
            "help",
            "quickstart",
            "commands",
            "status",
            "model",
            "provider",
            "cost",
            "tokens",
            "usage",
            "metrics",
            "bifrost",
            "config",
            "theme",
            "agent",
        },
        "Memoria": {"memory"},
        "Sistema": {"init", "file", "ygg", "quit"},
        "Otros": {"diff-config"},
    }

    def _category_for(self, cmd_name: str) -> str:
        for category, names in self._CATEGORIES.items():
            if cmd_name in names:
                return category
        return "Otros"

    async def execute(self, args: str) -> None:
        from .commands import CommandRegistry

        registry = CommandRegistry(self.session)
        registry.discover()

        pattern = args.strip().lower()
        all_commands = sorted(registry._commands.values(), key=lambda c: c.name)

        if pattern:
            filtered = [
                cmd
                for cmd in all_commands
                if pattern in cmd.name.lower()
                or pattern in cmd.description.lower()
                or any(pattern in a.lower() for a in cmd.aliases)
            ]
            if not filtered:
                console.print(f"[dim]Ningún comando coincide con '{args.strip()}'.[/]")
                return
            all_commands = filtered

        # Group by category while preserving category order.
        groups: dict[str, list[BaseCommand]] = {}
        for cmd in all_commands:
            cat = self._category_for(cmd.name)
            groups.setdefault(cat, []).append(cmd)

        category_order = [
            "Sesión",
            "Herramientas",
            "Seguridad",
            "Plan",
            "Memoria",
            "Info",
            "Sistema",
            "Otros",
        ]

        console.print("\n[bold realm]᛭ Comandos de Yggdrasil[/]\n")
        for cat in category_order:
            if cat not in groups:
                continue
            console.print(f"[bold]{cat}[/]")
            for cmd in groups[cat]:
                aliases = f" ({', '.join(f'/{a}' for a in cmd.aliases)})" if cmd.aliases else ""
                console.print(f"  [bold cyan]/{cmd.name}[/][dim]{aliases}[/] — {cmd.description}")
            console.print()


class ToolsCommand(BaseCommand):
    name = "tools"
    description = "Listar, habilitar o deshabilitar herramientas"
    aliases = ["tool"]

    async def execute(self, args: str) -> None:
        arg = args.strip()
        if not arg:
            await self._list_all()
            return

        tokens = arg.split()
        subcmd = tokens[0].lower()
        rest = " ".join(tokens[1:]).strip()

        if subcmd in ("enabled", "activas"):
            await self._list_enabled()
            return

        if subcmd in ("disabled", "inactivas"):
            await self._list_disabled()
            return

        if subcmd in ("enable", "habilitar"):
            if not rest:
                render_error("Uso: /tools enable <herramienta>")
                return
            await self._enable(rest)
            return

        if subcmd in ("disable", "deshabilitar"):
            if not rest:
                render_error("Uso: /tools disable <herramienta>")
                return
            await self._disable(rest)
            return

        render_error(f"Subcomando desconocido: /tools {subcmd}")

    async def _list_all(self) -> None:
        all_tools = self.session._all_tool_names()
        enabled = {t["name"] for t in self.session.get_tool_descriptions()}
        if not all_tools:
            console.print("[warning]No hay herramientas disponibles.[/]")
            return

        console.print("\n[bold realm]᛭ Herramientas[/]\n")
        for name in sorted(all_tools):
            status = "✓" if name in enabled else "✗"
            console.print(f"  [{status}] [tool.name]{name}[/]")
        console.print()

    async def _list_enabled(self) -> None:
        tools = self.session.get_tool_descriptions()
        if not tools:
            console.print("[warning]No hay herramientas habilitadas.[/]")
            return

        console.print("\n[bold realm]᛭ Herramientas habilitadas[/]\n")
        for tool in tools:
            console.print(f"  [tool.name]{tool['name']}[/]")
        console.print()

    async def _list_disabled(self) -> None:
        all_tools = self.session._all_tool_names()
        enabled = {t["name"] for t in self.session.get_tool_descriptions()}
        disabled = sorted(all_tools - enabled)
        if not disabled:
            console.print("[info]Todas las herramientas están habilitadas.[/]")
            return

        console.print("\n[bold realm]᛭ Herramientas deshabilitadas[/]\n")
        for name in disabled:
            console.print(f"  [tool.name]{name}[/]")
        console.print()

    async def _enable(self, name: str) -> None:
        all_tools = self.session._all_tool_names()
        if name not in all_tools:
            render_error(f"Herramienta desconocida: [model]{name}[/]")
            return
        self.session.enable_tool(name)
        console.print(f"[success]✓ Herramienta habilitada: [tool.name]{name}[/]")

    async def _disable(self, name: str) -> None:
        all_tools = self.session._all_tool_names()
        if name not in all_tools:
            render_error(f"Herramienta desconocida: [model]{name}[/]")
            return
        self.session.disable_tool(name)
        console.print(f"[warning]✗ Herramienta deshabilitada: [tool.name]{name}[/]")


class ModelCommand(BaseCommand):
    name = "model"
    description = "Mostrar o cambiar el modelo activo"

    async def execute(self, args: str) -> None:
        if not args.strip():
            console.print(f"[info]Modelo actual: [model]{self.session.config.model}[/]")
            console.print(f"[info]Proveedor: [model]{self.session.config.provider}[/]")
            return

        new_model = args.strip()
        self.session.config.model = new_model
        # Re-initialise provider with the new model.
        from .providers import create_provider

        self.session.provider = create_provider(self.session.config)
        console.print(f"[success]✓ Modelo cambiado a: [model]{new_model}[/]")


class ProviderCommand(BaseCommand):
    name = "provider"
    description = "Mostrar o cambiar el proveedor LLM"

    async def execute(self, args: str) -> None:
        if not args.strip():
            console.print(f"[info]Proveedor actual: [model]{self.session.config.provider}[/]")
            providers = (
                ", ".join(self.session.config.providers.keys())
                if self.session.config.providers
                else "(ninguno configurado)"
            )
            console.print(f"[info]Perfiles: {providers}")
            return

        new_provider = args.strip()
        self.session.config.provider = new_provider
        # Update model from profile if available.
        profile = self.session.config.providers.get(new_provider.lower())
        if profile and profile.model:
            self.session.config.model = profile.model
            console.print(f"[success]✓ Modelo del perfil: [model]{profile.model}[/]")
        if profile and profile.api_key:
            self.session.config.api_key = profile.api_key
        if profile and profile.base_url:
            self.session.config.base_url = profile.base_url

        from .providers import create_provider

        self.session.provider = create_provider(self.session.config)
        console.print(f"[success]✓ Proveedor cambiado a: [model]{new_provider}[/]")


class MemoryCommand(BaseCommand):
    name = "memory"
    description = "Buscar o guardar en la memoria del agente"
    aliases = ["m"]

    async def execute(self, args: str) -> None:
        if not self.session.memory:
            render_error("Memoria no disponible (deshabilitada en configuración o falta lilith_memory).")
            return

        query = args.strip()
        if not query:
            # Show recent memories.
            results = self.session.memory.recent(limit=5)
        elif query.lower().startswith("save "):
            text = args[5:].strip()
            if not text:
                render_error("Uso: /memory save <texto> — guarda un texto en memoria.")
                return
            entry_id = self.session.memory.add(text, role="user", session_id="default")
            console.print(f"[success]✓ Guardado en memoria (id={entry_id}).[/]")
            return
        elif query.lower() == "list":
            results = self.session.memory.recent(limit=50)
        else:
            results = self.session.memory.search(query, limit=5)

        if not results:
            console.print("[dim]Sin resultados en memoria.[/]")
            return

        console.print(f"\n[bold realm]᛭ Memoria: {query or 'recientes'}[/]\n")
        for entry in results:
            content = entry.get("content", "")
            ts = entry.get("created_at", entry.get("timestamp", ""))
            eid = entry.get("id", "")
            console.print(f"  [dim][{eid}] [{ts}][/] {content[:120]}{'…' if len(content) > 120 else ''}")
        console.print()


class ClearCommand(BaseCommand):
    name = "clear"
    description = "Limpiar historial de conversación"
    aliases = ["cls"]

    async def execute(self, _args: str) -> None:
        self.session.clear_history()
        console.print("[success]✓ Historial limpiado.[/]")


class CostCommand(BaseCommand):
    """Show session cost and token usage with per-model breakdown."""

    name = "cost"
    description = "Mostrar costo estimado de la sesion"
    aliases = ["c"]

    async def execute(self, _args: str) -> None:
        from .providers import estimate_cost
        from .render import render_cost

        usage = self.session.total_usage
        model = self.session.config.model
        cost = estimate_cost(
            model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        console.print("\n[bold realm]᛭ Costo estimado de la sesion[/]\n")
        render_cost(
            total_usage=usage,
            per_model_usage=self.session.per_model_usage,
            current_model=model,
            total_cost=cost,
        )
        console.print()


class UndoCommand(BaseCommand):
    """Undo the last destructive file operation (file_write/file_edit).

    ``/undo`` and ``/undo pop`` restore the most recent backup from
    ``~/.yggdrasil/undo/``. ``/undo list`` shows pending backups.
    """

    name = "undo"
    description = "Deshacer la última operación de archivo"
    aliases = ["u"]

    async def execute(self, args: str) -> None:
        from lilith_tools.undo import UndoManager

        manager = UndoManager()
        subcmd = args.strip().lower()

        if subcmd in ("", "pop"):
            entry = manager.pop()
            if entry is None:
                render_error("No hay backups pendientes para deshacer.")
                return
            console.print(
                f"[success]✓ Deshecho: [model]{entry.original_path}[/] "
                f"restaurado desde backup ({datetime.fromtimestamp(entry.timestamp).strftime('%H:%M:%S')})[/]",
            )
            return

        if subcmd == "list":
            entries = manager.list()
            if not entries:
                console.print("[dim]No hay backups pendientes.[/]")
                return

            console.print("\n[bold realm]᛭ Backups pendientes (más reciente al final)[/]\n")
            for i, entry in enumerate(entries, start=1):
                ts = datetime.fromtimestamp(entry.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                console.print(
                    f"  [bold cyan]{i}.[/] [model]{entry.tool}[/] "
                    f"[dim]{entry.original_path}[/] · [dim]{ts}[/]",
                )
            console.print()
            return

        render_error("Uso: /undo [pop|list]  — pop es el default")


class TokensCommand(BaseCommand):
    """Show session token usage without cost."""

    name = "tokens"
    description = "Mostrar uso de tokens de la sesion"

    async def execute(self, _args: str) -> None:
        from .render import render_token_usage

        console.print("\n[bold realm]᛭ Tokens de la sesion[/]\n")
        render_token_usage(self.session.total_usage)
        console.print()


class UsageCommand(BaseCommand):
    """Show detailed session statistics: tokens, cost, tools, messages, duration."""

    name = "usage"
    description = "Mostrar estadísticas detalladas de la sesión"
    aliases = ["stats", "u"]

    async def execute(self, args: str) -> None:
        from .providers import estimate_cost

        usage = self.session.total_usage
        model = self.session.config.model
        total_cost = estimate_cost(
            model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        tool_counts = self.session.tool_call_counts
        msg_counts = self.session.message_counts
        duration = self.session.session_duration()
        duration_str = self.session._format_duration(duration)
        start_time = self.session.session_start.strftime("%Y-%m-%d %H:%M:%S")

        if args.strip().lower() == "json":
            data = {
                "tokens": {
                    "prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                },
                "cost": {
                    "total_usd": round(total_cost, 6),
                    "per_model": self.session.per_model_usage,
                },
                "tool_calls": tool_counts,
                "messages": msg_counts,
                "session": {
                    "start_time": self.session.session_start.isoformat(),
                    "duration_seconds": duration,
                    "duration_human": duration_str,
                },
            }
            console.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            return

        console.print("\n[bold realm]᛭ Estadísticas de la sesión[/]\n")

        # Tokens
        console.print("[bold frost]Tokens[/]")
        console.print(f"  Prompt:       {usage.get('prompt_tokens', 0)}")
        console.print(f"  Completion:   {usage.get('completion_tokens', 0)}")
        console.print(f"  Total:        {usage.get('total_tokens', 0)}")
        console.print()

        # Cost
        console.print("[bold frost]Costo[/]")
        console.print(f"  Estimado:     ${total_cost:.4f} USD")
        if len(self.session.per_model_usage) > 1:
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
            for m, stats in sorted(self.session.per_model_usage.items()):
                table.add_row(
                    m,
                    str(stats.get("prompt_tokens", 0)),
                    str(stats.get("completion_tokens", 0)),
                    str(stats.get("total_tokens", 0)),
                    f"${stats.get('cost', 0.0):.4f}",
                )
            console.print(table)
        console.print()

        # Tool calls
        console.print("[bold frost]Llamadas a herramientas[/]")
        if tool_counts:
            for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                console.print(f"  {name}: {count}")
        else:
            console.print("  (ninguna)")
        console.print()

        # Messages
        console.print("[bold frost]Mensajes[/]")
        console.print(f"  Usuario:      {msg_counts.get('user', 0)}")
        console.print(f"  Asistente:    {msg_counts.get('assistant', 0)}")
        console.print(f"  Herramienta:  {msg_counts.get('tool', 0)}")
        console.print(f"  Total:        {sum(msg_counts.values())}")
        console.print()

        # Session
        console.print("[bold frost]Sesión[/]")
        console.print(f"  Inicio:       {start_time}")
        console.print(f"  Duración:     {duration_str}")
        console.print()


class MetricsCommand(BaseCommand):
    """Show aggregate session metrics: tool usage, commands, file edits, tokens.

    ``/metrics`` shows a summary of everything.
    ``/metrics tools`` breaks down tool calls by name with average duration.
    ``/metrics commands`` shows most-used slash commands.
    ``/metrics files`` shows most-edited files.
    """

    name = "metrics"
    description = "Mostrar métricas agregadas de la sesión"
    aliases = ["mtr"]

    async def execute(self, args: str) -> None:
        subcmd = args.strip().lower()
        if subcmd == "json":
            await self._emit_json()
            return

        if not subcmd or subcmd == "all":
            await self._show_summary()
            return

        if subcmd == "tools":
            await self._show_tools()
            return

        if subcmd == "commands":
            await self._show_commands()
            return

        if subcmd == "files":
            await self._show_files()
            return

        render_error(
            "Uso: /metrics [tools|commands|files|json|all] — muestra métricas de la sesión",
        )

    def _tool_metrics(self) -> tuple[dict[str, int], dict[str, float], int]:
        history = getattr(self.session, "_tool_call_history", [])
        counts: dict[str, int] = {}
        durations: dict[str, list[float]] = {}
        total = 0
        for entry in history:
            name = entry.get("name")
            if not name:
                continue
            total += 1
            counts[name] = counts.get(name, 0) + 1
            durations.setdefault(name, []).append(entry.get("duration", 0.0))
        avg: dict[str, float] = {
            name: sum(durs) / len(durs) if durs else 0.0
            for name, durs in durations.items()
        }
        return counts, avg, total

    def _command_metrics(self) -> dict[str, int]:
        history = getattr(self.session, "_command_history", [])
        counts: dict[str, int] = {}
        for entry in history:
            name = entry.get("name")
            if name:
                counts[name] = counts.get(name, 0) + 1
        return counts

    def _file_edit_metrics(self) -> dict[str, int]:
        history = getattr(self.session, "_file_edit_history", [])
        counts: dict[str, int] = {}
        for entry in history:
            path = entry.get("path")
            if path:
                counts[path] = counts.get(path, 0) + 1
        return counts

    async def _show_summary(self) -> None:
        console.print("\n[bold realm]᛭ Métricas de la sesión[/]\n")

        # Tokens over time.
        usage = self.session.total_usage
        console.print("[bold frost]Tokens acumulados[/]")
        console.print(f"  Prompt:       {usage.get('prompt_tokens', 0)}")
        console.print(f"  Completion:   {usage.get('completion_tokens', 0)}")
        console.print(f"  Total:        {usage.get('total_tokens', 0)}")
        console.print()

        # Tool calls.
        counts, avg, total = self._tool_metrics()
        console.print("[bold frost]Llamadas a herramientas[/]")
        console.print(f"  Total:        {total}")
        if counts:
            for name, count in sorted(counts.items(), key=lambda x: -x[1])[:5]:
                console.print(f"  {name}: {count} (avg {avg[name]:.3f}s)")
        else:
            console.print("  (ninguna)")
        console.print()

        # Commands.
        cmd_counts = self._command_metrics()
        console.print("[bold frost]Comandos de barra[/]")
        if cmd_counts:
            for name, count in sorted(cmd_counts.items(), key=lambda x: -x[1])[:5]:
                console.print(f"  /{name}: {count}")
        else:
            console.print("  (ninguno)")
        console.print()

        # File edits.
        file_counts = self._file_edit_metrics()
        console.print("[bold frost]Archivos editados[/]")
        if file_counts:
            for path, count in sorted(file_counts.items(), key=lambda x: -x[1])[:5]:
                console.print(f"  {path}: {count}")
        else:
            console.print("  (ninguno)")
        console.print()

    async def _show_tools(self) -> None:
        counts, avg, total = self._tool_metrics()
        console.print("\n[bold realm]᛭ Métricas de herramientas[/]\n")
        console.print(f"[bold frost]Total de llamadas:[/] {total}")
        if not counts:
            console.print("  (ninguna)")
            console.print()
            return

        table = Table(
            show_header=True,
            header_style="bold",
            border_style="dim",
            expand=False,
        )
        table.add_column("Herramienta", style="tool.name")
        table.add_column("Llamadas", justify="right")
        table.add_column("Duración promedio", justify="right")
        for name, count in sorted(counts.items(), key=lambda x: -x[1]):
            table.add_row(name, str(count), f"{avg[name]:.3f}s")
        console.print(table)
        console.print()

    async def _show_commands(self) -> None:
        counts = self._command_metrics()
        console.print("\n[bold realm]᛭ Métricas de comandos[/]\n")
        if not counts:
            console.print("  (ninguno)")
            console.print()
            return

        table = Table(
            show_header=True,
            header_style="bold",
            border_style="dim",
            expand=False,
        )
        table.add_column("Comando", style="tool.name")
        table.add_column("Usos", justify="right")
        for name, count in sorted(counts.items(), key=lambda x: -x[1]):
            table.add_row(f"/{name}", str(count))
        console.print(table)
        console.print()

    async def _show_files(self) -> None:
        counts = self._file_edit_metrics()
        console.print("\n[bold realm]᛭ Métricas de archivos editados[/]\n")
        if not counts:
            console.print("  (ninguno)")
            console.print()
            return

        table = Table(
            show_header=True,
            header_style="bold",
            border_style="dim",
            expand=False,
        )
        table.add_column("Archivo", style="tool.name")
        table.add_column("Ediciones", justify="right")
        for path, count in sorted(counts.items(), key=lambda x: -x[1]):
            table.add_row(path, str(count))
        console.print(table)
        console.print()

    async def _emit_json(self) -> None:
        counts, avg, total = self._tool_metrics()
        data = {
            "tokens": dict(self.session.total_usage),
            "tools": {
                "total": total,
                "counts": counts,
                "average_duration": avg,
            },
            "commands": self._command_metrics(),
            "files": self._file_edit_metrics(),
            "session": {
                "start_time": self.session.session_start.isoformat(),
                "duration_seconds": self.session.session_duration(),
            },
        }
        console.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


class PlanCommand(BaseCommand):
    """Generate a numbered plan from a goal and show it as a checklist.

    ``/plan <goal>`` — asks Lilith for a step-by-step plan and renders it
    as a Rich panel. The plan is parsed with ``lilith_cli.plan.parse_plan``
    and stored in the session so the user can track progress.

    ``/plan show`` — re-render the current plan.
    ``/plan done <n>`` — mark step ``n`` as completed.
    ``/plan reset`` — clear all "done" markers.
    """

    name = "plan"
    description = "Crear un plan numerado a partir de un objetivo"
    aliases = ["todo"]

    async def execute(self, args: str) -> None:
        from .plan import build_planning_prompt, parse_plan
        from .render import render_plan

        text = args.strip()
        if not text:
            console.print(
                "[dim]Uso: /plan <objetivo>  ·  /plan show  ·  /plan done <n>  ·  /plan reset[/]"
            )
            return

        parts = text.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        # State-management subcommands.
        current_plan = getattr(self.session, "current_plan", None)
        if sub == "show":
            if current_plan is None:
                console.print("[dim]No hay un plan activo. Usa /plan <objetivo>.[/]")
                return
            render_plan(current_plan)
            return
        if sub == "reset":
            if current_plan is not None:
                current_plan.reset()
                console.print("[success]✓ Plan reseteado.[/]")
            return
        if sub == "done":
            try:
                n = int(rest.strip())
            except ValueError:
                render_error("Uso: /plan done <numero>")
                return
            if current_plan is None or not current_plan.mark_done(n):
                render_error(f"Paso {n} no encontrado en el plan actual.")
                return
            console.print(f"[success]✓ Paso {n} marcado como completado.[/]")
            render_plan(current_plan)
            return

        # Default: treat the whole args as the goal and ask the LLM for a plan.
        goal = text
        prompt = build_planning_prompt(goal)
        try:
            response = await self.session.provider.complete(
                [{"role": "user", "content": prompt}],
                tools=None,
            )
        except Exception as exc:
            render_error(f"No se pudo generar el plan: {exc}")
            return

        content = response.get("content", "")
        if not content:
            render_error("El LLM no devolvió un plan. Probá reformular el objetivo.")
            return

        plan = parse_plan(content)
        plan.goal = goal
        self.session.current_plan = plan

        console.print(
            f"\n[bold realm]᛭ Plan para:[/] [info]{goal}[/]\n"
        )
        render_plan(plan)
        console.print(
            "\n[dim]Marcá pasos completados con /plan done <n>.[/]"
        )


class StatusCommand(BaseCommand):
    name = "status"
    description = "Estado del ecosistema Yggdrasil"

    async def execute(self, _args: str) -> None:
        # Try importing from ygg (hub CLI) for realm status.
        try:
            # Add root to sys.path if needed.
            from lilith_cli.main import _resolve_yggdrasil_root

            root = str(_resolve_yggdrasil_root())
            if root not in sys.path:
                sys.path.insert(0, root)
            from ygg import (
                REALMS,
                SERVICES,
                YGGDRASIL_ROOT,
                get_service_status,
            )

            realm_data: dict[str, Any] = {}
            for realm in REALMS:
                rdf = YGGDRASIL_ROOT / realm
                if rdf.exists():
                    projects = [
                        d for d in rdf.iterdir() if d.is_dir() and not d.name.startswith(".")
                    ]
                    realm_data[realm] = {"exists": True, "projects": len(projects)}
                else:
                    realm_data[realm] = {"exists": False, "projects": 0}

            status_dict: dict[str, Any] = {
                "Modelo": self.session.config.model,
                "Proveedor": self.session.config.provider,
                "Memoria": self.session.memory is not None,
            }
            # Add service statuses.
            for key in SERVICES:
                svc = get_service_status(key)
                status_dict[f"{svc['emoji']} {svc['name']}"] = svc.get("running", False)

            # Add realm info.
            for realm, data in realm_data.items():
                status_dict[f"Realm: {realm}"] = (
                    data.get("projects", 0) if data["exists"] else "NO EXISTE"
                )

            render_status(status_dict)
        except ImportError:
            # Fallback: just show local status.
            render_status(
                {
                    "Modelo": self.session.config.model,
                    "Proveedor": self.session.config.provider,
                    "Memoria": self.session.memory is not None,
                    "Herramientas": len(self.session.get_tool_descriptions()),
                },
            )


class BifrostCommand(BaseCommand):
    name = "bifrost"
    description = "Estado de Bifrost IPC (comunicación entre agentes)"
    aliases = ["bifrost", "ipc"]

    async def execute(self, args: str) -> None:
        """Show Bifrost IPC status and optionally send a message."""
        try:
            from lilith_cli.main import _resolve_yggdrasil_root

            root = _resolve_yggdrasil_root()
            bifrost_path = root / "Vanaheim" / "bifrost" / "bifrost" / "ipc.py"
            if not bifrost_path.exists():
                render_error("Bifrost IPC no encontrado en Vanaheim/bifrost/bifrost/ipc.py")
                return

            # Try to load BifrostIPC
            import sys

            sys.path.insert(0, str(root / "Vanaheim" / "bifrost"))
            from bifrost.bifrost.ipc import BifrostIPC

            ipc = BifrostIPC(root=root / ".bifrost")
            inbox_count = 0
            outbox_count = 0
            inbox_path = ipc._root / "inbox"
            outbox_path = ipc._root / "outbox"

            if inbox_path.exists():
                inbox_count = len(list(inbox_path.iterdir()))
            if outbox_path.exists():
                outbox_count = len(list(outbox_path.iterdir()))

            console.print("\n[bold realm]᛭ Bifrost IPC — Estado[/]\n")
            console.print(f"  [cyan]Raíz:[/] {ipc._root}")
            console.print(f"  [cyan]Mensajes pendientes (inbox):[/] {inbox_count}")
            console.print(f"  [cyan]Mensajes enviados (outbox):[/] {outbox_count}")
            console.print(f"  [cyan]Archivo de historial:[/] {ipc._history}")
            console.print()

        except ImportError as e:
            render_error(f"No se pudo importar Bifrost IPC: {e}")
        except Exception as e:
            render_error(f"Error al obtener estado de Bifrost: {e}")


class ConfigCommand(BaseCommand):
    name = "config"
    description = "Mostrar configuración actual"

    async def execute(self, _args: str) -> None:
        data = self.session.config.model_dump()
        # Mask API keys.
        if data.get("api_key"):
            key = data["api_key"]
            data["api_key"] = key[:8] + "…" + key[-4:] if len(key) > 12 else "***"

        console.print(
            Panel(
                Syntax(
                    json.dumps(data, indent=2, ensure_ascii=False, default=str),
                    "json",
                    theme="monokai",
                ),
                title="[bold realm]⚙ Configuración[/]",
                border_style="gold1",
                expand=False,
            ),
        )


class DiffConfigCommand(BaseCommand):
    """Compare global and project configuration values.

    ``/diff-config`` shows all keys side by side; ``/diff-config only-different``
    restricts the table to keys whose effective value differs from the global
    one (i.e., keys overridden by the project config).
    """

    name = "diff-config"
    description = "Mostrar diferencias entre configuración global y del proyecto"
    aliases = ["diffconfig", "dcfg"]

    async def execute(self, args: str) -> None:
        from .config import CONFIG_FILE, find_project_config, load_config

        global_path = getattr(self.session, "config_path", None) or CONFIG_FILE
        project_path = find_project_config()

        global_cfg = load_config(global_path)
        project_cfg = load_config(project_path) if project_path is not None else None

        only_different = args.strip().lower() in ("only-different", "solo-diferencias", "diff")

        table = Table(
            title="[bold realm]⚙ Diferencias de configuración[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="cyan",
            expand=False,
        )
        table.add_column("Clave", style="tool.name")
        table.add_column("Global", style="tool.result")
        table.add_column("Proyecto", style="tool.result")
        table.add_column("Efectiva", style="bold green")

        rows_added = 0
        for key in self._flatten_keys(global_cfg.model_dump()):
            global_value = self._get_dotted(global_cfg, key)
            project_value = self._get_dotted(project_cfg, key) if project_cfg is not None else "(no definido)"
            effective_value = project_value if project_cfg is not None and project_value != "(no definido)" else global_value

            if only_different and self._same(global_value, effective_value):
                continue

            table.add_row(
                key,
                self._fmt(global_value),
                self._fmt(project_value),
                self._fmt(effective_value),
            )
            rows_added += 1

        if project_path is None and only_different:
            console.print("[info]No hay configuración de proyecto; nada que comparar.[/]")
            return

        if rows_added == 0:
            console.print("[info]No hay diferencias entre la configuración global y la del proyecto.[/]")
            return

        console.print(table)

    @staticmethod
    def _flatten_keys(data: dict[str, Any], prefix: str = "") -> list[str]:
        """Return all dotted keys from a nested dict, sorted."""
        keys: list[str] = []
        for key, value in sorted(data.items()):
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                keys.extend(DiffConfigCommand._flatten_keys(value, full))
            else:
                keys.append(full)
        return keys

    @staticmethod
    def _get_dotted(obj: Any, key: str) -> Any:
        """Return the value at *key* (dot notation) or a sentinel."""
        parts = key.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return "(no definido)"
                current = current[part]
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return "(no definido)"
        return current

    @staticmethod
    def _fmt(value: Any) -> str:
        """Format a config value for display."""
        if isinstance(value, str) and value.startswith("$"):
            # Preserve env placeholders, mask resolved values that look like secrets.
            env_val = value
            if len(env_val) > 12:
                return env_val[:8] + "…" + env_val[-4:]
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    @staticmethod
    def _same(a: Any, b: Any) -> bool:
        """Compare values after normalising to JSON-serialisable strings."""
        return json.dumps(a, sort_keys=True, ensure_ascii=False, default=str) == json.dumps(
            b, sort_keys=True, ensure_ascii=False, default=str
        )


class QuitCommand(BaseCommand):
    name = "quit"
    description = "Salir del agente"
    aliases = ["exit", "q"]

    async def execute(self, _args: str) -> None:
        console.print("[dim]Odin te guíe. Hasta la próxima.[/]")
        raise SystemExit(0)


class SaveCommand(BaseCommand):
    name = "save"
    description = "Guardar la conversación a un archivo"

    async def execute(self, _args: str) -> None:
        from .repl import _auto_save_conversation

        filepath = _auto_save_conversation(self.session)
        if filepath:
            console.print(f"[success]✓ Conversación guardada en: {filepath.resolve()}[/]")
        else:
            render_error("No hay mensajes para guardar.")


# ── New QoL commands (inspired by Hermes Agent) ───────────────────────


class RedoCommand(BaseCommand):
    """Re-send the last user message (like Hermes /retry)."""

    name = "redo"
    description = "Reenviar el último mensaje al modelo"
    aliases = ["r"]

    async def execute(self, _args: str) -> None:
        last_msg = getattr(self.session, "_last_user_message", "") or ""
        if not last_msg:
            render_error("No hay mensaje anterior para reenviar.")
            return

        # Pop last user message from history if it matches.
        if (
            self.session.history
            and self.session.history[-1].get("role") == "user"
            and self.session.history[-1].get("content", "") == last_msg
        ):
            self.session.history.pop()

        # Pop any trailing assistant messages too.
        while self.session.history and self.session.history[-1].get("role") == "assistant":
            self.session.history.pop()

        console.print(
            f"[dim]⟳ Reenviando: [model]{last_msg[:80]}{'…' if len(last_msg) > 80 else ''}[/][/]",
        )
        # Import here to avoid circular imports.
        from .repl import _process_with_streaming
        from .render import render_turn_start

        render_turn_start(999)  # We don't track turn number in commands — use placeholder
        await _process_with_streaming(self.session, last_msg)


class RetryCommand(BaseCommand):
    """Re-send the last user message, optionally with a replacement text.

    ``/retry`` re-sends the exact last user message.  ``/retry <text>``
    sends a new message instead, keeping the same conversational context.
    """

    name = "retry"
    description = "Reenviar el último mensaje o enviar uno nuevo"
    aliases = ["reintentar"]

    async def execute(self, args: str) -> None:
        text = args.strip()
        if not text:
            text = getattr(self.session, "_last_user_message", "") or ""
        if not text:
            render_error("No hay mensaje anterior para reenviar.")
            return

        # Pop last user message from history if it matches.
        if (
            self.session.history
            and self.session.history[-1].get("role") == "user"
            and self.session.history[-1].get("content", "") == self.session._last_user_message
        ):
            self.session.history.pop()

        # Pop any trailing assistant messages too.
        while self.session.history and self.session.history[-1].get("role") == "assistant":
            self.session.history.pop()

        console.print(
            f"[dim]⟳ Reenviando: [model]{text[:80]}{'…' if len(text) > 80 else ''}[/][/]",
        )
        # Import here to avoid circular imports.
        from .repl import _process_with_streaming
        from .render import render_turn_start

        render_turn_start(999)  # We don't track turn number in commands — use placeholder
        await _process_with_streaming(self.session, text)


class ContinueCommand(BaseCommand):
    """Prompt the LLM to continue its previous response."""

    name = "continue"
    description = "Continuar la respuesta anterior"
    aliases = ["cont", "c"]

    async def execute(self, _args: str) -> None:
        prompt = "Continuá por favor."
        self.session.history.append({"role": "user", "content": prompt})
        self.session._last_user_message = prompt

        console.print("[dim]⟳ Pidiendo continuación...[/]")
        # Import here to avoid circular imports.
        from .repl import _process_with_streaming
        from .render import render_turn_start

        render_turn_start(999)
        await _process_with_streaming(self.session, prompt)


class CopyCommand(BaseCommand):
    """Copy the last assistant response to clipboard (like Hermes /copy)."""

    name = "copy"
    description = "Copiar última respuesta al portapapeles"
    aliases = ["cp"]

    async def execute(self, args: str) -> None:
        from .repl import _copy_to_clipboard

        # Find assistant messages.
        assistant_msgs = [
            m for m in self.session.history if m.get("role") == "assistant" and m.get("content")
        ]
        if not assistant_msgs:
            render_error("No hay respuesta para copiar.")
            return

        # Support /copy <n> for Nth response (1-based).
        idx = -1  # Default: last
        if args.strip():
            try:
                n = int(args.strip())
                idx = n - 1  # Convert to 0-based
                if idx < 0 or idx >= len(assistant_msgs):
                    render_error(f"Índice fuera de rango. Hay {len(assistant_msgs)} respuestas.")
                    return
            except ValueError:
                render_error(
                    "Uso: /copy [número]  — donde número es el índice de respuesta (1-based)",
                )
                return

        text = assistant_msgs[idx].get("content", "")
        # Strip reasoning tags for clean copy.
        import re

        text = re.sub(
            r"<(?:reasoning|thinking|thought)>.*?</(?:reasoning|thinking|thought)>",
            "",
            text,
            flags=re.DOTALL,
        ).strip()

        if _copy_to_clipboard(text):
            preview = text[:60] + "…" if len(text) > 60 else text
            console.print(f"[success]✓ Copiado al portapapeles: [dim]{preview}[/]")
        else:
            render_error("No se pudo copiar al portapapeles. Intenta instalar xclip o wl-paste.")


class SystemCommand(BaseCommand):
    """Show or modify the system prompt (like Hermes /system)."""

    name = "system"
    description = "Mostrar o modificar el system prompt"

    async def execute(self, args: str) -> None:
        if not args.strip():
            # Show current system prompt.
            sp = self.session.system_prompt or "(sin system prompt)"
            console.print(
                Panel(
                    sp,
                    title="[bold realm]⚙ System Prompt[/]",
                    border_style="gold1",
                    expand=False,
                    padding=(0, 1),
                ),
            )
        else:
            # Set new system prompt.
            self.session.system_prompt = args.strip()
            console.print("[success]✓ System prompt actualizado.[/]")


# ── Default prompt templates ─────────────────────────────────────────

_DEFAULT_TEMPLATES: dict[str, str] = {
    "review-pr": "Review the changes in this branch for code quality, security, and best practices",
    "explain-code": "Explain what this code does in plain language",
    "find-bugs": "Look for potential bugs in this code, including edge cases",
    "refactor": "Suggest refactoring improvements for this code",
}

_TEMPLATE_FILE: Path = Path.home() / ".yggdrasil" / "templates.json"


class TemplateCommand(BaseCommand):
    """Manage and recall prompt templates.

    ``/template list`` — list all templates
    ``/template show <name>`` — show a template
    ``/template save <name> <content>`` — save a template
    ``/template use <name>`` — load and send the template as a prompt
    ``/template delete <name>`` — delete a template
    """

    name = "template"
    description = "Gestionar y usar plantillas de prompt"
    aliases = ["templates", "tpl"]

    @classmethod
    def _templates_path(cls) -> Path:
        return _TEMPLATE_FILE

    @classmethod
    def _load_templates(cls) -> dict[str, str]:
        path = cls._templates_path()
        if not path.exists():
            return dict(_DEFAULT_TEMPLATES)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                templates = {str(k): str(v) for k, v in data.items()}
                # Ensure built-in defaults exist even if the file omitted them.
                for default_name, default_value in _DEFAULT_TEMPLATES.items():
                    templates.setdefault(default_name, default_value)
                return templates
        except Exception as exc:
            logger = logging.getLogger(__name__)
            logger.warning("No se pudieron cargar plantillas: %s", exc)
        return dict(_DEFAULT_TEMPLATES)

    @classmethod
    def _save_templates(cls, templates: dict[str, str]) -> None:
        path = cls._templates_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(templates, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    async def execute(self, args: str) -> None:
        text = args.strip()
        if not text or text.lower() in ("list", "ls"):
            await self._list()
            return

        parts = text.split(maxsplit=1)
        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "show":
            await self._show(rest)
        elif subcmd == "save":
            await self._save(rest)
        elif subcmd == "use":
            await self._use(rest)
        elif subcmd in ("delete", "rm", "del"):
            await self._delete(rest)
        else:
            render_error(
                "Uso: /template [list|show <name>|save <name> <content>|use <name>|delete <name>]"
            )

    async def _list(self) -> None:
        templates = self._load_templates()
        if not templates:
            console.print("[dim]No hay plantillas guardadas.[/]")
            return

        console.print("\n[bold realm]᛭ Plantillas de prompt[/]\n")
        for name in sorted(templates):
            preview = templates[name]
            if len(preview) > 70:
                preview = preview[:70] + "…"
            default_mark = " [dim](predefinida)[/]" if name in _DEFAULT_TEMPLATES else ""
            console.print(f"  [bold cyan]{name}[/]{default_mark}: [dim]{preview}[/]")
        console.print()

    async def _show(self, name: str) -> None:
        templates = self._load_templates()
        if name not in templates:
            render_error(f"Plantilla no encontrada: [model]{name}[/]")
            return

        console.print(
            Panel(
                templates[name],
                title=f"[bold realm]᛭ Plantilla: {name}[/]",
                border_style="frost",
                expand=False,
                padding=(0, 1),
            ),
        )

    async def _save(self, rest: str) -> None:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            render_error("Uso: /template save <name> <content>")
            return

        name, content = parts[0], parts[1].strip()
        if not content:
            render_error("El contenido de la plantilla no puede estar vacío.")
            return

        templates = self._load_templates()
        templates[name] = content
        self._save_templates(templates)
        console.print(f"[success]✓ Plantilla guardada: [model]{name}[/]")

    async def _use(self, name: str) -> None:
        templates = self._load_templates()
        if name not in templates:
            render_error(f"Plantilla no encontrada: [model]{name}[/]")
            return

        prompt = templates[name]
        # Place the template content as a user message and send it through the agent.
        # The provider may not be available in stub sessions; catch that gracefully.
        try:
            self.session.history.append({"role": "user", "content": prompt})
            self.session._last_user_message = prompt  # noqa: SLF001
        except Exception as exc:
            render_error(f"No se pudo cargar la plantilla: {exc}")
            return

        console.print(f"[success]✓ Plantilla cargada: [model]{name}[/]")
        console.print(f"[dim]{prompt}[/]")

        # If the session has a process_message_stream method, prefer streaming; otherwise fall back.
        try:
            if hasattr(self.session, "process_message_stream"):
                await self.session.process_message_stream(prompt)
            elif hasattr(self.session, "process_message"):
                await self.session.process_message(prompt)
        except Exception as exc:
            render_error(f"Error al enviar el prompt: {exc}")

    async def _delete(self, name: str) -> None:
        templates = self._load_templates()
        if name not in templates:
            render_error(f"Plantilla no encontrada: [model]{name}[/]")
            return

        del templates[name]
        self._save_templates(templates)
        console.print(f"[warning]✗ Plantilla eliminada: [model]{name}[/]")


class InitCommand(BaseCommand):
    """Create a project-local .lilith/CLAUDE.md instructions file.

    ``/init [path]`` creates a ``.lilith/`` directory with a CLAUDE.md
    template at the requested path (current working directory by default).
    The contents are auto-injected into the system prompt as project
    instructions.
    """

    name = "init"
    description = "Crear archivo de instrucciones .lilith/CLAUDE.md para el proyecto"

    # File markers used to detect project types.
    PROJECT_MARKERS: dict[str, list[str]] = {
        "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
        "node": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
        "go": ["go.mod", "go.sum"],
        "rust": ["Cargo.toml", "Cargo.lock"],
    }

    async def execute(self, args: str) -> None:
        target = Path(args.strip() or ".")
        target = target.expanduser().resolve()
        lilith_dir = target / ".lilith"
        claude_md = lilith_dir / "CLAUDE.md"

        if claude_md.exists():
            render_error(f"Ya existe {claude_md}. Usá /init en un directorio distinto o editá el archivo existente.")
            return

        project_type = self._detect_project_type(target)
        content = self._build_template(target, project_type)

        # Confirmation panel.
        console.print(
            Panel(
                content,
                title=f"[bold realm]᛭ Crear {claude_md}[/]",
                subtitle=f"[dim]Tipo detectado: {project_type or 'genérico'}[/]",
                border_style="frost",
                expand=False,
                padding=(0, 1),
            ),
        )
        console.print("[dim]Escribiendo instrucciones del proyecto…[/]")

        # Recreate target after the patched cwd context may have changed it.
        target = Path(args.strip() or ".")
        target = target.expanduser().resolve()
        lilith_dir = target / ".lilith"
        claude_md = lilith_dir / "CLAUDE.md"
        lilith_dir.mkdir(parents=True, exist_ok=True)
        claude_md.write_text(content, encoding="utf-8")
        console.print(f"[success]✓ Instrucciones del proyecto creadas: {claude_md}[/]")

    def _detect_project_type(self, target: Path) -> str | None:
        """Detect project language/type based on common file markers."""
        for project_type, markers in self.PROJECT_MARKERS.items():
            if any((target / marker).exists() for marker in markers):
                return project_type
        return None

    def _build_template(self, target: Path, project_type: str | None) -> str:
        """Build a CLAUDE.md template tailored to the detected project type."""
        commands = self._project_commands(project_type)

        lines = [
            "# Instrucciones del proyecto",
            "",
            f"Proyecto: {target.name}",
            "",
            "## Project Overview",
            "",
            "Escribe aquí una descripción breve del propósito del proyecto,",
            "su arquitectura general y cualquier convención importante que",
            "deba respetar al trabajar en él.",
            "",
            "## Build Commands",
            "",
        ]
        for key, value in commands:
            lines.append(f"- {key}: {value}")
        lines.extend([
            "",
            "## Code Style",
            "",
            "- Mantené las funciones pequeñas y enfocadas.",
            "- Usá nombres descriptivos en variables y funciones.",
            "- Preferí claridad sobre optimización prematura.",
            "",
            "## Testing",
            "",
        ])
        if project_type == "python":
            lines.append("- Ejecutá los tests con `pytest` antes de finalizar cambios.")
        elif project_type == "node":
            lines.append("- Ejecutá los tests con `npm test` antes de finalizar cambios.")
        elif project_type == "go":
            lines.append("- Ejecutá `go test ./...` antes de finalizar cambios.")
        elif project_type == "rust":
            lines.append("- Ejecutá `cargo test` antes de finalizar cambios.")
        else:
            lines.append("- Ejecutá el comando de tests del proyecto antes de finalizar cambios.")
        lines.extend([
            "- Agregá tests para las funcionalidades nuevas.",
            "- No rompas los tests existentes salvo que sea intencional y esté documentado.",
            "",
        ])
        return "\n".join(lines)

    def _project_commands(self, project_type: str | None) -> list[tuple[str, str]]:
        """Return build/test/lint commands for a given project type."""
        if project_type == "python":
            return [
                ("Instalar dependencias", "pip install -e ."),
                ("Ejecutar tests", "pytest"),
                ("Formatear", "ruff format ."),
                ("Lint", "ruff check ."),
            ]
        if project_type == "node":
            return [
                ("Instalar dependencias", "npm install"),
                ("Ejecutar tests", "npm test"),
                ("Lint", "npm run lint"),
                ("Build", "npm run build"),
            ]
        if project_type == "go":
            return [
                ("Descargar dependencias", "go mod download"),
                ("Ejecutar tests", "go test ./..."),
                ("Build", "go build ./..."),
                ("Lint", "golangci-lint run"),
            ]
        if project_type == "rust":
            return [
                ("Build", "cargo build"),
                ("Ejecutar tests", "cargo test"),
                ("Lint", "cargo clippy"),
                ("Formatear", "cargo fmt"),
            ]
        return [
            ("Build", "<comando de build>"),
            ("Ejecutar tests", "<comando de tests>"),
            ("Lint", "<comando de lint>"),
        ]


class HistoryCommand(BaseCommand):
    """Show conversation history (like Hermes /history)."""

    name = "history"
    description = "Mostrar historial de conversación"
    aliases = ["hist"]

    async def execute(self, args: str) -> None:
        if not self.session.history:
            console.print("[dim]El historial está vacío.[/]")
            return

        # Parse optional limit.
        limit = len(self.session.history)
        if args.strip():
            with contextlib.suppress(ValueError):
                limit = min(int(args.strip()), len(self.session.history))

        # Show the last N messages.
        messages = self.session.history[-limit:]
        console.print(f"\n[bold realm]᛭ Historial ({len(messages)} mensajes)[/]\n")

        for i, msg in enumerate(messages, 1):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            role_styles = {
                "user": ("bold gold1", "᛭ Tú"),
                "assistant": ("bold cyan", "✦ Asistente"),
                "system": ("dim italic", "⚙ Sistema"),
                "tool": ("bold green", "⟡ Herramienta"),
            }
            style, label = role_styles.get(role, ("dim", role))

            # Truncate long messages.
            display = content[:200] + "…" if len(content) > 200 else content
            console.print(f"  [{style}]{i}. {label}[/]  [dim]{display}[/]")

        console.print()


class CompactCommand(BaseCommand):
    """Compress conversation history by summarizing older messages.

    Like Hermes Agent's /compact — asks the LLM to summarize the
    conversation so far, then replaces older messages with the summary
    while keeping recent exchanges intact. Frees up context tokens.
    """

    name = "compact"
    description = "Comprimir historial resumiendo mensajes antiguos"
    aliases = ["summarize"]

    async def execute(self, args: str) -> None:
        history = self.session.history
        if not history:
            render_error("El historial está vacío — no hay nada que compactar.")
            return

        # Parse optional keep_recent count.
        keep_recent = 2
        if args.strip():
            try:
                keep_recent = max(1, int(args.strip()))
            except ValueError:
                render_error(
                    "Uso: /compact [n]  — donde n es el número de "
                    "turnos recientes a conservar (default: 2)",
                )
                return

        before = len(history)
        console.print(f"[dim]Compactando {before} mensajes…[/]")

        try:
            # Generate summary from LLM with a thinking spinner.
            from .render import Timer, make_thinking_spinner

            timer = Timer()
            timer.__enter__()
            spinner_info = make_thinking_spinner()
            spinner_info["set_label"]("Resumiendo historial")
            spinner_info["status"].__enter__()
            try:
                summary = await self.session.generate_compact_summary()
            finally:
                spinner_info["status"].__exit__(None, None, None)
            timer.__exit__(None, None, None)

            if not summary:
                render_error("No se pudo generar el resumen. Intenta de nuevo.")
                return

            # Compact the history.
            self.session.compact_history(summary, keep_recent=keep_recent)
            after = len(self.session.history)

            # Show result.
            console.print()
            console.print(
                Panel(
                    summary[:500] + ("…" if len(summary) > 500 else ""),
                    title="[bold realm]᛭ Historial Compactado[/]",
                    border_style="frost",
                    expand=False,
                    padding=(0, 1),
                ),
            )
            console.print(
                f"[success]✓ {before} mensajes → {after} "
                f"(1 resumen + {keep_recent} turnos recientes)[/]",
            )
            console.print(f"[dim]{timer.elapsed:.1f}s para generar resumen[/]")

        except Exception as exc:
            render_error(f"Error al compactar: {exc}")


class ResumeCommand(BaseCommand):
    """Resume a previously saved conversation.

    Lists saved conversations from ``~/.yggdrasil/conversations/`` and
    lets the user pick one to restore into the current session history.
    Supports selecting by index (``/resume 3``) or searching by name.
    Without arguments, shows the list of recent conversations.
    """

    name = "resume"
    description = "Reanudar una conversación guardada"
    aliases = ["load"]

    async def execute(self, args: str) -> None:
        from .repl import _list_saved_conversations, _load_conversation

        conversations = _list_saved_conversations()

        if not conversations:
            render_error("No hay conversaciones guardadas en ~/.yggdrasil/conversations/")
            return

        # ── No args: show list ───────────────────────────────────
        if not args.strip():
            from rich.table import Table

            table = Table(
                title="[bold gold1]᛭ Conversaciones Guardadas[/]",
                show_lines=False,
                border_style="cyan",
                header_style="bold dim",
            )
            table.add_column("#", style="bold gold1", width=3)
            table.add_column("Fecha", style="bright_white")
            table.add_column("Modelo", style="cyan")
            table.add_column("Mensajes", style="green", justify="right")
            table.add_column("Vista previa", style="dim", max_width=50)

            for i, conv in enumerate(conversations[:15], start=1):
                ts = conv["timestamp"]
                # Format timestamp nicely.
                try:
                    date_str = f"{ts[:4]}-{ts[6:8]}-{ts[4:6]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
                except (ValueError, IndexError):
                    date_str = ts
                table.add_row(
                    str(i),
                    date_str,
                    conv["model"],
                    str(conv["message_count"]),
                    conv["preview"] or "(sin mensajes de usuario)",
                )

            console.print(table)
            console.print("[dim]Usa /resume <número> para reanudar una conversación[/]")
            return

        # ── Select by index or search ────────────────────────────
        selection = args.strip()

        # Try numeric index.
        try:
            idx = int(selection) - 1
            if 0 <= idx < len(conversations):
                conv = conversations[idx]
            else:
                render_error(
                    f"Índice fuera de rango: {selection} (hay {len(conversations)} conversaciones)",
                )
                return
        except ValueError:
            # Search by name/preview substring.
            matches = [
                c
                for c in conversations
                if selection.lower() in c["name"].lower()
                or selection.lower() in c["preview"].lower()
            ]
            if not matches:
                render_error(f"No se encontró ninguna conversación que coincida con '{selection}'")
                return
            if len(matches) > 1:
                render_error(
                    f"Múltiples coincidencias para '{selection}'. "
                    f"Usa /resume <número> para seleccionar una específica.",
                )
                return
            conv = matches[0]

        # ── Load and restore ─────────────────────────────────────
        filepath = conv["file"]
        data = _load_conversation(filepath)
        if data is None:
            return

        messages = data.get("messages", [])
        if not messages:
            render_error("La conversación seleccionada está vacía.")
            return

        # Preserve current system prompt, restore history.
        old_count = len(self.session.history)
        self.session.history = messages

        # Merge usage if available.
        loaded_usage = data.get("usage", {})
        if loaded_usage:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if key in loaded_usage:
                    self.session._total_usage[key] += loaded_usage.get(key, 0)
        for model, model_usage in data.get("per_model_usage", {}).items():
            self.session._ensure_per_model_entry(model)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self.session._per_model_usage[model][key] += model_usage.get(key, 0)
            self.session._per_model_usage[model]["cost"] += model_usage.get("cost", 0.0)

        console.print()
        console.print(
            Panel(
                f"[bright_white]{conv['message_count']} mensajes[/] · "
                f"[cyan]{conv.get('model', '?')}[/] · "
                f"[dim]{conv.get('timestamp', '')}[/]\n\n"
                f"[dim]{conv.get('preview', '(sin preview)')}[/]",
                title="[bold gold1]᛭ Conversación Reanudada[/]",
                border_style="green",
                expand=False,
                padding=(0, 1),
            ),
        )
        console.print(
            f"[success]✓ Restaurados {conv['message_count']} mensajes (antes: {old_count})[/]",
        )


class AgentCommand(BaseCommand):
    """Switch between agent operating modes.

    ``/agent`` shows the current mode.
    ``/agent list`` lists available modes.
    ``/agent <mode>`` switches to a mode and applies its policy settings.

    Modes:
      default      — full capabilities
      plan-first   — always create a plan before destructive work
      review-only  — read/analyze only, no writes
      auto-edit    — skip diff-preview confirmation
    """

    name = "agent"
    description = "Cambiar el modo de operación del agente"
    aliases = ["mode", "modo"]

    async def execute(self, args: str) -> None:
        from .agent_modes import (
            apply_agent_mode,
            get_agent_mode,
            get_current_agent_mode,
            is_valid_agent_mode,
            list_agent_modes,
        )

        text = args.strip()

        if not text or text.lower() in ("current", "now"):
            current = get_current_agent_mode(self.session)
            mode = get_agent_mode(current)
            label = mode.label if mode else current
            console.print(f"[info]Modo actual: [bold]{label}[/] ([model]{current}[/])")
            return

        if text.lower() in ("list", "ls", "help"):
            console.print("\n[bold realm]᛭ Modos de agente disponibles[/]\n")
            current = get_current_agent_mode(self.session)
            for mode in list_agent_modes():
                marker = " ▶" if mode.name == current else ""
                console.print(
                    f"  [bold cyan]{mode.name}{marker}[/] — [dim]{mode.description}[/]"
                )
            console.print("\n[dim]Usa /agent <modo> para cambiar.[/]")
            return

        mode_name = text.lower()
        if not is_valid_agent_mode(mode_name):
            available = ", ".join(m.name for m in list_agent_modes())
            render_error(f"Modo desconocido: {mode_name}. Disponibles: {available}")
            return

        mode = get_agent_mode(mode_name)
        assert mode is not None
        apply_agent_mode(self.session, mode)
        console.print(
            f"[success]✓ Modo cambiado a: [bold]{mode.label}[/] ([model]{mode.name}[/])"
        )


class AutoCommand(BaseCommand):
    """Manage auto-execute mode and pre-approved tool patterns.

    ``/auto on`` enables auto-execute: tool calls that match a pre-approved
    pattern skip the diff-preview confirmation and write directly.
    ``/auto off`` disables it.
    ``/auto list`` shows the current mode and patterns.
    ``/auto add <pattern>`` adds a regex pattern to the auto-approve list.
    ``/auto remove <pattern>`` removes a pattern.
    """

    name = "auto"
    description = "Activar auto-ejecución y patrones pre-aprobados"
    aliases = ["autorun", "autoexec"]

    async def execute(self, args: str) -> None:
        text = args.strip()

        if not text or text.lower() in ("list", "ls", "status"):
            state = "ON" if self.session._auto_execute else "OFF"
            console.print(f"[info]Modo auto-ejecutar: [bold]{state}[/]")
            patterns = self.session._auto_approved_patterns
            if patterns:
                console.print(f"[info]Patrones aprobados ({len(patterns)}):[/]")
                for pat in patterns:
                    console.print(f"  [tool.name]{pat}[/]")
            else:
                console.print("[dim]No hay patrones aprobados.[/]")
            console.print("[dim]Uso: /auto on | off | add <patrón> | remove <patrón>[/]")
            return

        if text.lower() == "on":
            self.session._auto_execute = True
            console.print("[success]✓ auto-ejecutar: ON[/]")
            return

        if text.lower() == "off":
            self.session._auto_execute = False
            console.print("[success]✓ auto-ejecutar: OFF[/]")
            return

        parts = text.split(maxsplit=1)
        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "add":
            if not rest:
                render_error("Uso: /auto add <patrón>")
                return
            self.session._auto_approved_patterns.append(rest)
            console.print(f"[success]✓ Patrón agregado: [tool.name]{rest}[/]")
            return

        if subcmd in ("remove", "rm", "del"):
            if not rest:
                render_error("Uso: /auto remove <patrón>")
                return
            try:
                self.session._auto_approved_patterns.remove(rest)
                console.print(f"[success]✓ Patrón eliminado: [tool.name]{rest}[/]")
            except ValueError:
                render_error(f"No se encontró el patrón: [model]{rest}[/]")
            return

        render_error("Uso: /auto on | off | list | add <patrón> | remove <patrón>")


# ── Registry ────────────────────────────────────────────────────────

# We need a late import for render in ConfigCommand's Panel.

from rich.panel import Panel
from rich.syntax import Syntax


class ThemeCommand(BaseCommand):
    """Switch or preview CLI themes.

    Without arguments, lists available themes with a preview of the
    current selection highlighted.  With a theme name, switches
    immediately — the Rich Console and prompt_toolkit style update
    on the next prompt cycle.
    """

    name = "theme"
    description = "Cambiar o listar temas visuales"
    aliases = ["themes"]

    async def execute(self, args: str) -> None:
        from rich.table import Table

        arg = args.strip().lower()

        # ── Current theme ─────────────────────────────────
        if arg in ("current", "actual"):
            current = get_theme()
            console.print(
                f"[info]Tema actual: [bold]{current.label}[/] ({current.name})[/]"
            )
            console.print(f"[dim]{current.description}[/]")
            return

        # ── List themes ───────────────────────────────────
        if not arg or arg in ("list", "ls", "lista"):
            current = get_theme()
            table = Table(
                title="[bold]᛭ Temas Disponibles[/]",
                show_lines=False,
                border_style=current.border_style,
                header_style=f"bold {current.border_style}",
            )
            table.add_column("Nombre", style="bold", min_width=10)
            table.add_column("Descripción", min_width=30)
            table.add_column("Prefijo", min_width=4)

            for t in list_themes():
                marker = " ◄" if t.name == current.name else ""
                table.add_row(
                    f"{t.name}{marker}",
                    t.description,
                    t.prompt_prefix,
                )

            console.print()
            console.print(table)
            console.print(
                f"\n[dim]Tema actual: [bold]{current.label}[/]. "
                f"Usa /theme <nombre> para cambiar, /theme current para ver el activo.[/]",
            )
            return

        # ── Switch theme ──────────────────────────────────
        available_names = [t.name for t in list_themes()]
        if arg not in available_names:
            available = ", ".join(available_names)
            render_error(f"Tema desconocido: {arg}. Disponibles: {available}")
            return

        new_theme = set_theme(arg)

        # Persist to config.
        try:
            import yaml

            from .config import CONFIG_FILE

            raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            raw["theme"] = arg
            CONFIG_FILE.write_text(
                yaml.dump(raw, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception:
            pass  # Best-effort — don't crash on config save.

        console.print()
        console.print(
            Panel(
                f"{_THEME_DISPLAYS.get(arg, '')}\n\n"
                f"Prefijo: {new_theme.prompt_prefix}\n"
                f"Bordes: {new_theme.border_style}",
                title=f"[bold {new_theme.border_style}]{new_theme.label}[/]",
                border_style=new_theme.border_style,
                expand=False,
                padding=(0, 1),
            ),
        )
        console.print(f"[success]✓ Tema cambiado a {new_theme.label}[/]")
        console.print("[dim]Los cambios se reflejan en el siguiente prompt.[/]")


# Theme preview snippets for the switch confirmation.
_THEME_DISPLAYS: dict[str, str] = {
    "norse": ("᛭ Runas doradas sobre fondo oscuro\n   Árboles ancestrales y mitología nórdica"),
    "cyberpunk": (
        "⟐ Neon cian y magenta sobre fondo negro\n   Señales digitales desde los nodos periféricos"
    ),
    "minimal": ("› Líneas limpias y silencio\n   Máxima legibilidad, cero decoración"),
}


class ConfirmCommand(BaseCommand):
    """Toggle destructive-write confirmation (diff preview) mode."""

    name = "confirm"
    description = "Activar o desactivar confirmación para escrituras destructivas"
    aliases = ["confirm_write"]

    async def execute(self, args: str) -> None:
        arg = args.strip().lower()
        if arg == "on":
            self.session.config.confirm_write = True
        elif arg == "off":
            self.session.config.confirm_write = False
        elif not arg:
            state = "ON" if self.session.config.confirm_write else "OFF"
            console.print(f"[info]confirm_write: [bold]{state}[/]")
            console.print("[dim]Uso: /confirm on | /confirm off[/]")
            return
        else:
            render_error("Uso: /confirm [on|off]")
            return
        state = "ON" if self.session.config.confirm_write else "OFF"
        console.print(f"[success]✓ confirm_write: {state}[/]")


class DiffCommand(BaseCommand):
    """Preview a would-be file edit or write as a unified diff.

    Usage::

        /diff write path/to/file "new content"          — preview file_write
        /diff edit path/to/file "old" "new"             — preview file_edit
        /diff edit path/to/file "old" "new" --all       — preview replace_all
    """

    name = "diff"
    description = "Previsualizar cambios de archivo sin aplicarlos"
    aliases = ["preview"]

    async def execute(self, args: str) -> None:
        from lilith_tools import ToolRegistry
        from lilith_tools.filesystem import FileEditTool, FileWriteTool

        parts = args.strip().split(None, 1)
        if not parts:
            render_error(
                "Uso: /diff write <path> <content>  |  "
                "/diff edit <path> <old> <new> [--all]"
            )
            return

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "write":
            # Parse: /diff write path content
            # Path is the first whitespace-separated token; everything after is content.
            rest_parts = rest.split(None, 1)
            if len(rest_parts) < 2:
                render_error("Uso: /diff write <path> <content>")
                return
            path, content = rest_parts[0], rest_parts[1]
            tool = FileWriteTool()
            result = tool.execute(path=path, content=content, show_diff=True)

        elif subcmd == "edit":
            # Parse: /diff edit path old_string new_string [--all]
            # Quoted strings are not unquoted here; we just split by whitespace.
            tokens = rest.split()
            if len(tokens) < 3:
                render_error("Uso: /diff edit <path> <old> <new> [--all]")
                return
            replace_all = tokens[-1].lower() == "--all"
            if replace_all:
                path, old, new = tokens[0], tokens[1], tokens[2]
            else:
                path, old, new = tokens[0], tokens[1], tokens[2]
            tool = FileEditTool()
            result = tool.execute(path=path, old_string=old, new_string=new, replace_all=replace_all, show_diff=True)
        else:
            render_error(
                "Subcomando desconocido. Usa: /diff write ... | /diff edit ..."
            )
            return

        if not result.success:
            render_error(result.error)
            return

        diff = result.data.get("diff", "")
        if not diff:
            console.print("[dim]Sin cambios para previsualizar.[/]")
            return

        console.print(f"\n[bold realm]᛭ Diff preview: {result.data.get('path', '')}[/]\n")
        console.print(Syntax(diff, "diff", theme="monokai", line_numbers=False))
        console.print()


class WhereCommand(BaseCommand):
    """Show the loaded configuration files and active settings.

    Reports the global config path, any project-level .lilith/config.yaml
    that was discovered, and the active model/provider.
    """

    name = "where"
    description = "Mostrar archivos de configuración cargados"
    aliases = ["configpath"]

    async def execute(self, _args: str) -> None:
        from .config import CONFIG_FILE, find_project_config, load_config
        from rich.panel import Panel

        cfg = self.session.config
        global_path = getattr(self.session, "config_path", None) or CONFIG_FILE
        project_path = find_project_config()

        lines = [
            f"  [bold]Proveedor:[/]  {cfg.provider}",
            f"  [bold]Modelo:[/]     {cfg.model}",
            f"  [bold]Global:[/]     {global_path}",
            f"  [bold]Proyecto:[/]   {project_path or '(no encontrado)'}",
            f"  [bold]Tools:[/]      {len(getattr(self.session, '_disabled_tools', set()))} deshabilitadas",
            f"  [bold]Confirm write:[/] {'on' if getattr(cfg, 'confirm_write', True) else 'off'}",
        ]
        console.print(
            Panel(
                "\n".join(lines),
                title="[gold]᛭ Configuración activa[/]",
                border_style="dim cyan",
            )
        )
        console.print()


class FileCommand(BaseCommand):
    """Attach a local file to the conversation context.

    Reads the file contents and injects it as a user message so the LLM
    can see it.  Supports text files, code, JSON, YAML, etc.

    Usage::

        /file path/to/file.py          — attach file
        /file path/to/file.py describe — attach with a prompt
    """

    name = "file"
    description = "Adjuntar archivo al contexto del chat"
    aliases = ["f"]

    # Max file size to read (5 MB).
    MAX_SIZE = 5 * 1024 * 1024

    async def execute(self, args: str) -> None:
        from rich.panel import Panel

        parts = args.strip().split(maxsplit=1)
        if not parts:
            render_error("Uso: /file <ruta> [prompt]  — adjunta un archivo al contexto")
            return

        file_path = Path(parts[0]).expanduser()
        user_prompt = parts[1].strip() if len(parts) > 1 else ""

        # Resolve path: relative to CWD, support ~
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path

        # Check existence.
        if not file_path.exists():
            render_error(f"Archivo no encontrado: {file_path}")
            return

        # Check size.
        size = file_path.stat().st_size
        if size > self.MAX_SIZE:
            render_error(
                f"Archivo demasiado grande ({size / 1024 / 1024:.1f} MB). "
                f"Máximo: {self.MAX_SIZE // 1024 // 1024} MB",
            )
            return

        # Read content.
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            render_error(f"Error leyendo archivo: {exc}")
            return

        # Detect language for syntax highlighting.
        suffix_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".rs": "rust",
            ".go": "go",
            ".rb": "ruby",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".sh": "bash",
            ".bash": "bash",
            ".zsh": "bash",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".toml": "toml",
            ".ini": "ini",
            ".cfg": "ini",
            ".md": "markdown",
            ".html": "html",
            ".css": "css",
            ".sql": "sql",
            ".xml": "xml",
        }
        lang = suffix_map.get(file_path.suffix.lower(), "")

        # Show preview.
        line_count = content.count("\n") + 1
        preview_lines = content.split("\n")[:10]
        preview = "\n".join(preview_lines)
        if line_count > 10:
            preview += f"\n... ({line_count - 10} más líneas)"

        console.print(
            Panel(
                preview,
                title=f"[bold gold1]📄 {file_path.name}[/] ({line_count} líneas, {size:,} bytes)",
                border_style="frost",
                expand=False,
            ),
        )

        # Build context message.
        file_message = f"📄 Archivo: `{file_path}`\n\n```{lang}\n{content}\n```"
        if user_prompt:
            file_message = f"{user_prompt}\n\n{file_message}"

        # Add to history and show confirmation.
        self.session.history.append({"role": "user", "content": file_message})
        console.print(f"[success]✓ Archivo adjuntado al contexto ({line_count} líneas)[/]")


class ExportCommand(BaseCommand):
    """Export the current session to a file for sharing or documentation.

    Supports Markdown (human-readable) and JSON (full data) formats. The
    exported payload includes the session id, model, configuration, full
    message history with tool calls and results, active plan state, and
    token usage.

    Usage::

        /export                         — export to Markdown (default)
        /export markdown                — same as above
        /export md filename             — custom filename in ~/.yggdrasil/exports/
        /export json                    — export as full JSON
        /export json /path/to/file.json — export JSON to a specific path
    """

    name = "export"
    description = "Exportar sesión a Markdown o JSON para compartir"
    aliases = ["exp"]

    def _build_metadata(self) -> dict[str, Any]:
        """Collect metadata describing the current session."""
        from datetime import UTC, datetime

        session_id = getattr(self.session, "_session_id", "")
        plan = getattr(self.session, "current_plan", None)
        plan_state: dict[str, Any] | None = None
        if plan is not None:
            try:
                from .plan import plan_to_dict

                plan_state = plan_to_dict(plan)
            except Exception:
                plan_state = {"goal": getattr(plan, "goal", ""), "error": "serialization failed"}

        return {
            "timestamp": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session_id or "local",
            "model": self.session.config.model,
            "provider": self.session.config.provider,
            "config": self.session.config.model_dump(mode="json"),
            "usage": self.session.total_usage,
            "per_model_usage": self.session.per_model_usage,
            "plan": plan_state,
        }

    def _format_markdown(self, metadata: dict[str, Any]) -> str:
        """Format session as Markdown suitable for documentation."""
        lines = [
            "# Sesión Yggdrasil",
            "",
            f"- **Fecha**: {metadata.get('timestamp', 'N/A')}",
            f"- **ID de sesión**: {metadata.get('session_id', 'N/A')}",
            f"- **Modelo**: {metadata.get('model', 'N/A')}",
            f"- **Proveedor**: {metadata.get('provider', 'N/A')}",
        ]
        usage = metadata.get("usage", {})
        if usage and any(v > 0 for v in usage.values()):
            lines.append(
                f"- **Tokens**: {usage.get('prompt_tokens', 0)}↑ "
                f"{usage.get('completion_tokens', 0)}↓ "
                f"{usage.get('total_tokens', 0)}Σ",
            )
        plan = metadata.get("plan")
        if plan:
            goal = plan.get("goal", "")
            done = sum(1 for s in plan.get("steps", []) if s.get("done"))
            total = len(plan.get("steps", []))
            lines.append(f"- **Plan**: {goal} ({done}/{total})")
        lines.append("")
        lines.append("---")
        lines.append("")

        role_icons = {"user": "🧑", "assistant": "🤖", "system": "⚙️", "tool": "🔧"}
        for msg in self.session.history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            icon = role_icons.get(role, "💬")

            if role == "system":
                lines.append(f"> ⚙️ **System**: {content}")
                lines.append("")
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    func = tc.get("function", {}) or {}
                    name = func.get("name", "tool")
                    args = func.get("arguments", "{}")
                    lines.append(f"- **Tool call**: `{name}`")
                    lines.append(f"  - Args: `{args}`")
                lines.append("")

            lines.append(f"### {icon} {role.capitalize()}")
            lines.append("")
            if content:
                # Wrap assistant/user content as code block only if it looks like JSON or is a tool result.
                if role == "tool" and isinstance(content, str):
                    lines.append(f"```text\n{content}\n```")
                else:
                    lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _format_json(self, metadata: dict[str, Any]) -> str:
        """Format session as a complete JSON dump."""
        data = {
            **metadata,
            "messages": list(self.session.history),
        }
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)

    async def execute(self, args: str) -> None:
        from rich.panel import Panel

        text = args.strip()

        # First token is the format; everything else is the requested path/name.
        parts = text.split(maxsplit=1)
        fmt = parts[0].lower() if parts else "md"
        custom_name = parts[1].strip() if len(parts) > 1 else ""

        if fmt not in ("md", "markdown", "json"):
            render_error(f"Formato desconocido: '{fmt}'. Usa 'md', 'markdown' o 'json'.")
            return

        fmt_ext = "json" if fmt == "json" else "md"

        # Prepare export data.
        messages = self.session.history
        if not messages:
            render_error("No hay mensajes para exportar.")
            return

        metadata = self._build_metadata()

        # Generate content.
        if fmt_ext == "json":
            content = self._format_json(metadata)
        else:
            content = self._format_markdown(metadata)

        # Determine output path.
        if custom_name:
            candidate = Path(custom_name).expanduser()
            if candidate.is_absolute() or candidate.parent != Path("."):
                filepath = candidate.resolve()
            else:
                exports_dir = Path("~/.yggdrasil/exports").expanduser()
                exports_dir.mkdir(parents=True, exist_ok=True)
                if not candidate.name.endswith(f".{fmt_ext}"):
                    candidate = Path(f"{candidate.name}.{fmt_ext}")
                filepath = exports_dir / candidate.name
        else:
            from datetime import UTC, datetime

            exports_dir = Path("~/.yggdrasil/exports").expanduser()
            exports_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
            filepath = exports_dir / f"export_{timestamp}.{fmt_ext}"

        # Ensure the parent directory exists.
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Write file.
        try:
            filepath.write_text(content, encoding="utf-8")
        except Exception as exc:
            render_error(f"Error escribiendo archivo: {exc}")
            return

        # Show confirmation.
        msg_count = len(messages)
        user_msgs = sum(1 for m in messages if m.get("role") == "user")
        asst_msgs = sum(1 for m in messages if m.get("role") == "assistant")
        tool_msgs = sum(1 for m in messages if m.get("role") == "tool")

        console.print(
            Panel(
                f"[success]✓ Exportado a {filepath}[/]\n\n"
                f"Mensajes: {msg_count} ({user_msgs} usuario, {asst_msgs} asistente, {tool_msgs} herramienta)\n"
                f"Formato: {fmt_ext.upper()}",
                title="[bold gold1]📦 Exportación Completa[/]",
                border_style="frost",
                expand=False,
            ),
        )


class YggContextCommand(BaseCommand):
    """Manage .ygg project context directories.

    Inspired by Eter-Agents' .eter/ and Aether-Agents' .aether/.
    Provides per-project structured context that persists across sessions.

    Usage::

        /ygg init "My Project" "Description"  — create .ygg/ in current dir
        /ygg status                          — show current .ygg context
        /ygg current "Working on feature X"  — update current.md
        /ygg log "Fixed bug in auth"         — append to log.md
        /ygg tasks                           — show pending tasks
    """

    name = "ygg"
    description = "Gestionar contexto de proyecto .ygg/"
    aliases = ["project", "ctx"]

    async def execute(self, args: str) -> None:
        from lilith_cli.ygg_context import (
            YggContext,
            append_log,
            create_ygg_context,
            find_ygg_dir,
            load_ygg_context,
            update_current,
        )

        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "init":
            # Parse: init "Name" "Description" [goal1,goal2]
            name = rest.strip('"') or "New Project"
            desc = ""
            goals: list[str] = []
            # Simple parsing: first quoted string = name, second = description
            import re
            quoted = re.findall(r'"([^"]*)"', args)
            if len(quoted) >= 1:
                name = quoted[0]
            if len(quoted) >= 2:
                desc = quoted[1]
            # Goals after second quote
            after_desc = args.split('"', 3)[-1] if len(quoted) >= 2 else ""
            if after_desc.strip():
                goals = [g.strip() for g in after_desc.strip().split(",") if g.strip()]

            ctx = create_ygg_context(Path.cwd(), name=name, description=desc, goals=goals)
            console.print(f"[success]✓ .ygg/ creado: {ctx.config.name}[/]")
            console.print(f"[dim]  {ctx.path}[/]")
            return

        if subcmd == "status":
            ygg_path = find_ygg_dir()
            if not ygg_path:
                console.print("[warning]No se encontró .ygg/ en este directorio o superiores.[/]")
                console.print("[dim]  Usa /ygg init para crear uno.[/]")
                return
            ctx = load_ygg_context(ygg_path)
            console.print(f"\n[bold realm]᛭ Contexto .ygg/[/]\n")
            console.print(f"  [cyan]Proyecto:[/] {ctx.config.name or '(sin nombre)'}")
            if ctx.config.description:
                console.print(f"  [cyan]Descripción:[/] {ctx.config.description}")
            if ctx.config.goals:
                console.print(f"  [cyan]Goals:[/] {', '.join(ctx.config.goals)}")
            if ctx.config.model:
                console.print(f"  [cyan]Modelo:[/] {ctx.config.model}")
            if ctx.current:
                console.print(f"\n  [bold]Current:[/]\n  {ctx.current}")
            if ctx.tasks:
                console.print(f"\n  [bold]Tasks:[/]\n  {ctx.tasks}")
            console.print()
            return

        if subcmd == "current":
            if not rest:
                render_error("Uso: /ygg current \"Nuevo estado del proyecto\"")
                return
            update_current(Path.cwd(), rest.strip('"'))
            console.print("[success]✓ current.md actualizado.[/]")
            return

        if subcmd == "log":
            if not rest:
                render_error("Uso: /ygg log \"Entrada de log\"")
                return
            append_log(Path.cwd(), rest.strip('"'))
            console.print("[success]✓ Entrada añadida a log.md.[/]")
            return

        if subcmd == "tasks":
            ygg_path = find_ygg_dir()
            if not ygg_path:
                render_error("No se encontró .ygg/ en este directorio.")
                return
            ctx = load_ygg_context(ygg_path)
            if ctx.tasks:
                console.print(f"\n[bold realm]᛭ Tareas Pendientes[/]\n")
                console.print(ctx.tasks)
            else:
                console.print("[dim]No hay tareas pendientes.[/]")
            return

        if subcmd == "prompt":
            # Generate prompt context for injection into LLM
            ygg_path = find_ygg_dir()
            if not ygg_path:
                render_error("No se encontró .ygg/ en este directorio.")
                return
            ctx = load_ygg_context(ygg_path)
            prompt = ctx.to_prompt_context()
            if prompt:
                console.print(
                    Panel(
                        prompt,
                        title="[bold realm]᛭ Contexto para Prompt[/]",
                        border_style="frost",
                        expand=False,
                        padding=(0, 1),
                    ),
                )
            else:
                console.print("[dim]No hay contexto generado (proyecto vacío).[/]")
            return

        render_error(f"Subcomando desconocido: {subcmd}. Usa: init, status, current, log, tasks, prompt")


class BookmarkCommand(BaseCommand):
    """Manage bookmarks for the current conversation history.

    Bookmarks are stored in ``~/.yggdrasil/bookmarks.json`` and point to a
    specific index in the conversation history. They allow the user to
    mark and return to important points in the chat.

    Usage::

        /bookmark [name]          — create a bookmark at current history position
        /bookmark list            — show all bookmarks
        /bookmark go <n>          — show the message at bookmark N
        /bookmark delete <n>      — remove bookmark N
    """

    name = "bookmark"
    description = "Guardar y gestionar marcadores de la conversación"
    aliases = ["bm", "mark"]

    async def execute(self, args: str) -> None:
        bookmarks = _load_bookmarks()
        arg = args.strip()

        if arg.lower() == "list":
            await self._list_bookmarks(bookmarks)
            return

        if arg.lower().startswith("go "):
            await self._go_to_bookmark(bookmarks, arg[3:].strip())
            return

        if arg.lower().startswith("delete "):
            await self._delete_bookmark(bookmarks, arg[7:].strip())
            return

        # Anything else is treated as a bookmark name (default: timestamp if empty).
        await self._add_bookmark(bookmarks, arg)

    async def _add_bookmark(self, bookmarks: list[dict[str, Any]], name: str) -> None:
        history = self.session.history
        index = len(history) - 1 if history else 0
        if index < 0:
            index = 0
        if not name:
            name = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        bookmark = {
            "id": len(bookmarks) + 1,
            "name": name,
            "index": index,
            "created": datetime.now(UTC).isoformat(),
        }
        bookmarks.append(bookmark)
        _save_bookmarks(bookmarks)
        console.print(f"[success]✓ Marcador guardado:[/] [bold cyan]{bookmark['id']}[/] — {name} (posición {index})")

    async def _list_bookmarks(self, bookmarks: list[dict[str, Any]]) -> None:
        if not bookmarks:
            console.print("[dim]No hay marcadores guardados.[/]")
            return
        table = Table(title="[bold realm]᛭ Marcadores[/]", show_header=True, header_style="bold cyan")
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Nombre", style="white")
        table.add_column("Posición", justify="right")
        table.add_column("Creado", style="dim")
        for bm in bookmarks:
            table.add_row(
                str(bm.get("id", "?")),
                bm.get("name", ""),
                str(bm.get("index", "?")),
                bm.get("created", "")[:19].replace("T", " "),
            )
        console.print()
        console.print(table)
        console.print()

    async def _go_to_bookmark(self, bookmarks: list[dict[str, Any]], raw: str) -> None:
        try:
            bookmark_id = int(raw.strip())
        except ValueError:
            render_error("Uso: /bookmark go <n>  — donde <n> es el ID del marcador")
            return
        for bm in bookmarks:
            if bm.get("id") == bookmark_id:
                history = self.session.history
                index = int(bm.get("index", 0))
                if not history or index < 0 or index >= len(history):
                    render_error(f"La posición del marcador ({index}) está fuera del historial actual.")
                    return
                msg = history[index]
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                console.print(
                    Panel(
                        Markdown(content) if content else "[dim](sin contenido)[/]",
                        title=f"[bold cyan]Marcador {bookmark_id}: {bm.get('name')}[/] — {role}",
                        border_style="frost",
                        expand=False,
                    ),
                )
                return
        render_error(f"Marcador {bookmark_id} no encontrado.")

    async def _delete_bookmark(self, bookmarks: list[dict[str, Any]], raw: str) -> None:
        try:
            bookmark_id = int(raw.strip())
        except ValueError:
            render_error("Uso: /bookmark delete <n>  — donde <n> es el ID del marcador")
            return
        for i, bm in enumerate(bookmarks):
            if bm.get("id") == bookmark_id:
                del bookmarks[i]
                _save_bookmarks(bookmarks)
                console.print(f"[success]✓ Marcador {bookmark_id} eliminado.[/]")
                return
        render_error(f"Marcador {bookmark_id} no encontrado.")


# ── Macro storage helpers ─────────────────────────────────────────

_MACROS_PATH = CONFIG_DIR / "macros.json"


# In-memory recording state is keyed by session identity so multiple sessions
# do not clobber each other. The set of stored macro names is also cached in
# memory so that list/record can behave consistently across a session.
_macro_recording: dict[int, list[str]] = {}


def _load_macros() -> dict[str, list[str]]:
    """Load macros from ``~/.yggdrasil/macros.json``."""
    if not _MACROS_PATH.exists():
        return {}
    try:
        data = json.loads(_MACROS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, list)}
    except Exception as exc:  # pragma: no cover — defensive
        logger = logging.getLogger(__name__)
        logger.warning("Error cargando macros: %s", exc)
    return {}


def _save_macros(macros: dict[str, list[str]]) -> None:
    """Persist macros to ``~/.yggdrasil/macros.json``."""
    _MACROS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MACROS_PATH.write_text(
        json.dumps(macros, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


class MacroCommand(BaseCommand):
    """Record and play back sequences of slash commands.

    A macro is a named list of slash commands (including the leading ``/``) that
    can be executed in order with ``/macro play``. While recording, every slash
    command entered in the REPL is appended to the current macro instead of
    being executed immediately. Recording stops with ``/macro stop``.

    Macros are stored in ``~/.yggdrasil/macros.json``.

    Usage::

        /macro record <name>  — start recording a new macro
        /macro stop           — stop recording
        /macro play <name>    — execute the recorded commands in order
        /macro list           — list saved macros
        /macro delete <name>  — delete a macro
    """

    name = "macro"
    description = "Grabar y reproducir secuencias de comandos de barra"
    aliases = ["macros"]

    async def execute(self, args: str) -> None:
        text = args.strip()
        if not text:
            await self._show_usage()
            return

        parts = text.split(maxsplit=1)
        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "record":
            await self._record(rest)
            return
        if subcmd == "stop":
            await self._stop()
            return
        if subcmd == "play":
            await self._play(rest)
            return
        if subcmd in ("list", "ls"):
            await self._list()
            return
        if subcmd in ("delete", "rm", "remove"):
            await self._delete(rest)
            return

        render_error(f"Subcomando desconocido: /macro {subcmd}")
        await self._show_usage()

    async def _show_usage(self) -> None:
        console.print("\n[bold realm]᛭ /macro[/]\n")
        console.print("  [bold cyan]/macro record <nombre>[/] — Iniciar la grabación")
        console.print("  [bold cyan]/macro stop[/]           — Finalizar la grabación")
        console.print("  [bold cyan]/macro play <nombre>[/]  — Reproducir la macro")
        console.print("  [bold cyan]/macro list[/]           — Listar macros guardadas")
        console.print("  [bold cyan]/macro delete <nombre>[/] — Eliminar una macro")
        console.print()

    def _session_key(self) -> int:
        return id(self.session)

    async def _record(self, name: str) -> None:
        name = name.strip()
        if not name:
            render_error("Uso: /macro record <nombre>")
            return
        if "/" in name or " " in name:
            render_error("El nombre de la macro no puede contener '/' ni espacios.")
            return
        key = self._session_key()
        if key in _macro_recording:
            render_error(
                "Ya se está grabando una macro. Usa /macro stop antes de iniciar otra."
            )
            return
        macros = _load_macros()
        if name in macros:
            render_error(f"La macro '[model]{name}[/]' ya existe. Usa /macro delete {name} para reemplazarla.")
            return
        _macro_recording[key] = []
        # Store the record command itself so stop knows which name to use.
        _macro_recording[key].append(f"/macro record {name}")
        console.print(f"[success]✓ Grabando macro '[model]{name}[/]' — usá /macro stop para finalizar.[/]")

    async def _stop(self) -> None:
        key = self._session_key()
        commands = _macro_recording.pop(key, None)
        if commands is None:
            render_error("No hay una grabación en curso.")
            return

        # The first recorded command is always the /macro record name.
        name = ""
        if commands and commands[0].startswith("/macro record "):
            name = commands[0][len("/macro record "):].strip()
        if not name:
            name = datetime.now(UTC).strftime("macro-%Y%m%d-%H%M%S")

        # Remove the record command itself from the stored sequence.
        stored = [c for c in commands if not c.startswith("/macro record ")]
        if not stored:
            render_error("La macro está vacía; no se guardó.")
            return

        macros = _load_macros()
        macros[name] = stored
        _save_macros(macros)
        console.print(
            f"[success]✓ Macro guardada:[/] [bold cyan]{name}[/] ({len(stored)} comando(s))"
        )

    async def _play(self, name: str) -> None:
        name = name.strip()
        if not name:
            render_error("Uso: /macro play <nombre>")
            return
        macros = _load_macros()
        commands = macros.get(name)
        if commands is None:
            render_error(f"Macro no encontrada: [model]{name}[/]")
            return

        console.print(f"[info]▶ Reproduciendo macro '[model]{name}[/]' ({len(commands)} comando(s))...[/]")
        registry = CommandRegistry(self.session)
        registry.discover()
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            console.print(f"[dim]  ▸ {cmd}[/]")
            # Record the executed command so playbacks don't appear as plain
            # user messages in the history.
            if hasattr(self.session, "_command_history"):
                self.session._command_history.append(
                    {
                        "name": "macro-step",
                        "args": cmd,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
            try:
                await registry.dispatch(cmd)
            except SystemExit:
                raise
            except Exception as exc:  # pragma: no cover — defensive
                render_error(f"Error ejecutando {cmd}: {exc}")
                break
        console.print(f"[success]✓ Macro '[model]{name}[/]' finalizada.[/]")

    async def _list(self) -> None:
        macros = _load_macros()
        if not macros:
            console.print("[dim]No hay macros guardadas.[/]")
            return
        table = Table(title="[bold realm]᛭ Macros guardadas[/]", show_header=True, header_style="bold cyan")
        table.add_column("Nombre", style="cyan")
        table.add_column("Comandos", justify="right")
        table.add_column("Creada", style="dim")
        for name, commands in sorted(macros.items()):
            table.add_row(name, str(len(commands)), "—")
        console.print()
        console.print(table)
        console.print()

    async def _delete(self, name: str) -> None:
        name = name.strip()
        if not name:
            render_error("Uso: /macro delete <nombre>")
            return
        macros = _load_macros()
        if name not in macros:
            render_error(f"Macro no encontrada: [model]{name}[/]")
            return
        del macros[name]
        _save_macros(macros)
        console.print(f"[success]✓ Macro '[model]{name}[/]' eliminada.[/]")


class FeedbackCommand(BaseCommand):
    """Collect and review user feedback on Lilith's responses.

    Feedback is stored in ``~/.yggdrasil/feedback.json`` and helps improve
    the agent's responses over time.

    Usage::

        /feedback <rating> <comment>  — submit feedback (rating 1-5)
        /feedback list                — show recent feedback
        /feedback stats               — show aggregate stats (avg, total)
        /feedback clear               — clear feedback log
    """

    name = "feedback"
    description = "Enviar y revisar retroalimentación sobre las respuestas de Lilith"
    aliases = ["fb"]

    async def execute(self, args: str) -> None:
        entries = _load_feedback()
        text = args.strip()

        if not text:
            console.print(
                "[dim]Uso: /feedback <puntuación 1-5> [comentario]  ·  /feedback list  ·  /feedback stats  ·  /feedback clear[/]"
            )
            return

        parts = text.split(maxsplit=1)
        sub = parts[0].lower()

        if sub == "list":
            await self._list_feedback(entries)
            return

        if sub == "stats":
            await self._stats_feedback(entries)
            return

        if sub == "clear":
            await self._clear_feedback(entries)
            return

        # Try to parse a rating as the first token.
        try:
            rating = int(sub)
        except ValueError:
            render_error("La puntuación debe ser un número entero entre 1 y 5.")
            return

        if rating < 1 or rating > 5:
            render_error("La puntuación debe estar entre 1 y 5.")
            return

        comment = parts[1] if len(parts) > 1 else ""
        entry = {
            "id": len(entries) + 1,
            "rating": rating,
            "comment": comment,
            "created": datetime.now(UTC).isoformat(),
        }
        entries.append(entry)
        _save_feedback(entries)
        console.print(
            f"[success]✓ Feedback guardado:[/] [bold cyan]{entry['id']}[/] — "
            f"puntuación {rating}/5"
            f"{f' · {comment}' if comment else ''}"
        )

    async def _list_feedback(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            console.print("[dim]No hay feedback registrado.[/]")
            return

        table = Table(
            title="[bold realm]᛭ Feedback reciente[/]",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Puntuación", justify="right")
        table.add_column("Comentario", style="white")
        table.add_column("Creado", style="dim")

        # Show the most recent 20 entries.
        for entry in entries[-20:]:
            table.add_row(
                str(entry.get("id", "?")),
                str(entry.get("rating", "?")),
                entry.get("comment", "") or "[dim](sin comentario)[/]",
                entry.get("created", "")[:19].replace("T", " "),
            )
        console.print()
        console.print(table)
        console.print()

    async def _stats_feedback(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            console.print("[dim]No hay feedback registrado.[/]")
            return

        ratings = [int(e.get("rating", 0)) for e in entries if isinstance(e.get("rating"), int)]
        avg = sum(ratings) / len(ratings) if ratings else 0.0

        console.print("\n[bold realm]᛭ Estadísticas de feedback[/]\n")
        console.print(f"  Total de entradas: {len(entries)}")
        console.print(f"  Puntuación media:  {avg:.2f} / 5")
        console.print(f"  Puntuación mínima: {min(ratings) if ratings else '-'} / 5")
        console.print(f"  Puntuación máxima: {max(ratings) if ratings else '-'} / 5")
        console.print()

    async def _clear_feedback(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            console.print("[dim]No hay feedback para borrar.[/]")
            return
        _save_feedback([])
        console.print(f"[success]✓ {len(entries)} entrada(s) de feedback eliminada(s).[/]")


class CostsCommand(BaseCommand):
    name = "costs"
    description = "Mostrar o reiniciar telemetría de delegaciones"

    async def execute(self, args: str) -> None:
        from lilith_tools.orchestration_state import OrchestrationStateStore

        store = OrchestrationStateStore()
        parts = args.strip().split()
        if parts and parts[0].lower() == "reset":
            if len(parts) < 2 or parts[1] != "CONFIRMAR":
                console.print("[warning]Confirma con: /costs reset CONFIRMAR[/]")
                return
            store.reset_costs()
            console.print("[success]✓ Telemetría de costes reiniciada.[/]")
            return
        summary = store.cost_summary(getattr(self.session, "_session_id", ""))
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Tipo")
        table.add_column("Preset/Provider")
        table.add_column("Prompt", justify="right")
        table.add_column("Completion", justify="right")
        table.add_column("Llamadas", justify="right")
        historical = summary["historical"]
        for scope, label in (("presets", "preset"), ("providers", "provider")):
            for name, usage in sorted(historical.get(scope, {}).items()):
                table.add_row(
                    label, name, str(usage.get("prompt_tokens", 0)),
                    str(usage.get("completion_tokens", 0)), str(usage.get("calls", 0)),
                )
        console.print(table)
        session_total = summary["session"].get("total", {})
        history_total = historical.get("total", {})
        console.print(
            f"Sesión: {session_total.get('prompt_tokens', 0)} prompt + "
            f"{session_total.get('completion_tokens', 0)} completion / "
            f"{session_total.get('calls', 0)} llamadas"
        )
        console.print(
            f"Histórico: {history_total.get('prompt_tokens', 0)} prompt + "
            f"{history_total.get('completion_tokens', 0)} completion / "
            f"{history_total.get('calls', 0)} llamadas"
        )


class StateCommand(BaseCommand):
    name = "state"
    description = "Mostrar o limpiar el plan persistente de orquestación"

    async def execute(self, args: str) -> None:
        from lilith_tools.orchestration_state import OrchestrationStateStore

        store = OrchestrationStateStore()
        parts = args.strip().split()
        if parts and parts[0].lower() == "clear":
            if len(parts) < 2 or parts[1] != "CONFIRMAR":
                console.print("[warning]Confirma con: /state clear CONFIRMAR[/]")
                return
            store.clear()
            console.print("[success]✓ Estado de orquestación limpiado.[/]")
            return
        state = store.get()
        plan = state.get("plan")
        if not plan:
            console.print("[dim]No hay plan activo.[/]")
            return
        console.print(f"\n[bold realm]Plan activo: {plan['name']}[/]")
        if plan.get("description"):
            console.print(f"[dim]{plan['description']}[/]")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID")
        table.add_column("Tarea")
        table.add_column("Estado")
        table.add_column("Preset")
        table.add_column("Usage", justify="right")
        for task in state.get("tasks", []):
            usage = task.get("usage") or {}
            total = usage.get("total_tokens", 0)
            table.add_row(
                str(task.get("id", "")), str(task.get("title", "")),
                str(task.get("status", "")), str(task.get("preset") or "—"),
                str(total),
            )
        console.print(table)


class SkillsCommand(BaseCommand):
    name = "skills"
    description = "Listar, mostrar, guardar o borrar skills de delegación"

    async def execute(self, args: str) -> None:
        import shlex
        from lilith_skills.delegation_skills import (
            DelegationSkill,
            DelegationSkillRegistry,
        )

        registry = DelegationSkillRegistry()
        try:
            parts = shlex.split(args)
        except ValueError as exc:
            render_error(str(exc))
            return
        action = parts[0].lower() if parts else "list"
        if action == "list":
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Nombre")
            table.add_column("Preset")
            table.add_column("Flags")
            table.add_column("Descripción")
            for skill in registry.list():
                flags = []
                if skill.agentic:
                    flags.append("agentic")
                if skill.structured:
                    flags.append("structured")
                if skill.max_tokens is not None:
                    flags.append(f"max={skill.max_tokens}")
                table.add_row(skill.name, skill.preset, ", ".join(flags) or "—", skill.description)
            console.print(table)
            return
        if action == "show" and len(parts) == 2:
            skill = registry.get(parts[1])
            if skill is None:
                render_error(f"Skill '{parts[1]}' no existe")
                return
            console.print(Panel(json.dumps(skill.to_dict(), ensure_ascii=False, indent=2), title=skill.name))
            return
        if action == "save":
            if len(parts) < 2 or parts[1].lower() != "latest" or "--name" not in parts:
                render_error("Uso: /skills save latest --name <nombre> [--description texto]")
                return
            name_idx = parts.index("--name") + 1
            if name_idx >= len(parts):
                render_error("--name requiere valor")
                return
            description = "Delegación guardada"
            if "--description" in parts:
                desc_idx = parts.index("--description") + 1
                if desc_idx < len(parts):
                    description = parts[desc_idx]
            history = getattr(self.session, "_tool_call_history", [])
            last = next((item for item in reversed(history) if item.get("name") == "delegate_subagent"), None)
            if last is None:
                render_error("No hay una delegación anterior para guardar")
                return
            call = last.get("arguments") or {}
            prompt = str(call.get("prompt", ""))
            if "{TASK}" not in prompt:
                prompt = prompt + "\n\nTarea reutilizada: {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}"
            else:
                if "{PROJECT}" not in prompt:
                    prompt += "\nProyecto: {PROJECT}"
                if "{CONTEXT}" not in prompt:
                    prompt += "\nContexto: {CONTEXT}"
            skill = DelegationSkill(
                name=parts[name_idx], description=description,
                preset=str(call.get("preset", "")), prompt_template=prompt,
                agentic=bool(call.get("agentic", False)),
                structured=bool(call.get("structured", False)),
                max_tokens=call.get("max_tokens"),
            )
            registry.save(skill)
            console.print(f"[success]✓ Skill '{skill.name}' guardada.[/]")
            return
        if action == "delete" and len(parts) >= 2:
            if len(parts) < 3 or parts[2] != "CONFIRMAR":
                console.print(f"[warning]Confirma con: /skills delete {parts[1]} CONFIRMAR[/]")
                return
            if not registry.delete(parts[1]):
                render_error(f"Skill '{parts[1]}' no existe")
                return
            console.print(f"[success]✓ Skill '{parts[1]}' eliminada.[/]")
            return
        render_error("Uso: /skills list|show <name>|save latest --name <name>|delete <name> CONFIRMAR")


class MCPCommand(BaseCommand):
    """Inspect and manage MCP (Model Context Protocol) servers.

    Sub-commands:

    * ``/mcp list`` — show every configured server with its status
      (``ok``, ``down``, ``disabled``), the number of mounted tools,
      and the last error if any.
    * ``/mcp reload <server>`` — tear down and re-spawn a single
      server. Useful after editing ``~/.yggdrasil/config.yaml`` without
      restarting the REPL.
    """

    name = "mcp"
    description = "Listar y recargar servidores MCP montados en el REPL"
    aliases = ["mcps"]

    async def execute(self, args: str) -> None:
        import shlex

        try:
            parts = shlex.split(args)
        except ValueError as exc:
            render_error(str(exc))
            return
        if not parts or parts[0].lower() in ("list", "ls", "status"):
            self._list()
            return
        if parts[0].lower() == "reload" and len(parts) == 2:
            self._reload(parts[1])
            return
        render_error("Uso: /mcp list | /mcp reload <server>")

    def _manager(self) -> Any | None:
        """Return the MCP manager attached to the session, if any.

        The manager is attached by :func:`run_repl` at startup; tests
        that bypass the REPL may not have one.
        """
        return getattr(self.session, "_mcp_manager", None)

    def _list(self) -> None:
        manager = self._manager()
        if manager is None:
            console.print(
                "[dim]MCP no inicializado en esta sesión. "
                "Inicia el REPL con run_repl() para usar /mcp.[/]"
            )
            return
        rows = manager.status()
        if not rows:
            console.print("[dim]No hay servidores MCP configurados.[/]")
            return
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Server")
        table.add_column("Estado", justify="center")
        table.add_column("Tools", justify="right")
        table.add_column("Error")
        status_icon = {"ok": "[status.ok]ok[/]", "down": "[error]down[/]",
                       "disabled": "[dim]disabled[/]"}
        for row in rows:
            table.add_row(
                row["server"],
                status_icon.get(row["status"], row["status"]),
                str(row["tools"]),
                row.get("error") or "",
            )
        console.print(table)

    def _reload(self, server: str) -> None:
        manager = self._manager()
        if manager is None:
            render_error("MCP no inicializado en esta sesión.")
            return
        status = manager.reload(server)
        if status == "ok":
            tools = manager.mounted_tools.get(server, [])
            console.print(
                f"[status.ok]✓ MCP '{server}'[/] "
                f"recargado ({len(tools)} tools): "
                f"{', '.join(tools) or '(ninguna)'}"
            )
        else:
            render_error(f"MCP '{server}': {status}")


class SubagentsCommand(BaseCommand):
    """Inspect Hlidskjalf sub-agent presets.

    Sub-commands:

    * ``/subagents list`` — show every preset declared in
      ``~/.yggdrasil/hlidskjalf_subagents.yaml`` along with its
      provider, model and a one-line status.
    * ``/subagents test [preset]`` — fire a ``PONG`` ping (with a
      small ``max_tokens``) at every preset (or just the named one) in
      parallel and report a table with latency, model actually used,
      and any error. Per-preset timeout: 20 s.

    The test never raises: a 401, timeout, or any other failure is
    captured as a row in the table with the actual error message.
    """

    name = "subagents"
    description = "Listar y probar presets de Hlidskjalf (sub-agentes)"
    aliases = ["sa"]

    # Per-preset timeout for /subagents test. Independent of the
    # tool-level 180s of ``delegate_subagent`` because the healthcheck
    # should fail fast on bad credentials.
    _TEST_TIMEOUT_SECONDS = 20.0
    # Probe size: tiny enough to be cheap on every provider, big enough
    # for the provider to actually respond with content.
    _TEST_MAX_TOKENS = 8
    # Bonus probe: ask the provider for this many tokens and see what
    # happens. If the API rejects the request, the error message often
    # reports the actual ceiling.
    _PROBE_MAX_TOKENS = 65536

    async def execute(self, args: str) -> None:
        import shlex

        try:
            parts = shlex.split(args)
        except ValueError as exc:
            render_error(str(exc))
            return
        action = (parts[0].lower() if parts else "list")
        if action in ("list", "ls"):
            self._list()
            return
        if action == "test":
            target = parts[1] if len(parts) >= 2 else None
            await self._test(target)
            return
        render_error(
            "Uso: /subagents list | /subagents test [preset]"
        )

    # ── list ──────────────────────────────────────────────────────

    def _list(self) -> None:
        from .main import _load_subagent_presets

        presets = _load_subagent_presets()
        if not presets:
            console.print(
                "[dim]No hay presets en "
                "~/.yggdrasil/hlidskjalf_subagents.yaml[/]"
            )
            return
        cfg = None
        try:
            from .config import load_config
            cfg = load_config()
        except Exception:
            cfg = None
        configured_providers = set((cfg.providers or {}).keys()) if cfg else set()
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Preset")
        table.add_column("Provider")
        table.add_column("Modelo")
        table.add_column("Estado")
        for name, preset in sorted(presets.items()):
            provider = str((preset or {}).get("provider", "—"))
            model = str((preset or {}).get("model", "—"))
            if configured_providers and provider not in configured_providers:
                state = "[warning]no en config.yaml[/]"
            elif configured_providers:
                state = "[status.ok]ok[/]"
            else:
                state = "[dim]config no cargado[/]"
            table.add_row(name, provider, model, state)
        console.print(table)

    # ── test ──────────────────────────────────────────────────────

    async def _test(self, target: str | None) -> None:
        from .config import load_config
        from .main import _load_subagent_presets
        from .providers import LLMProviderWrapper

        presets = _load_subagent_presets()
        if not presets:
            console.print(
                "[warning]No hay presets en "
                "~/.yggdrasil/hlidskjalf_subagents.yaml[/]"
            )
            return
        if target is not None and target not in presets:
            render_error(
                f"Preset '{target}' no existe. Disponibles: "
                f"{', '.join(sorted(presets))}"
            )
            return

        try:
            cfg = load_config()
        except Exception as exc:
            render_error(f"No pude cargar config: {exc}")
            return

        selected = (
            {target: presets[target]} if target else dict(presets)
        )
        names = list(selected.keys())

        # Pre-create the wrappers so the parallel section is purely
        # network-bound; building a wrapper inside the gather would
        # serialise httpx client init.
        wrappers: dict[str, tuple[LLMProviderWrapper, dict]] = {}
        for name in names:
            preset = selected[name] or {}
            provider_name = str(preset.get("provider") or "").lower()
            if not provider_name:
                wrappers[name] = (None, {"error": "preset sin 'provider'"})  # type: ignore[assignment]
                continue
            profile = (cfg.providers or {}).get(provider_name)
            if profile is None:
                wrappers[name] = (
                    None,
                    {"error": f"provider '{provider_name}' no en config.yaml"},
                )
                continue
            try:
                # Use the same per-call config shape that
                # ``delegate_subagent`` builds at runtime, so the
                # healthcheck mirrors production behaviour.
                local_cfg = cfg.model_copy(deep=True)
                local_cfg.provider = provider_name
                local_cfg.model = (
                    preset.get("model") or profile.model or cfg.model
                )
                if preset.get("max_tokens") is not None:
                    local_cfg.max_tokens = int(preset["max_tokens"])
                elif profile.max_tokens is not None:
                    local_cfg.max_tokens = profile.max_tokens
                if preset.get("temperature") is not None:
                    local_cfg.temperature = float(preset["temperature"])
                wrapper = LLMProviderWrapper(local_cfg)
            except Exception as exc:
                wrappers[name] = (None, {"error": f"init: {exc}"})
                continue
            wrappers[name] = (wrapper, {"profile": profile, "preset": preset})

        async def _ping_one(name: str) -> dict[str, Any]:
            wrapper, meta = wrappers[name]
            row: dict[str, Any] = {
                "preset": name,
                "provider": (meta.get("preset") or {}).get("provider", "—"),
                "model": (meta.get("preset") or {}).get("model", "—"),
            }
            if wrapper is None:
                row["ok"] = False
                row["latency_ms"] = 0
                row["error"] = meta.get("error", "unknown")
                return row
            t0 = time.perf_counter()
            try:
                response = await wrapper.complete(
                    [{"role": "user", "content": "PONG"}],
                    tools=None,
                    max_tokens=self._TEST_MAX_TOKENS,
                )
            except Exception as exc:
                row["ok"] = False
                row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                row["error"] = f"{type(exc).__name__}: {exc}"
                return row
            finally:
                # Per-call close so each preset's connection is freed;
                # the gather runs them in parallel and the wrappers
                # share no state.
                try:
                    await wrapper.close()
                except Exception:
                    pass

            elapsed = (time.perf_counter() - t0) * 1000
            row["latency_ms"] = int(elapsed)
            content = response.get("content") or ""
            usage = response.get("usage") or {}
            row["ok"] = bool(content)
            row["tokens_out"] = usage.get("completion_tokens") or len(content)
            row["error"] = "" if row["ok"] else "respuesta vacía"

            # Bonus: probe max_tokens by asking for a huge value and
            # capturing the error. Providers that accept the request
            # are reported as 'acepta N'; providers that reject get
            # the actual error.
            probe_row = await self._probe_max_tokens(wrapper, name)
            row["max_tokens_probe"] = probe_row
            return row

        # Header announcing the run.
        console.print(
            f"[info]Probando {len(selected)} preset(s) en paralelo "
            f"(timeout {self._TEST_TIMEOUT_SECONDS:.0f}s c/u)...[/]"
        )

        # Run in parallel with a hard ceiling — even though each
        # wrapper.complete already gets its own timeout via
        # ``asyncio.wait_for`` inside the provider, the gather adds a
        # second wall-clock guarantee.
        async def _guarded(name: str) -> dict[str, Any]:
            try:
                return await asyncio.wait_for(
                    _ping_one(name),
                    timeout=self._TEST_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                return {
                    "preset": name,
                    "provider": (
                        (selected[name] or {}).get("provider", "—")
                    ),
                    "model": ((selected[name] or {}).get("model", "—")),
                    "ok": False,
                    "latency_ms": int(self._TEST_TIMEOUT_SECONDS * 1000),
                    "error": "timeout global",
                }
            except Exception as exc:
                return {
                    "preset": name,
                    "provider": (
                        (selected[name] or {}).get("provider", "—")
                    ),
                    "model": ((selected[name] or {}).get("model", "—")),
                    "ok": False,
                    "latency_ms": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        rows: list[dict[str, Any]] = await asyncio.gather(
            *(_guarded(name) for name in names)
        )
        # Stable order — gather preserves input order, but be safe.
        rows.sort(key=lambda r: r["preset"])

        self._render_test_table(rows)

    async def _probe_max_tokens(
        self, wrapper: LLMProviderWrapper, preset_name: str
    ) -> dict[str, Any]:
        """Best-effort probe of the real ``max_tokens`` ceiling.

        We reuse the same wrapper (already closed in the main call) by
        doing one final tiny ping; if that fails the main table is
        already red and we just return ``{"status": "skipped"}``.
        """
        try:
            await wrapper.complete(
                [{"role": "user", "content": "PONG"}],
                tools=None,
                max_tokens=self._PROBE_MAX_TOKENS,
            )
            return {"status": f"acepta {self._PROBE_MAX_TOKENS}"}
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            # Many providers report the ceiling in the error text.
            import re

            m = re.search(r"(\d{3,7})", msg)
            ceiling = int(m.group(1)) if m else None
            return {"status": "rechaza", "error": msg, "ceiling_hint": ceiling}

    def _render_test_table(self, rows: list[dict[str, Any]]) -> None:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Preset")
        table.add_column("Provider")
        table.add_column("Modelo")
        table.add_column("ms", justify="right")
        table.add_column("Out", justify="right")
        table.add_column("max_tokens probe")
        table.add_column("Estado")
        for row in rows:
            probe = row.get("max_tokens_probe") or {}
            if isinstance(probe, dict):
                probe_text = probe.get("status", "—")
                if probe.get("ceiling_hint"):
                    probe_text = f"{probe_text} (~{probe['ceiling_hint']})"
            else:
                probe_text = str(probe) if probe else "—"
            if row["ok"]:
                state = "[status.ok]ok[/]"
            else:
                state = f"[error]{row.get('error', '?')}[/]"
            table.add_row(
                row["preset"],
                row.get("provider", "—"),
                row.get("model", "—"),
                str(row.get("latency_ms", 0)),
                str(row.get("tokens_out", 0)),
                probe_text,
                state,
            )
        console.print(table)


class CommandRegistry:
    """Discovers, registers, and routes slash commands."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self._commands: dict[str, BaseCommand] = {}
        self._aliases: dict[str, str] = {}

    def discover(self) -> None:
        """Register all built-in command classes."""
        builtin: list[type[BaseCommand]] = [
            HelpCommand,
            QuickstartCommand,
            CommandsCommand,
            ToolsCommand,
            ModelCommand,
            ProviderCommand,
            MemoryCommand,
            CostsCommand,
            StateCommand,
            SkillsCommand,
            MCPCommand,
            SubagentsCommand,
            ClearCommand,
            CostCommand,
            TokensCommand,
            UsageCommand,
            MetricsCommand,
            PlanCommand,
            UndoCommand,

            StatusCommand,
            ConfigCommand,

            DiffConfigCommand,
            QuitCommand,
            SaveCommand,
            RedoCommand,
            RetryCommand,
            ContinueCommand,
            CopyCommand,
            SystemCommand,
            TemplateCommand,
            InitCommand,
            HistoryCommand,
            CompactCommand,
            ResumeCommand,
            ThemeCommand,
            FileCommand,
            ExportCommand,
            BifrostCommand,
            YggContextCommand,
            ConfirmCommand,
            DiffCommand,
            WhereCommand,
            AgentCommand,
            BookmarkCommand,
            FeedbackCommand,
            MacroCommand,
            AutoCommand,
        ]
        for cmd_cls in builtin:
            cmd = cmd_cls(self.session)
            self._commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self._aliases[alias] = cmd.name


    def get(self, name: str) -> BaseCommand | None:
        """Look up a command by name or alias."""
        real_name = self._aliases.get(name, name)
        return self._commands.get(real_name)

    def list_commands(self) -> list[BaseCommand]:
        """Return all registered commands."""
        return list(self._commands.values())

    async def dispatch(self, raw_input: str) -> bool:
        """Try to dispatch a slash command.

        Returns True if the input was a command (and was handled),
        False otherwise.
        """
        text = raw_input.strip()
        if not text.startswith("/"):
            return False

        parts = text[1:].split(maxsplit=1)
        cmd_name = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""

        # Record command usage for /metrics telemetry.
        if hasattr(self.session, "_command_history"):
            self.session._command_history.append(
                {
                    "name": cmd_name,
                    "args": cmd_args,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

        cmd = self.get(cmd_name)
        if cmd is None:
            render_error(
                f"Comando desconocido: /{cmd_name}  — escribe /help para ver los disponibles",
            )
            return True

        await cmd.execute(cmd_args)
        return True
