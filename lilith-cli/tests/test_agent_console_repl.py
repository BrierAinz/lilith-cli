"""Exercise actual REPL dispatch/presentation with input and model transport replaced."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_repl_receives_natural_request_without_setup_form_or_verification(tmp_path, monkeypatch):
    from lilith_cli import config, render, repl
    from lilith_cli.agent import AgentSession
    from lilith_cli.agent_console import prepare_console
    from lilith_cli.config import YggdrasilConfig
    from lilith_cli.robust_kit import configure_session

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('YGGDRASIL_ORCHESTRATION_STATE', str(tmp_path / 'state.json'))
    monkeypatch.setenv('LILITH_PREFERENCES_DB', str(tmp_path / 'preferences.sqlite3'))
    monkeypatch.setattr(repl, '_HISTORY_FILE', tmp_path / 'history')
    monkeypatch.setattr(repl, '_CONVERSATIONS_DIR', tmp_path / 'conversations')
    config_file = tmp_path / 'config.yaml'
    config_file.write_text('theme: cyberpunk\n', encoding='utf-8')
    monkeypatch.setattr(config, 'CONFIG_FILE', config_file)
    cfg = YggdrasilConfig(provider='local', model='fixture-model', memory={'enabled': False}, history={'save': False})
    session = AgentSession(cfg, provider=MagicMock())
    configure_session(session, 'agent')
    prepare_console(session, tmp_path)
    render.set_theme('lilith')
    inputs = iter(['/capabilities', '/kit show project-map', 'quiero que me digas que tenemos pendiente'])
    prompts = []
    settings = []
    received = []

    class InputFixture:
        def __init__(self, **kwargs):
            settings.append(kwargs)
        async def prompt_async(self, prompt, **kwargs):
            prompts.append(prompt)
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError from None

    async def stream(text, **kwargs):
        received.append(text)
        yield {'type': 'text', 'content': 'Pendientes revisados en la prueba de interfaz.'}
        yield {'type': 'done', 'content': 'Pendientes revisados en la prueba de interfaz.', 'usage': {}}

    monkeypatch.setattr(repl, 'PromptSession', InputFixture)
    monkeypatch.setattr(session, 'process_message_stream', stream)
    with render.console.capture() as capture:
        await repl.run_repl(session)
    output = capture.get()
    assert received == ['quiero que me digas que tenemos pendiente']
    assert settings[0]['bottom_toolbar'] is None
    assert all('Tú' in str(prompt) and 'fixture-model' not in str(prompt) for prompt in prompts)
    assert render.get_theme().name == 'lilith'
    assert 'QUEEN ORCHESTRATOR' in output
    assert 'windows_admin' in output
    assert 'Endpoint del modelo' not in output
    assert 'NUEVA SAGA' not in output
    assert not (tmp_path / 'conversations').exists()
