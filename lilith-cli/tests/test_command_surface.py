"""Single discovery contract for help, commands, completion, and suggestions."""

from __future__ import annotations

import pytest
from lilith_cli import repl
from lilith_cli.command_surface import aliases, completion_words, unknown_command
from lilith_cli.commands import CommandRegistry
from lilith_cli.extra_commands import run_help_command


def test_alias_catalog_covers_registry_and_repl_aliases() -> None:
    mapping = aliases()
    expected = {
        "h": "help",
        "cmds": "commands",
        "m": "memory",
        "s": "search",
        "tr": "transcript",
        "cls": "clear-screen",
        "sec": "security-review",
    }
    for alias, canonical in expected.items():
        assert mapping.get(alias) == canonical


def test_completion_is_unique_and_prioritizes_canonical_names() -> None:
    mapping = aliases()
    words = completion_words(repl._SLASH_COMMANDS)
    assert len(words) == len(set(words))
    assert all(f"/{alias}" in words for alias in mapping)
    alias_flags = [word[1:] in mapping for word in words]
    assert alias_flags == sorted(alias_flags)


def test_unknown_command_suggests_without_rewriting_input() -> None:
    message = unknown_command("toools")
    assert "Comando desconocido: /toools" in message
    assert "/tools" in message
    assert "/commands <texto>" in message


@pytest.mark.asyncio
async def test_bare_slash_dispatches_help(fake_session, capsys) -> None:
    registry = CommandRegistry(fake_session)
    registry.discover()
    assert await registry.dispatch("/")
    assert "Comandos de Lilith" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_full_help_covers_every_repl_command(fake_session, capsys) -> None:
    await run_help_command(fake_session, "all")
    output = capsys.readouterr().out
    mapping = aliases()
    canonical = {mapping.get(word[1:], word[1:]) for word in repl._SLASH_COMMANDS}
    missing = sorted(name for name in canonical if f"/{name}" not in output)
    assert not missing
    assert "Comando disponible" not in output
    assert "/transcript" in output
    assert "alias: /tr" in output


@pytest.mark.asyncio
async def test_commands_reuses_full_help_surface(fake_session, capsys) -> None:
    registry = CommandRegistry(fake_session)
    registry.discover()
    await registry.get("commands").execute("")
    commands_output = capsys.readouterr().out
    await run_help_command(fake_session, "all")
    help_output = capsys.readouterr().out
    assert commands_output == help_output
