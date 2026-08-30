"""Tests for the /model-info slash command."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_model_info_current_shows_session_model(fake_session, capsys):
    """/model-info (no args) must render a table for the session's current model."""
    from lilith_cli.extra_commands import run_model_info_command

    fake_session.config.model = "deepseek-v4-flash"

    await run_model_info_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "deepseek-v4-flash" in combined
    # The "current" annotation must appear when the model is the active one.
    assert "actual" in combined.lower() or "(actual)" in combined


@pytest.mark.asyncio
async def test_model_info_current_keyword(fake_session, capsys):
    """/model-info current must behave like the no-arg form."""
    from lilith_cli.extra_commands import run_model_info_command

    fake_session.config.model = "grok-4.20-0309-reasoning"

    await run_model_info_command(fake_session, "current")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "grok-4.20-0309-reasoning" in combined


@pytest.mark.asyncio
async def test_model_info_list_renders_all_models(fake_session, capsys):
    """/model-info list must print every known model from the registry."""
    from lilith_cli.extra_commands import run_model_info_command

    await run_model_info_command(fake_session, "list")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "deepseek-v4-flash" in combined
    assert "deepseek-v4-pro" in combined
    assert "grok-4.5" in combined
    assert "gpt-4o" not in combined


@pytest.mark.asyncio
async def test_model_info_specific_known_model(fake_session, capsys):
    """/model-info <known> must render info for that model and use canonical casing."""
    from lilith_cli.extra_commands import run_model_info_command

    await run_model_info_command(fake_session, "GROK-4.5")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Canonical casing from the registry is used.
    assert "grok-4.5" in combined


@pytest.mark.asyncio
async def test_model_info_supports_grok_45(fake_session, capsys):
    """El catálogo muestra el contexto y precios de Grok 4.5."""
    from lilith_cli.extra_commands import run_model_info_command

    await run_model_info_command(fake_session, "grok-4.5")

    combined = capsys.readouterr().out
    assert "grok-4.5" in combined
    assert "500K" in combined
    assert "$2.00" in combined
    assert "$6.00" in combined
    assert "reasoning" in combined


@pytest.mark.asyncio
async def test_model_info_unknown_model_reports_error(fake_session, capsys):
    """/model-info <unknown> must print an error and not raise."""
    from lilith_cli.extra_commands import run_model_info_command

    await run_model_info_command(fake_session, "totally-fake-model-zzz")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "desconocido" in combined.lower() or "no encontr" in combined.lower() or "totally-fake-model-zzz" in combined
