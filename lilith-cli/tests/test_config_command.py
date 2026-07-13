"""Tests for the /config slash command."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_config_no_args_shows_status(fake_session, capsys):
    """/config (no args) must render model / provider / base_url of the session."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "gpt-4o"
    fake_session.config.provider = "openai"
    fake_session.config.base_url = "https://api.openai.com/v1"

    await run_config_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "gpt-4o" in combined
    assert "openai" in combined
    assert "https://api.openai.com/v1" in combined


@pytest.mark.asyncio
async def test_config_show_alias_renders_status(fake_session, capsys):
    """/config show (and 'status') must behave like the no-arg form."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "claude-sonnet-4"
    fake_session.config.provider = "anthropic"
    fake_session.config.base_url = "https://api.anthropic.com"

    await run_config_command(fake_session, "show")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "claude-sonnet-4" in combined
    assert "anthropic" in combined


@pytest.mark.asyncio
async def test_config_sets_known_attribute(fake_session, capsys):
    """/config model foo must setattr on session.config.model."""
    from lilith_cli.extra_commands import run_config_command

    original = fake_session.config.model
    fake_session.config.model = "gpt-4o"

    await run_config_command(fake_session, "model gpt-4o-mini")

    assert fake_session.config.model == "gpt-4o-mini"
    assert fake_session.config.model != original


@pytest.mark.asyncio
async def test_config_without_value_reports_error(fake_session, capsys):
    """/config <key> (no value) must print a usage error and not mutate config."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "gpt-4o"

    await run_config_command(fake_session, "model")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "uso" in combined.lower()
    assert fake_session.config.model == "gpt-4o"


@pytest.mark.asyncio
async def test_config_unknown_key_reports_error(fake_session, capsys):
    """/config <unknown> <value> must print an unknown-key error and not mutate config."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "gpt-4o"

    await run_config_command(fake_session, "totally_made_up_key somevalue")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "desconocida" in combined.lower() or "unknown" in combined.lower() or "no existe" in combined.lower()
    assert fake_session.config.model == "gpt-4o"


@pytest.mark.asyncio
async def test_config_provider_change(fake_session, capsys):
    """/config provider openai must update session.config.provider."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.provider = "local"

    await run_config_command(fake_session, "provider openai")

    assert fake_session.config.provider == "openai"