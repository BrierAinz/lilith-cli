"""Tests for the /recap slash command.

Behavior sourced from ``extra_commands.py:run_recap_command``:
    - No args → uses n = 5.
    - Args parses to ``int(text)`` (ValueError → usage error).
    - Awaits ``_stream_agent_reply(session, prompt)`` with prompt =
      ``f"Resumí las últimas {n} rondas de la conversación de forma concisa."``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lilith_cli.extra_commands import run_recap_command


@pytest.mark.asyncio
async def test_recap_default_uses_five(fake_session):
    """/recap (no args) must default to n=5 in the awaited prompt."""
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
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_recap_command(fake_session, "abc")

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Uso: /recap" in out