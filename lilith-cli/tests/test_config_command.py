"""Tests for the /config slash command."""

from __future__ import annotations

import pytest


def _configure_supported_profiles(fake_session):
    from lilith_cli.config import ProviderProfile

    fake_session.config.providers = {
        "deepseek": ProviderProfile(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="test-key",
            base_url="https://api.deepseek.com/v1",
        ),
        "grok": ProviderProfile(
            provider="grok",
            model="grok-4.20-0309-reasoning",
            api_key="test-key",
            base_url="https://api.x.ai/v1",
        ),
    }


@pytest.mark.asyncio
async def test_config_no_args_shows_status(fake_session, capsys):
    """/config (no args) must render model / provider / base_url of the session."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "deepseek-v4-flash"
    fake_session.config.provider = "deepseek"
    fake_session.config.base_url = "https://api.deepseek.com/v1"

    await run_config_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "deepseek-v4-flash" in combined
    assert "deepseek" in combined
    assert "https://api.deepseek.com/v1" in combined


@pytest.mark.asyncio
async def test_config_show_alias_renders_status(fake_session, capsys):
    """/config show (and 'status') must behave like the no-arg form."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "grok-4.20-0309-reasoning"
    fake_session.config.provider = "grok"
    fake_session.config.base_url = "https://api.x.ai/v1"

    await run_config_command(fake_session, "show")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "grok-4.20-0309-reasoning" in combined
    assert "grok" in combined


@pytest.mark.asyncio
async def test_config_sets_known_attribute(fake_session, capsys):
    """/config model foo must setattr on session.config.model."""
    from lilith_cli.extra_commands import run_config_command

    original = fake_session.config.model
    _configure_supported_profiles(fake_session)
    fake_session.config.provider = "deepseek"
    fake_session.config.model = "deepseek-v4-flash"

    await run_config_command(fake_session, "model deepseek-v4-pro")

    assert fake_session.config.model == "deepseek-v4-pro"
    assert fake_session.config.model != original


@pytest.mark.asyncio
async def test_config_without_value_reports_error(fake_session, capsys):
    """/config <key> (no value) must print a usage error and not mutate config."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "deepseek-v4-flash"

    await run_config_command(fake_session, "model")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "uso" in combined.lower()
    assert fake_session.config.model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_config_unknown_key_reports_error(fake_session, capsys):
    """/config <unknown> <value> must print an unknown-key error and not mutate config."""
    from lilith_cli.extra_commands import run_config_command

    fake_session.config.model = "deepseek-v4-flash"

    await run_config_command(fake_session, "totally_made_up_key somevalue")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "desconocida" in combined.lower() or "unknown" in combined.lower() or "no existe" in combined.lower()
    assert fake_session.config.model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_config_provider_change(fake_session, capsys):
    """/config provider grok must update session.config.provider."""
    from lilith_cli.extra_commands import run_config_command

    _configure_supported_profiles(fake_session)
    fake_session.config.provider = "deepseek"

    await run_config_command(fake_session, "provider grok")

    assert fake_session.config.provider == "grok"
