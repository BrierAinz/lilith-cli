"""Tests for the /recap slash command.

Behavior sourced from ``extra_commands.py:run_recap_command``:
    - No args → uses n = 5.
    - Args parses to ``int(text)`` (ValueError → usage error).
    - Bounds-check: n must be in [1, 50]; out-of-range → usage error.
    - Empty history → friendly warning, no stream.
    - Awaits ``_stream_agent_reply(session, prompt)`` with prompt =
      ``f"Resumí las últimas {n} rondas de la conversación de forma concisa."``.
    - n is clamped to ``len(session.history)`` so we never ask the LLM
      for more rounds than actually exist.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lilith_cli.extra_commands import run_recap_command


@pytest.mark.asyncio
async def test_recap_default_uses_five(fake_session):
    """/recap (no args) must default to n=5 in the awaited prompt.

    fake_session carries a small history so the bound check passes.
    """
    fake_session.history = [{"role": "user", "content": "x"}] * 5
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, "")

    mock_stream.assert_awaited_once_with(
        fake_session,
        "Resumí las últimas 5 rondas de la conversación de forma concisa.",
    )


@pytest.mark.asyncio
async def test_recap_with_explicit_n(fake_session):
    """/recap <n> must embed the parsed integer in the awaited prompt."""
    fake_session.history = [{"role": "user", "content": "x"}] * 20
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, "10")

    mock_stream.assert_awaited_once_with(
        fake_session,
        "Resumí las últimas 10 rondas de la conversación de forma concisa.",
    )


@pytest.mark.asyncio
async def test_recap_invalid_n_reports_error(fake_session, capsys):
    """/recap with a non-integer arg must report a usage error and skip the stream."""
    fake_session.history = [{"role": "user", "content": "x"}] * 5
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, "abc")

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Uso: /recap" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_n", ["0", "-1", "-100", "51", "1000"])
async def test_recap_out_of_range_reports_error(fake_session, capsys, bad_n):
    """/recap <n> where n is outside [1, 50] must report a usage error and skip the stream."""
    fake_session.history = [{"role": "user", "content": "x"}] * 5
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, bad_n)

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Uso: /recap" in out


@pytest.mark.asyncio
async def test_recap_empty_history_warns_and_skips_stream(fake_session, capsys):
    """/recap on an empty session must print a warning and not call the LLM."""
    fake_session.history = []
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, "5")

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "vacía" in out


@pytest.mark.asyncio
async def test_recap_clamps_n_to_history_length(fake_session):
    """/recap 50 on a 3-message history must clamp the prompt to n=3."""
    fake_session.history = [{"role": "user", "content": f"msg-{i}"} for i in range(3)]
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, "50")

    mock_stream.assert_awaited_once_with(
        fake_session,
        "Resumí las últimas 3 rondas de la conversación de forma concisa.",
    )