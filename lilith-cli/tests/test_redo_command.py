"""Tests for the /redo slash command.

The command delegates the actual LLM turn to the module-level helper
``_stream_agent_reply(session, text)``. These tests patch that helper
with an ``AsyncMock`` and assert on what prompt text it is awaited with.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lilith_cli.extra_commands import run_redo_command


@pytest.mark.asyncio
async def test_redo_resends_last_user_message(fake_session):
    """/redo must await _stream_agent_reply with session._last_user_message."""
    fake_session._last_user_message = "previous prompt"

    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_redo_command(fake_session, "")

    mock_stream.assert_awaited_once_with(fake_session, "previous prompt")


@pytest.mark.asyncio
async def test_redo_with_args_reports_error(fake_session, capsys):
    """/redo with extra args must render a usage error and never call _stream_agent_reply."""
    fake_session._last_user_message = "previous prompt"

    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_redo_command(fake_session, "extra")

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Uso: /redo" in out


@pytest.mark.asyncio
async def test_redo_without_last_message_reports_error(fake_session, capsys):
    """/redo on a session without _last_user_message must report an error and skip the stream."""
    fake_session._last_user_message = ""

    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_redo_command(fake_session, "")

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "No hay un mensaje previo para reenviar" in out