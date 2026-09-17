"""Real local tools + deterministic mock HTTP model, including fail/edit/retest.

This tests orchestration, not a real model's competence or account authentication.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest


@pytest.mark.asyncio
async def test_conversation_discovers_skill_reads_fails_edits_and_retests(tmp_path, monkeypatch):
    from lilith_cli.agent import AgentSession
    from lilith_cli.agent_console import prepare_console
    from lilith_cli.config import YggdrasilConfig
    from lilith_cli.providers import LLMProviderWrapper
    from lilith_cli.robust_kit import configure_session
    from lilith_tools import filesystem
    from lilith_tools.undo import UndoManager

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('YGGDRASIL_PROVIDER_HEALTH_DB', str(tmp_path / 'health.sqlite3'))
    monkeypatch.setenv('YGGDRASIL_ORCHESTRATION_STATE', str(tmp_path / 'state.json'))
    monkeypatch.setenv('LILITH_PREFERENCES_DB', str(tmp_path / 'preferences.sqlite3'))
    monkeypatch.setenv('TEMP', str(tmp_path))
    monkeypatch.setenv('TMP', str(tmp_path))
    monkeypatch.setattr(filesystem, 'UndoManager', lambda: UndoManager(tmp_path / 'undo'))
    (tmp_path / 'README.md').write_text('Pendiente: corregir add. Ejecuta los tests existentes; no los cambies.', encoding='utf-8')
    (tmp_path / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
    tests = tmp_path / 'tests'
    tests.mkdir()
    test_source = 'import unittest\nfrom calc import add\n\nclass AddTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n'
    test_file = tests / 'test_calc.py'
    test_file.write_text(test_source, encoding='utf-8')
    original_test_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
    command = subprocess.list2cmdline([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    actions = [
        ('directory_list', {'path': '.'}),
        ('skill_read', {'name': 'project-map'}),
        ('file_read', {'path': 'README.md'}),
        ('file_read', {'path': 'calc.py'}),
        ('run_test', {'path': '.', 'test_command': command, 'timeout': 15}),
        ('file_edit', {'path': 'calc.py', 'old_string': 'return a - b', 'new_string': 'return a + b', 'show_diff': False}),
        ('run_test', {'path': '.', 'test_command': command, 'timeout': 15}),
    ]
    requests = []
    test_observations = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        step = len(requests) - 1
        if step in (5, 7):
            result = next(m for m in reversed(body['messages']) if m.get('role') == 'tool')
            test_observations.append(result['content'])
        if step == 5:
            assert 'FAILED' in test_observations[-1]
        if step == 7:
            assert 'OK' in test_observations[-1]
        if step < len(actions):
            name, args = actions[step]
            assert name in {tool['function']['name'] for tool in body['tools']}
            delta = {'tool_calls': [{'index': 0, 'id': f'call_{step}', 'type': 'function',
                                    'function': {'name': name, 'arguments': json.dumps(args)}}]}
            finish = 'tool_calls'
        else:
            assert step == len(actions), 'unexpected automatic model calls'
            delta = {'content': 'Corregido add; prueba fallida antes y aprobada después. Tests originales intactos.'}
            finish = 'stop'
        text = 'data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': finish}]}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=text, headers={'Content-Type': 'text/event-stream'})

    cfg = YggdrasilConfig(provider='local', model='fixture', base_url='http://localhost/v1',
                         memory={'enabled': False}, history={'save': False}, confirm_write=False,
                         max_iterations=16, retry_max=0, tools={'retry_count': 0})
    provider = LLMProviderWrapper(cfg)
    provider._client = httpx.AsyncClient(base_url=cfg.base_url, transport=httpx.MockTransport(respond))
    session = AgentSession(cfg, provider=provider)
    configure_session(session, 'agent')
    prepare_console(session, tmp_path)
    try:
        events = [event async for event in session.process_message_stream('Investiga lo pendiente y resuelve el fallo sin cambiar los tests.')]
    finally:
        await provider.close()
    assert events[-1]['type'] == 'done'
    assert 'aprobada' in events[-1]['content']
    assert len(requests) == 8
    assert len(test_observations) == 2
    assert hashlib.sha256(test_file.read_bytes()).hexdigest() == original_test_hash
    assert 'return a + b' in (tmp_path / 'calc.py').read_text(encoding='utf-8')
    verified = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                              cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert verified.returncode == 0 and 'OK' in verified.stderr
    (tmp_path / 'loop-evidence.json').write_text(json.dumps({
        'model_transport': 'httpx.MockTransport', 'real_provider_calls': 0,
        'model_rounds': len(requests), 'real_tools': [name for name, _ in actions],
        'initial_check_failed': 'FAILED' in test_observations[0],
        'post_edit_check_passed': 'OK' in test_observations[1],
        'independent_check_returncode': verified.returncode,
        'protected_test_sha256': original_test_hash,
        'os_admin_change': False,
    }, indent=2), encoding='utf-8')
