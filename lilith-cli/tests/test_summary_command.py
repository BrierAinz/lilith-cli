"""Tests for the /summary slash command.

The command sends a fixed summarize prompt to ``_stream_agent_reply``.
The helper is patched with an ``AsyncMock`` so we can assert on the
exact prompt text without invoking the real provider.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lilith_cli.extra_commands import run_summary_command


@pytest.mark.asyncio
async def test_summary_uses_fixed_prompt(fake_session):
    """/summary must await _stream_agent_reply with the documented fixed prompt."""
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_summary_command(fake_session, "")

    mock_stream.assert_awaited_once_with(
        fake_session,
        "Resumí la conversación hasta ahora de forma concisa.",
    )


@pytest.mark.asyncio
async def test_summary_with_args_reports_error(fake_session, capsys):
    """/summary must reject any extra args and never call _stream_agent_reply."""
    with patch(
        "lilith_cli.extra_commands._stream_agent_reply",
        new_callable=AsyncMock,
    ) as mock_stream:
        await run_summary_command(fake_session, "extra")

    mock_stream.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Uso: /summary" in out