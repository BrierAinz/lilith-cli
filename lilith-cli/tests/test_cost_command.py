"""Tests for the /cost command and per-model cost tracking."""

import pytest
from lilith_cli.agent import AgentSession
from lilith_cli.commands import CostCommand
from lilith_cli.config import YggdrasilConfig
from lilith_cli.render import render_cost
from lilith_cli.providers import estimate_cost


def _make_session(model: str = "gpt-4o") -> AgentSession:
    cfg = YggdrasilConfig(provider="openai", model=model)
    return AgentSession(cfg)


@pytest.mark.asyncio
async def test_cost_command_shows_total_and_model(capsys):
    """/cost should display total cost, token usage, and current model."""
    session = _make_session("gpt-4o")
    session._track_usage(
        {"prompt_tokens": 2000, "completion_tokens": 500, "total_tokens": 2500},
        "gpt-4o",
    )

    cmd = CostCommand(session)
    await cmd.execute("")

    captured = capsys.readouterr().out
    # estimate_cost("gpt-4o", 2000, 500) = (2000*2.5 + 500*10.0) / 1_000_000 = 0.0100
    assert "$0.0100" in captured or "0.0100" in captured
    assert "gpt-4o" in captured
    assert "Desglose por modelo" not in captured


@pytest.mark.asyncio
async def test_cost_command_breakdown_with_multiple_models(capsys):
    """/cost should show a per-model breakdown when multiple models were used."""
    session = _make_session("gpt-4o")
    session._track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 250, "total_tokens": 1250},
        "gpt-4o",
    )
    session._track_usage(
        {"prompt_tokens": 4000, "completion_tokens": 1000, "total_tokens": 5000},
        "gpt-4o-mini",
    )

    cmd = CostCommand(session)
    await cmd.execute("")

    captured = capsys.readouterr().out
    assert "Desglose por modelo" in captured
    assert "gpt-4o" in captured
    assert "gpt-4o-mini" in captured
    assert "1250" in captured
    assert "5000" in captured


def test_per_model_usage_tracks_costs():
    """AgentSession._track_usage should accumulate per-model tokens and cost."""
    session = _make_session("gpt-4o")
    session._track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        "gpt-4o",
    )
    session._track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        "gpt-4o-mini",
    )

    per_model = session.per_model_usage
    assert set(per_model.keys()) == {"gpt-4o", "gpt-4o-mini"}
    assert per_model["gpt-4o"]["total_tokens"] == 2000
    assert per_model["gpt-4o-mini"]["total_tokens"] == 2000
    assert per_model["gpt-4o"]["cost"] == estimate_cost("gpt-4o", 1000, 1000)
    assert per_model["gpt-4o-mini"]["cost"] == estimate_cost("gpt-4o-mini", 1000, 1000)


def test_render_cost_estimate_for_next_1k(capsys):
    """render_cost should include the estimated cost for the next 1K tokens."""
    render_cost(
        {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        {"gpt-4o": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000, "cost": 0.0125}},
        "gpt-4o",
        0.0125,
    )
    captured = capsys.readouterr().out
    assert "Estimado para 1K tokens" in captured
    # estimate_cost returns per-token cost, so next 1K prompt + 1K completion is
    # (2.5 + 10.0) / 1_000_000 * 1000 = 0.0125 USD.
    assert "0.0125" in captured


def test_render_cost_unknown_model_shows_no_estimate(capsys):
    """render_cost should indicate when a pricing estimate is unavailable."""
    render_cost(
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        {},
        "unknown-model",
        0.0,
    )
    captured = capsys.readouterr().out
    assert "no disponible para este modelo" in captured
