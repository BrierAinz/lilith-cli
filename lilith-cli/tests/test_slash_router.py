"""Routing table, dispatch, macros and command plugins of ``slash_router``."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from lilith_cli import commands, slash_router
from lilith_cli.commands import CommandRegistry
from lilith_cli.slash_router import SlashRoute, all_routes, dispatch, route, slash_commands

LILITH_CLI = Path(__file__).resolve().parent.parent / "lilith_cli"

# ``run_X_command`` coroutines whose command is served by a CommandRegistry
# class instead of a route. Each entry must name a registered class.
REGISTRY_BACKED = {
    "run_agent_command": "AgentCommand",
    "run_auto_command": "AutoCommand",
    "run_bookmark_command": "BookmarkCommand",
    "run_config_command": "ConfigCommand",
    "run_continue_command": "ContinueCommand",
    "run_copy_command": "CopyCommand",
    "run_export_command": "ExportCommand",
    "run_file_command": "FileCommand",
    "run_plan_command": "PlanCommand",
    "run_redo_command": "RedoCommand",
    "run_status_command": "StatusCommand",
    "run_template_command": "TemplateCommand",
    "run_theme_command": "ThemeCommand",
}


@pytest.fixture(autouse=True)
def _fresh_plugins(monkeypatch):
    monkeypatch.delenv(slash_router.DISABLED_PLUGINS_ENV, raising=False)
    slash_router.reload_plugins()
    yield
    slash_router.reload_plugins()


def _defined_handlers() -> set[str]:
    paths = [
        *LILITH_CLI.glob("*_command.py"),
        *LILITH_CLI.glob("*_commands.py"),
        *(LILITH_CLI / "slash_commands").glob("*.py"),
    ]
    names = set()
    for path in paths:
        if path.name == "extra_commands.py":
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("run_") \
                    and node.name.endswith("_command"):
                names.add(node.name)
    return names


def test_route_names_are_unique() -> None:
    with pytest.raises(ValueError, match="/dup"):
        slash_router.index_routes([SlashRoute("dup", None), SlashRoute("other", None, ("dup",))])


def test_every_route_handler_is_a_coroutine_function() -> None:
    for name, item in all_routes().items():
        assert inspect.iscoroutinefunction(item.handler), f"/{name}"


def test_every_command_handler_is_routed_or_registry_backed() -> None:
    routed = {item.handler.__name__ for item in all_routes().values()}
    orphans = sorted(_defined_handlers() - routed - set(REGISTRY_BACKED))
    assert not orphans, f"run_X_command handlers that no slash command reaches: {orphans}"


def test_registry_backed_handlers_name_registered_classes() -> None:
    registry = CommandRegistry(None)
    registry.discover()
    classes = {type(command).__name__ for command in registry.list_commands()}
    assert set(REGISTRY_BACKED.values()) <= classes
    assert not set(REGISTRY_BACKED) & {item.handler.__name__ for item in all_routes().values()}


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [("map", "map"), ("s", "search"), ("rev", "reverse"), ("sec", "security-review"),
     ("cls", "clear-screen"), ("?", "help"), ("tr", "transcript"), ("calc", "calc")],
)
def test_aliases_resolve_to_their_route(spelling: str, canonical: str) -> None:
    assert route(spelling).name == canonical
    assert f"/{spelling}" in slash_commands()


def test_slash_commands_include_registry_commands() -> None:
    words = slash_commands()
    for word in ("/model", "/quit", "/q", "/resume", "/tools", "/state"):
        assert word in words


@pytest.mark.asyncio
async def test_dispatch_passes_arguments_to_the_route(monkeypatch) -> None:
    calls = []

    async def handler(session, args):
        calls.append((session, args))

    monkeypatch.setitem(slash_router._BUILTIN, "map", SlashRoute("map", handler))
    session = SimpleNamespace(_command_history=[])
    assert await dispatch(session, "/map src --depth 2")
    assert calls == [(session, "src --depth 2")]
    assert session._command_history[-1]["name"] == "map"


@pytest.mark.asyncio
async def test_dispatch_ignores_plain_text() -> None:
    assert await dispatch(SimpleNamespace(), "hola") is False


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_the_registry(fake_session, capsys) -> None:
    assert await dispatch(fake_session, "/nope-not-a-command")
    assert "Comando desconocido: /nope-not-a-command" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_records_other_commands_and_stops(fake_session, monkeypatch, tmp_path) -> None:
    """Regression: the REPL recorded ``/macro stop`` and never stopped."""
    monkeypatch.setattr(commands, "_MACROS_PATH", tmp_path / "macros.json")
    ran = []

    async def pwd(session, args):
        ran.append(args)

    monkeypatch.setitem(slash_router._BUILTIN, "pwd", SlashRoute("pwd", pwd))

    await dispatch(fake_session, "/macro record demo")
    await dispatch(fake_session, "/pwd")
    assert ran == [], "commands typed while recording must not run"
    await dispatch(fake_session, "/macro stop")
    assert id(fake_session) not in commands._macro_recording
    assert commands._load_macros() == {"demo": ["/pwd"]}

    await dispatch(fake_session, "/macro play demo")
    assert ran == [""], "playback must reach routed commands, not only the registry"


@pytest.mark.asyncio
async def test_user_alias_expands_but_cannot_shadow_builtins(fake_session, monkeypatch) -> None:
    from lilith_cli.slash_commands import settings

    seen = []

    async def handler(session, args):
        seen.append(args)

    monkeypatch.setitem(slash_router._BUILTIN, "map", SlashRoute("map", handler))
    monkeypatch.setattr(settings, "_load_aliases", lambda: {"m2": "/map src", "map": "/git"})
    monkeypatch.setattr(slash_router, "_load_aliases", settings._load_aliases)

    await dispatch(fake_session, "/m2 --depth 1")
    await dispatch(fake_session, "/map")
    assert seen == ["src --depth 1", ""]


def test_utilities_bundle_can_be_disabled(monkeypatch) -> None:
    assert route("calc") is not None
    monkeypatch.setenv(slash_router.DISABLED_PLUGINS_ENV, "utilities")
    slash_router.reload_plugins()
    assert route("calc") is None
    assert "/calc" not in slash_commands()
    assert route("map") is not None


def test_entry_point_plugins_add_commands_without_overriding(monkeypatch, caplog) -> None:
    async def hello(session, args):
        return None

    async def hijack(session, args):
        return None

    plugin = (SlashRoute("hello", hello, ("hi",)), SlashRoute("git", hijack))
    entry = SimpleNamespace(name="greetings", load=lambda: plugin)
    monkeypatch.setattr(
        slash_router, "entry_points",
        lambda group: [entry] if group == slash_router.ENTRY_POINT_GROUP else [],
    )
    slash_router.reload_plugins()

    assert route("hi").handler is hello
    assert route("git").handler is not hijack
    assert "cannot override /git" in caplog.text


def test_broken_plugin_is_ignored(monkeypatch, caplog) -> None:
    def explode():
        raise RuntimeError("boom")

    entry = SimpleNamespace(name="broken", load=explode)
    monkeypatch.setattr(slash_router, "entry_points", lambda group: [entry])
    slash_router.reload_plugins()

    assert route("calc") is not None
    assert "Ignoring command plugin 'broken'" in caplog.text
