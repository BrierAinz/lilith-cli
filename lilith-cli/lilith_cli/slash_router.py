"""Single routing table for the slash commands typed in the REPL.

Every command handled outside :class:`~lilith_cli.commands.CommandRegistry`
is declared once here as a :class:`SlashRoute`. The REPL and ``/macro play``
dispatch through :func:`dispatch`, and completion, ``/help``, ``/how`` and
spelling suggestions derive their command lists from the same table.

Route bundles ("command plugins") add commands without touching this table:

* bundled ones are listed in ``BUNDLED_PLUGINS``;
* installed packages expose a ``lilith_cli.slash_commands`` entry point that
  resolves to a sequence of :class:`SlashRoute` objects.

``LILITH_DISABLED_COMMAND_PLUGINS`` (comma-separated plugin names) turns
bundles off. Built-in names always win over plugin names.
"""

from __future__ import annotations

import functools
import importlib
import logging
import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from .batch_command import run_batch_command
from .bg_command import run_bg_command
from .btw_command import run_btw_command
from .completion_command import run_completion_command
from .how_command import run_how_command
from .ingest_command import run_ingest_command
from .json_commands import run_json_command
from .metrics_commands import (
    run_bench_command,
    run_metrics_command,
    run_tokens_command,
    run_usage_command,
)
from .notes_command import run_note_command
from .paste_command import run_paste_command
from .pipeline_command import run_pipeline_command
from rich.markup import escape

from .render import console
from .slash_commands.conversation import (
    run_compact_command,
    run_conclave_command,
    run_context_command,
    run_goal_command,
    run_last_tool_command,
    run_pin_command,
    run_recap_command,
    run_summary_command,
)
from .slash_commands.environment import (
    run_cd_command,
    run_clear_screen_command,
    run_doctor_command,
    run_env_command,
    run_pwd_command,
    run_redact_command,
    run_secret_command,
)
from .slash_commands.extras import (
    run_feedback_command,
    run_learn_command,
    run_log_command,
    run_tip_command,
    run_tour_command,
)
from .slash_commands.git import (
    run_apply_command,
    run_changelog_command,
    run_diff_branch_command,
    run_diff_staged_command,
    run_diff_unstaged_command,
    run_git_command,
    run_pr_command,
    run_release_command,
    run_review_command,
)
from .slash_commands.help import run_help_command
from .slash_commands.navigation import (
    run_compare_command,
    run_deps_command,
    run_explain_command,
    run_map_command,
    run_multi_file_command,
    run_search_command,
    run_snippet_command,
    run_todos_command,
    run_tree_command,
    run_watch_command,
    run_whereami_command,
)
from .slash_commands.quality import (
    run_format_command,
    run_lint_command,
    run_lint_fix_command,
    run_security_command,
    run_test_command,
)
from .slash_commands.sessions import (
    run_capture_command,
    run_fork_command,
    run_history_command,
    run_recent_command,
    run_replay_command,
)
from .slash_commands.settings import (
    _load_aliases,
    run_alias_command,
    run_editor_command,
    run_hooks_command,
    run_json_mode_command,
    run_macro_command,
    run_model_info_command,
    run_profile_command,
    run_stream_command,
)
from .temperature_command import run_temperature_command
from .transcript_commands import run_transcript_command
from .undo_command import run_undo_peek_command
from .workflow_command import run_workflow_command

if TYPE_CHECKING:
    from .commands import CommandRegistry
    from .session_runtime import SessionRuntime

logger = logging.getLogger(__name__)

SlashHandler = Callable[["SessionRuntime", str], Awaitable[None]]

ENTRY_POINT_GROUP = "lilith_cli.slash_commands"
BUNDLED_PLUGINS = {"utilities": "lilith_cli.slash_commands.utilities:ROUTES"}
DISABLED_PLUGINS_ENV = "LILITH_DISABLED_COMMAND_PLUGINS"


@dataclass(frozen=True)
class SlashRoute:
    """A slash command: its canonical name, aliases and handler coroutine."""

    name: str
    handler: SlashHandler
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


def _console_route(name: str) -> SlashRoute:
    """Routes for the agent console views (/capabilities, /kit, /mission, /court)."""

    async def handler(session: SessionRuntime, args: str) -> None:
        from .agent_console import console_command

        console_command(session, name, args, console)

    return SlashRoute(name, handler)


ROUTES: tuple[SlashRoute, ...] = (
    # Help and discovery
    SlashRoute("help", run_help_command, ("h", "?")),
    SlashRoute("how", run_how_command),
    SlashRoute("completion", run_completion_command),
    SlashRoute("tip", run_tip_command),
    SlashRoute("tour", run_tour_command),
    SlashRoute("learn", run_learn_command),
    SlashRoute("feedback", run_feedback_command),
    # Agent console views
    _console_route("capabilities"),
    _console_route("kit"),
    _console_route("mission"),
    _console_route("court"),
    # Git
    SlashRoute("git", run_git_command),
    SlashRoute("pr", run_pr_command),
    SlashRoute("diff-staged", run_diff_staged_command, ("diffstaged",)),
    SlashRoute("diff-unstaged", run_diff_unstaged_command, ("diffunstaged",)),
    SlashRoute("diff-branch", run_diff_branch_command, ("diffbranch",)),
    SlashRoute("review", run_review_command),
    SlashRoute("changelog", run_changelog_command),
    SlashRoute("release", run_release_command),
    SlashRoute("apply", run_apply_command),
    # Code quality
    SlashRoute("lint", run_lint_command),
    SlashRoute("lint-fix", run_lint_fix_command),
    SlashRoute("format", run_format_command),
    SlashRoute("test", run_test_command),
    SlashRoute("security-review", run_security_command, ("sec",)),
    # Navigation
    SlashRoute("search", run_search_command, ("s",)),
    SlashRoute("tree", run_tree_command),
    SlashRoute("map", run_map_command),
    SlashRoute("todos", run_todos_command),
    SlashRoute("watch", run_watch_command, ("w",)),
    SlashRoute("deps", run_deps_command),
    SlashRoute("compare", run_compare_command),
    SlashRoute("snippet", run_snippet_command),
    SlashRoute("explain", run_explain_command),
    SlashRoute("whereami", run_whereami_command),
    SlashRoute("multi-file", run_multi_file_command),
    # Sessions and conversation
    SlashRoute("history", run_history_command),
    SlashRoute("capture", run_capture_command, ("cap",)),
    SlashRoute("replay", run_replay_command),
    SlashRoute("fork", run_fork_command),
    SlashRoute("recent", run_recent_command),
    SlashRoute("transcript", run_transcript_command, ("tr",)),
    SlashRoute("compact", run_compact_command),
    SlashRoute("context", run_context_command),
    SlashRoute("goal", run_goal_command),
    SlashRoute("pin", run_pin_command, ("p",)),
    SlashRoute("last-tool", run_last_tool_command),
    SlashRoute("recap", run_recap_command),
    SlashRoute("summary", run_summary_command),
    SlashRoute("conclave", run_conclave_command),
    SlashRoute("paste", run_paste_command),
    SlashRoute("note", run_note_command, ("notes", "note-add")),
    SlashRoute("btw", run_btw_command, ("aside", "side")),
    SlashRoute("ingest", run_ingest_command),
    # Workflows
    SlashRoute("workflow", run_workflow_command),
    SlashRoute("pipeline", run_pipeline_command),
    SlashRoute("batch", run_batch_command),
    SlashRoute("bg", run_bg_command),
    SlashRoute("undo-peek", run_undo_peek_command, ("undo-diff", "peeks")),
    SlashRoute("macro", run_macro_command),
    # Settings
    SlashRoute("profile", run_profile_command),
    SlashRoute("hooks", run_hooks_command),
    SlashRoute("json-mode", run_json_mode_command),
    SlashRoute("alias", run_alias_command),
    SlashRoute("model-info", run_model_info_command),
    SlashRoute("stream", run_stream_command),
    SlashRoute("editor", run_editor_command),
    SlashRoute("temperature", run_temperature_command, ("temp",)),
    # Environment
    SlashRoute("cd", run_cd_command),
    SlashRoute("pwd", run_pwd_command),
    SlashRoute("env", run_env_command),
    SlashRoute("secret", run_secret_command),
    SlashRoute("redact", run_redact_command),
    SlashRoute("doctor", run_doctor_command),
    SlashRoute("clear-screen", run_clear_screen_command, ("cls",)),
    SlashRoute("json", run_json_command),
    SlashRoute("log", run_log_command, ("l",)),
    # Usage and metrics
    SlashRoute("metrics", run_metrics_command),
    SlashRoute("tokens", run_tokens_command),
    SlashRoute("usage", run_usage_command),
    SlashRoute("bench", run_bench_command),
)


def index_routes(routes: Iterable[SlashRoute]) -> dict[str, SlashRoute]:
    """Map every name and alias to its route, rejecting duplicates."""
    by_name: dict[str, SlashRoute] = {}
    for item in routes:
        for name in item.names:
            if name in by_name:
                raise ValueError(f"/{name} is declared by two routes")
            by_name[name] = item
    return by_name


_BUILTIN = index_routes(ROUTES)
_plugin_routes: dict[str, SlashRoute] | None = None


def _disabled_plugins() -> set[str]:
    raw = os.environ.get(DISABLED_PLUGINS_ENV, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _load_target(target: str) -> Any:
    module_name, _, attr = target.partition(":")
    value: Any = importlib.import_module(module_name)
    for part in filter(None, attr.split(".")):
        value = getattr(value, part)
    return value


def _plugin_sources() -> list[tuple[str, Callable[[], Any]]]:
    sources: list[tuple[str, Callable[[], Any]]] = [
        (name, lambda target=target: _load_target(target))
        for name, target in BUNDLED_PLUGINS.items()
    ]
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name not in BUNDLED_PLUGINS:
            sources.append((ep.name, ep.load))
    return sources


def _load_plugins() -> dict[str, SlashRoute]:
    disabled = _disabled_plugins()
    loaded: dict[str, SlashRoute] = {}
    for plugin, load in _plugin_sources():
        if plugin in disabled:
            continue
        try:
            routes = load()
            if callable(routes):
                routes = routes()
            plugin_index = index_routes(routes)
        except Exception:  # a broken plugin must not take the REPL down
            logger.warning("Ignoring command plugin %r", plugin, exc_info=True)
            continue
        for name, item in plugin_index.items():
            if name in _BUILTIN or name in loaded:
                logger.warning("Command plugin %r cannot override /%s", plugin, name)
                continue
            loaded[name] = item
    return loaded


def plugin_routes() -> dict[str, SlashRoute]:
    """Routes contributed by enabled command plugins (loaded once)."""
    global _plugin_routes
    if _plugin_routes is None:
        _plugin_routes = _load_plugins()
    return _plugin_routes


def reload_plugins() -> None:
    """Forget loaded plugins so the next lookup reads the environment again."""
    global _plugin_routes
    _plugin_routes = None


def route(name: str) -> SlashRoute | None:
    """Return the route for a command name or alias, built-ins first."""
    return _BUILTIN.get(name) or plugin_routes().get(name)


def all_routes() -> dict[str, SlashRoute]:
    """Every routable spelling, built-in and plugin."""
    return {**plugin_routes(), **_BUILTIN}


def route_aliases() -> dict[str, str]:
    """``{alias: canonical}`` for every routed alias."""
    return {name: item.name for name, item in all_routes().items() if name != item.name}


@functools.cache
def registry_names() -> frozenset[str]:
    """Names and aliases of the :class:`CommandRegistry` built-ins."""
    from .commands import CommandRegistry

    registry = CommandRegistry(None)
    registry.discover()
    return frozenset(registry._commands) | frozenset(registry._aliases)


def slash_commands() -> list[str]:
    """Every accepted spelling as ``/name``: routes plus registry commands."""
    return sorted(f"/{name}" for name in set(all_routes()) | registry_names())


def expand_user_alias(name: str, args: str) -> tuple[str, str] | None:
    """Resolve a user alias (aliases.json) to ``(name, args)``.

    One level only: built-in commands always win and cannot be shadowed; the
    alias target is a slash command (with or without the leading ``/``) and
    the user's extra arguments are appended to the alias' own.
    """
    if f"/{name}" in slash_commands():
        return None
    target = _load_aliases().get(name)
    if not target:
        return None
    expanded = target.strip().lstrip("/")
    if not expanded:
        return None
    parts = f"{expanded} {args}".strip().split(maxsplit=1)
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


def _record_usage(session: Any, name: str, args: str) -> None:
    history = getattr(session, "_command_history", None)
    if isinstance(history, list):
        history.append({"name": name, "args": args, "timestamp": datetime.now(UTC).isoformat()})


async def dispatch(
    session: SessionRuntime, text: str, registry: CommandRegistry | None = None
) -> bool:
    """Run the slash command in *text*; ``False`` when it is not one.

    While ``/macro record`` is active, other slash commands are stored in the
    macro instead of running. Unknown names fall through to the registry,
    which handles its own commands and reports the rest as unknown.
    ``SystemExit`` from ``/quit`` propagates to the caller.
    """
    text = text.strip()
    if not text.startswith("/"):
        return False
    parts = text[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else "help"
    args = parts[1] if len(parts) > 1 else ""

    expanded = expand_user_alias(name, args)
    if expanded is not None:
        name, args = expanded
        text = f"/{name} {args}".strip()

    from .commands import _macro_recording

    recording = _macro_recording.get(id(session))
    if recording is not None and name != "macro":
        recording.append(text)
        console.print(f"[dim]  + grabado: {escape(text)}[/]")
        return True

    found = route(name)
    if found is not None:
        _record_usage(session, found.name, args)
        await found.handler(session, args)
        return True

    if registry is None:
        from .commands import CommandRegistry

        registry = CommandRegistry(session)
        registry.discover()
    return await registry.dispatch(text)
