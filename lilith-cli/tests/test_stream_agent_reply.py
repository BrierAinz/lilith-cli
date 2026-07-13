"""Tests for the ``_stream_agent_reply`` module-level helper.

The helper iterates over ``session.process_message_stream(text)`` and
prints any ``{"type": "text", "content": ...}`` chunk via ``console.print``
(no patching of console.print here — we drive the real Rich console and
rely on ``capsys`` to capture its stdout).
"""

from __future__ import annotations

import pytest

from lilith_cli.extra_commands import _stream_agent_reply


@pytest.mark.asyncio
async def test_stream_agent_reply_renders_text_chunks(fake_session, capsys):
    """Each ``text`` event must reach stdout; non-text events are ignored."""

    async def _fake_stream(_text: str):
        yield {"type": "text", "content": "hola "}
        yield {"type": "text", "content": "mundo"}
        yield {"type": "done"}

    fake_session.process_message_stream = _fake_stream

    await _stream_agent_reply(fake_session, "ignored prompt")

    out = capsys.readouterr().out
    assert "hola" in out
    assert "mundo" in out