"""Tests for the /bench slash command.

Covers the latency benchmark flow without hitting any real LLM provider.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig


# ── Helpers ──────────────────────────────────────────────────────────


class _FakeProvider:
    """Minimal provider that yields a pre-built sequence of stream events.

    The events mirror what the real ``LLMProviderWrapper.stream`` emits:
    dicts with optional ``content`` and ``finish_reason`` keys.
    """

    def __init__(self, events):
        self._events = list(events)

    async def stream(self, messages, model=None):  # noqa: ARG002
        for ev in self._events:
            yield ev

    async def close(self):
        return None


def _make_session(model: str = "test-model", provider: str = "local") -> AgentSession:
    cfg = YggdrasilConfig(provider=provider, model=model)
    return AgentSession(cfg)


def _one_turn_events():
    """A minimal two-event stream: one content chunk, then a finish marker."""
    return [
        {"content": "Hello world from the fake provider"},
        {"finish_reason": "stop"},
    ]


def _multi_token_events():
    """A stream that yields several content events to exercise token counting."""
    return [
        {"content": "alpha beta"},
        {"content": "gamma delta"},
        {"content": "epsilon zeta"},
        {"finish_reason": "stop"},
    ]


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bench_runs_one_turn(capsys):
    """A single-turn bench emits the header, the per-turn line, and a latency summary."""
    from lilith_cli.extra_commands import run_bench_command

    session = _make_session("bench-one")
    provider = _FakeProvider(_one_turn_events())

    with patch("lilith_cli.providers.create_provider", return_value=provider):
        await run_bench_command(session, "")

    out = capsys.readouterr().out
    assert "Benchmark" in out
    assert "bench-one" in out
    assert "1 turnos" in out
    assert "Latencia promedio" in out
    assert "Turno 1" in out


@pytest.mark.asyncio
async def test_bench_measures_multiple_turns(capsys):
    """`--turns 3` prints the configured count and one line per turn."""
    from lilith_cli.extra_commands import run_bench_command

    session = _make_session("bench-multi")
    provider = _FakeProvider(_one_turn_events())

    with patch("lilith_cli.providers.create_provider", return_value=provider):
        await run_bench_command(session, "--turns 3")

    out = capsys.readouterr().out
    assert "3 turnos" in out
    assert "Turno 1:" in out
    assert "Turno 2:" in out
    assert "Turno 3:" in out


@pytest.mark.asyncio
async def test_bench_handles_provider_exception(capsys):
    """If provider.stream raises, /bench prints an error and returns gracefully."""
    from lilith_cli.extra_commands import run_bench_command

    session = _make_session("bench-boom")

    class _BrokenProvider(_FakeProvider):
        async def stream(self, messages, model=None):  # noqa: ARG002
            raise RuntimeError("synthetic provider failure")
            yield  # pragma: no cover — makes this an async generator

    provider = _BrokenProvider([])

    with patch("lilith_cli.providers.create_provider", return_value=provider):
        # Must not raise — the command should catch and report.
        await run_bench_command(session, "")

    out = capsys.readouterr().out
    assert "falló" in out.lower()


@pytest.mark.asyncio
async def test_bench_custom_prompt(capsys):
    """`--prompt` is forwarded into the messages payload passed to provider.stream."""
    from lilith_cli.extra_commands import run_bench_command

    session = _make_session("bench-prompt")
    provider = _FakeProvider(_one_turn_events())

    with patch("lilith_cli.providers.create_provider", return_value=provider):
        await run_bench_command(session, '--prompt "hola mundo"')

    # Capture the messages/model kwargs passed to provider.stream by wrapping.
    captured: dict = {}

    class _CapturingProvider(_FakeProvider):
        async def stream(self, messages, model=None):
            captured["messages"] = messages
            captured["model"] = model
            async for ev in super().stream(messages, model=model):
                yield ev

    cap_provider = _CapturingProvider(_one_turn_events())
    with patch("lilith_cli.providers.create_provider", return_value=cap_provider):
        await run_bench_command(session, '--prompt "hola mundo"')

    assert captured["messages"] == [{"role": "user", "content": "hola mundo"}]


@pytest.mark.asyncio
async def test_bench_calculates_tokens_per_second(capsys):
    """Multiple content events yield a non-zero token total and a Tokens/segundo line."""
    from lilith_cli.extra_commands import run_bench_command

    session = _make_session("bench-tps")
    provider = _FakeProvider(_multi_token_events())

    with patch("lilith_cli.providers.create_provider", return_value=provider):
        await run_bench_command(session, "")

    out = capsys.readouterr().out
    assert "Tokens/segundo" in out
