"""Serial execution must preserve completed effects at each checkpoint."""
import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['none', 'cancel', 'pause', 'close'])
async def test_serial_results_are_saved_before_next_tool(tmp_path, monkeypatch, stop):
    from lilith_cli.agent import AgentSession, ToolResult, _drop_orphan_tool_messages
    from lilith_cli.config import YggdrasilConfig

    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    rounds = 0

    async def stream(*args, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield {'tool_calls': [
                {'id': 'first', 'name': 'file_read', 'arguments': {'path': 'first'}},
                {'id': 'second', 'name': 'file_read', 'arguments': {'path': 'second'}},
            ]}
        else:
            yield {'content': 'Finished', 'finish_reason': 'stop'}

    provider.stream = stream
    cfg = YggdrasilConfig(memory={'enabled': False}, history={'save': False})
    session = AgentSession(cfg, provider=provider)
    session._serial_tools = True
    session._progress_enabled = True
    if stop == 'pause':
        session._pause_after_tools = 1
    executed = []
    cancel = asyncio.Event()

    async def execute(call):
        if executed:
            assert session.history[-2]['role'] == 'tool'
            assert session.history[-2]['tool_call_id'] == 'first'
        executed.append(call.id)
        if stop == 'cancel':
            cancel.set()
        return ToolResult(call.id, call.name, 'Read ' + call.id)

    monkeypatch.setattr(session, 'execute_tool', execute)
    events = []
    iterator = session.process_message_stream('Read both', cancel_event=cancel)
    try:
        async for event in iterator:
            events.append(event)
            if event['type'] == 'tool_result':
                assert session._run_progress['pending'] == []
                assert _drop_orphan_tool_messages(session.history) == session.history
                if stop == 'close':
                    break
    finally:
        await iterator.aclose()

    expected = ['first', 'second'] if stop == 'none' else ['first']
    assert executed == expected
    assert [e['id'] for e in events if e['type'] == 'tool_call'] == expected
    assert [e['id'] for e in events if e['type'] == 'tool_result'] == expected
    assert _drop_orphan_tool_messages(session.history) == session.history
    assert session._run_progress['pending'] == []
    if stop in ('cancel', 'pause'):
        assert events[-1]['type'] == 'cancelled'
    if stop == 'none':
        assert events[-1]['type'] == 'done'


@pytest.mark.asyncio
async def test_invalid_json_feedback_reaches_model_and_corrected_call_runs(tmp_path, monkeypatch):
    from lilith_cli.agent import AgentSession, ToolResult, _drop_orphan_tool_messages
    from lilith_cli.config import YggdrasilConfig

    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    rounds = 0

    async def stream(messages, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield {'tool_calls': [{'id': 'truncated', 'name': 'file_read',
                                  'arguments': '{"path":'}], 'finish_reason': 'length'}
        elif rounds == 2:
            assert any('no fueron JSON v\u00e1lido' in str(m.get('content')) for m in messages)
            assert any("finish_reason='length'" in str(m.get('content')) for m in messages)
            assert not any(m.get('role') == 'tool' for m in messages)
            yield {'tool_calls': [{'id': 'fixed', 'name': 'file_read',
                                  'arguments': {'path': 'README.md'}}]}
        else:
            assert any(m.get('tool_call_id') == 'fixed' for m in messages)
            yield {'content': 'Read successfully', 'finish_reason': 'stop'}

    provider.stream = stream
    cfg = YggdrasilConfig(memory={'enabled': False}, history={'save': False})
    session = AgentSession(cfg, provider=provider)
    session._serial_tools = True
    session._progress_enabled = True
    executed = []

    async def execute(call):
        executed.append(call.id)
        return ToolResult(call.id, call.name, 'README content')

    monkeypatch.setattr(session, 'execute_tool', execute)
    events = [event async for event in session.process_message_stream('Read README')]
    assert executed == ['fixed']
    assert rounds == 3
    assert events[-1]['type'] == 'done'
    assert session._run_progress['tool_errors'] == 1
    assert session._run_progress['pending'] == []
    assert _drop_orphan_tool_messages(session.history) == session.history
