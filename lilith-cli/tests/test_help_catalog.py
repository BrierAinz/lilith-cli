"""The /help catalog, the route table and completion must describe the same commands.

``HELP_CATALOG`` is curated by hand, so these tests catch drift in both
directions: a routed command missing from /help, a catalog entry that no
longer dispatches, a name listed twice, or a completion entry that is not
documented.
"""

from __future__ import annotations

import inspect

import pytest

from lilith_cli.command_surface import aliases
from lilith_cli.commands import CommandRegistry, QuickstartCommand
from lilith_cli.slash_commands.help import HELP_CATALOG
from lilith_cli.slash_router import all_routes, registry_names, slash_commands

CATALOG_NAMES = {name for entries in HELP_CATALOG.values() for name, _ in entries}


def _canonical(name: str) -> str:
    return aliases().get(name, name)


def _registry() -> CommandRegistry:
    registry = CommandRegistry(None)
    registry.discover()
    return registry


def test_every_routed_command_is_documented() -> None:
    routed = {item.name for item in all_routes().values()}
    missing = sorted(routed - CATALOG_NAMES)
    assert not missing, f"Routed commands missing from HELP_CATALOG: {missing}"


def test_every_registry_command_is_documented_by_name_or_alias() -> None:
    missing = sorted(
        command.name
        for command in _registry().list_commands()
        if not ({command.name, *command.aliases} & CATALOG_NAMES)
    )
    assert not missing, f"Registry commands missing from HELP_CATALOG: {missing}"


def test_every_catalog_entry_dispatches() -> None:
    dispatchable = set(all_routes()) | registry_names()
    missing = sorted(CATALOG_NAMES - dispatchable)
    assert not missing, f"HELP_CATALOG lists commands nothing dispatches: {missing}"


def test_no_command_listed_twice() -> None:
    seen: dict[str, str] = {}
    duplicates = []
    for family, entries in HELP_CATALOG.items():
        for name, _ in entries:
            if name in seen:
                duplicates.append((name, seen[name], family))
            seen[name] = family
    assert not duplicates, f"Commands listed more than once in HELP_CATALOG: {duplicates}"


def test_catalog_families() -> None:
    assert set(HELP_CATALOG) == {
        "Session", "Configuration", "Development", "Information", "Files & Git",
        "Utilities", "Environment", "System", "Orchestration", "Help",
    }


def test_completion_covers_the_catalog_and_has_no_duplicates() -> None:
    words = slash_commands()
    assert len(words) == len(set(words))
    missing = sorted(name for name in CATALOG_NAMES if f"/{name}" not in words)
    assert not missing, f"Documented commands that Tab does not complete: {missing}"


def test_every_completion_entry_is_documented() -> None:
    undocumented = sorted(
        word for word in slash_commands()
        if _canonical(word[1:]) not in CATALOG_NAMES
        and not any(_canonical(word[1:]) == _canonical(name) for name in CATALOG_NAMES)
    )
    assert not undocumented, f"Completion offers undocumented commands: {undocumented}"


# Commands that were documented at some point and must stay documented.
KNOWN_PRESENT = {
    "agent", "alias", "auto", "base64", "batch", "bench", "bifrost", "bookmark",
    "calc", "capture", "cd", "changelog", "cls", "commands", "compact", "compare",
    "completion", "conclave", "config", "context", "continue", "copy", "cost",
    "deps", "diff", "diff-config", "diff-staged", "doctor", "editor", "env",
    "epoch", "explain", "export", "feedback", "file", "fork", "format", "git",
    "goal", "hash", "help", "history", "hooks", "how", "init", "json",
    "json-mode", "learn", "lines", "lint", "lint-fix", "log", "macro", "map",
    "metrics", "model", "model-info", "multi-file", "note", "now", "pin",
    "pipeline", "plan", "profile", "provider", "pwd", "qr", "quickstart", "quit",
    "quote", "random", "recap", "recent", "redo", "release", "replay",
    "reverse", "review", "save", "search", "secret", "snippet", "status",
    "stream", "summary", "template", "test", "theme", "timer", "tip", "tokens",
    "todos", "tools", "tour", "tree", "undo", "usage", "uuid", "voice", "watch",
    "whereami", "workflow", "ygg",
}


@pytest.mark.parametrize("name", sorted(KNOWN_PRESENT))
def test_known_command_still_documented_and_dispatchable(name: str) -> None:
    documented = name in CATALOG_NAMES or _canonical(name) in CATALOG_NAMES
    assert documented, f"/{name} disappeared from /help"
    assert name in set(all_routes()) | registry_names(), f"/{name} no longer dispatches"


@pytest.mark.parametrize(
    ("name", "family"),
    [("conclave", "System"), ("learn", "Information"), ("status", "Information"), ("mission", "Orchestration"),
     ("lint-fix", "Development"), ("tools", "Configuration")],
)
def test_command_family(name: str, family: str) -> None:
    assert name in {entry for entry, _ in HELP_CATALOG[family]}


def test_quickstart_mentions_v7_entry_points() -> None:
    body = inspect.getsource(QuickstartCommand._brief_tour)
    assert "lilith doctor" in body
    for flag in ("--preset", "--agentic", "--structured", "--max-tokens", "--max-turns"):
        assert flag in body
    assert "/conclave" in body and "/learn" in body
