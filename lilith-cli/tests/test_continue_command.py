"""Tests for the /continue slash command.

The command builds a prompt starting with ``Continuá la respuesta anterior.``
and optionally appends the extra args text. The actual LLM turn is delegated
to the module-level helper ``_stream_agent_reply(session, text)`` which is
patched here with an ``AsyncMock``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lilith_cli.extra_commands import run_continue_command


@pytest.mark.asyncio
async def test_continue_default_prompt(fake_session):
    """/continue with no args must await _stream_agent_reply with the base prompt only."""
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_continue_command(fake_session, "")

    mock_stream.assert_awaited_once_with(
        fake_session, "Continuá la respuesta anterior."
    )


@pytest.mark.asyncio
async def test_continue_appends_extra_text(fake_session):
    """/continue <text> must append the args after a newline to the base prompt."""
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_continue_command(fake_session, "sigue hablando")

    mock_stream.assert_awaited_once_with(
        fake_session,
        "Continuá la respuesta anterior.\nsigue hablando",
    )


@pytest.mark.asyncio
async def test_continue_prompt_starts_with_base(fake_session):
    """The awaited prompt must always start with the documented prefix."""
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_continue_command(fake_session, "cualquier cosa")

    args, _ = mock_stream.call_args
    prompt = args[1]
    assert prompt.startswith("Continuá la respuesta anterior.")