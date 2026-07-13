"""Tests for live streaming tool progress in the REPL."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure lilith_cli is importable when running tests directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.tool_progress import ToolProgressTracker


@pytest.fixture
def fake_session():
    """Return a lightweight AgentSession with a mocked provider."""
    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)
    session.provider = MagicMock()
    session.provider.stream = AsyncMock(return_value=iter([]))
    return session


async def _fake_stream(events):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_process_with_streaming_tracks_tool_progress(fake_session, capsys):
    """The REPL handler tracks running/completed tools and renders a summary."""
    from lilith_cli.repl import _process_with_streaming

    events = [
        {"type": "tool_call", "name": "read_file", "arguments": {"path": "a.py"}},
        {"type": "tool_call", "name": "directory_list", "arguments": {"path": "."}},
        {"type": "tool_result", "name": "read_file", "content": "content a"},
        {"type": "tool_result", "name": "directory_list", "content": "[]"},
        {"type": "done", "content": "Hecho", "usage": {"total_tokens": 10}},
    ]
    fake_session.process_message_stream = lambda text, cancel_event=None: _fake_stream(events)

    with patch("lilith_cli.repl.render_tool_progress") as mock_render_progress, \
         patch("lilith_cli.repl.render_tool_call") as mock_render_call, \
         patch("lilith_cli.repl.render_tool_result") as mock_render_result, \
         patch("lilith_cli.repl.render_markdown") as mock_render_md, \
         patch("lilith_cli.repl.render_turn_end") as mock_turn_end:
        await _process_with_streaming(fake_session, "hola")

    # Summary line and final Markdown should be rendered.
    mock_turn_end.assert_called_once()


@pytest.mark.asyncio
async def test_process_with_streaming_handles_failed_tool(fake_session):
    """A tool result with an error flag is tracked as failed."""
    from lilith_cli.repl import _process_with_streaming

    events = [
        {"type": "tool_call", "name": "coding", "arguments": {"command": "true"}},
        {
            "type": "tool_result",
            "name": "coding",
            "content": "error output",
            "is_error": True,
        },
        {"type": "done", "content": "listo", "usage": {}},
    ]
    fake_session.process_message_stream = lambda text, cancel_event=None: _fake_stream(events)

    with patch("lilith_cli.repl.render_tool_call") as mock_render_call, \
         patch("lilith_cli.repl.render_tool_result") as mock_render_result, \
         patch("lilith_cli.repl.render_turn_end") as mock_turn_end, \
         patch("lilith_cli.tool_progress.console.print") as mock_console_print:
        await _process_with_streaming(fake_session, "run")

    mock_turn_end.assert_called_once()
    # Summary should be printed with an X because one tool failed.
    summary_calls = [c for c in mock_console_print.call_args_list if "herramienta" in str(c)]
    assert summary_calls
    assert "✗" in str(summary_calls[0])


def test_tool_progress_tracker_state_transitions():
    """ToolProgressTracker moves tools from running to completed/failed."""
    tracker = ToolProgressTracker()
    tracker.start("a")
    tracker.start("b")
    assert set(tracker.running) == {"a", "b"}

    tracker.complete("a")
    tracker.complete("b", error="boom")
    assert tracker.running == {}
    assert [name for name, _ in tracker.completed] == ["a"]
    assert [name for name, _ in tracker.failed] == ["b"]
    assert tracker.is_active() is True
