"""Regression contracts for the conversational entry; no real provider calls."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_default_is_agent_conversation_not_task_form(monkeypatch):
    from lilith_cli import main
    calls = []
    monkeypatch.setattr(main, 'home', lambda **kw: pytest.fail('must not open task forms'))
    monkeypatch.setattr(main, 'start', lambda **kw: calls.append(kw))
    main.default_command()
    assert calls == [{'profile': 'agent'}]


def test_explicit_config_stays_explicit_in_conversation(monkeypatch):
    from lilith_cli import main
    calls = []
    monkeypatch.setattr(main, 'start', lambda **kw: calls.append(kw))
    main.default_command(config_path='selected.yaml')
    assert calls == [{'config': 'selected.yaml', 'profile': 'agent'}]


def test_agent_profile_has_discovery_tests_and_skills_without_privilege_tools(tmp_path, monkeypatch):
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig
    from lilith_cli.robust_kit import configure_session
    monkeypatch.chdir(tmp_path)
    cfg = YggdrasilConfig(memory={'enabled': False}, max_iterations=7)
    session = AgentSession(cfg, provider=MagicMock())
    configure_session(session, 'agent')
    names = {d['name'] for d in session.get_tool_descriptions()}
    assert {'directory_list', 'grep_files', 'file_read', 'file_edit', 'run_test', 'run_linter', 'skill_catalog', 'skill_read'} <= names
    assert {'mission_prepare', 'mission_court', 'mission_compute', 'mission_delegate', 'mission_skill_run', 'orchestration_state'} <= names
    assert not {'coding', 'bg_start', 'blender_exec', 'vor_delegate', 'huginn_delegate', 'git_operation', 'browser'} & names
    assert cfg.max_iterations == 7  # never enlarge a user's budget
    assert cfg.confirm_write is True


def test_agent_profile_cannot_bypass_model_calibration(tmp_path, monkeypatch):
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig
    from lilith_cli.robust_kit import configure_session
    monkeypatch.chdir(tmp_path)
    cfg = YggdrasilConfig(memory={'enabled': False}, require_calibration_for_edits=True)
    session = AgentSession(cfg, provider=MagicMock())
    configure_session(session, 'agent')
    names = {d['name'] for d in session.get_tool_descriptions()}
    assert {'skill_catalog', 'skill_read', 'file_read', 'directory_list'} <= names
    assert not {'file_write', 'file_edit', 'run_test', 'run_linter', 'memory_save'} & names


def test_console_preparation_preserves_model_permissions_and_history(tmp_path):
    from lilith_cli.agent_console import prepare_console
    from lilith_cli.config import YggdrasilConfig
    cfg = YggdrasilConfig(memory={'enabled': False}, provider='local', model='unchanged', confirm_write=True)
    session = SimpleNamespace(config=cfg, system_prompt='Original persona', history=[{'role': 'user', 'content': 'prior'}])
    before = cfg.model_dump()
    prepare_console(session, tmp_path)
    prepare_console(session, tmp_path)
    assert cfg.model_dump() == before
    assert session.history == [{'role': 'user', 'content': 'prior'}]
    assert session.system_prompt.count('LILITH_AGENT_WORKFLOW_V2') == 1
    assert session.system_prompt.startswith('Original persona')
    assert session._console_style == 'agent'


def test_capabilities_report_real_token_not_agent_mode(monkeypatch):
    from lilith_cli import agent_console as ui
    from lilith_cli.config import YggdrasilConfig
    cfg = YggdrasilConfig(memory={'enabled': False}, agent_mode='auto-edit')
    session = SimpleNamespace(config=cfg, agent_mode='auto-edit', get_tool_descriptions=lambda: [{'name': 'file_read'}])
    monkeypatch.setattr(ui, 'windows_admin_status', lambda: False)
    data = ui.capabilities(session)
    assert data['windows_admin'] is False
    assert data['agent_mode'] == 'auto-edit'
    assert data['tools'] == ['file_read']
    assert data['os_sandbox'] is False
    assert data['model_calls'] == 0
    assert 'api_key' not in json.dumps(data)


def test_prompt_uses_operator_identity_not_model():
    from lilith_cli.agent_console import prompt_fragments
    assert 'deepseek' not in str(prompt_fragments())
    assert "\u203a T\u00fa" in str(prompt_fragments())


def test_bundled_skill_read_is_content_not_execution():
    from lilith_cli.agent_skills import SkillCatalogTool, SkillReadTool
    listing = SkillCatalogTool().execute()
    assert listing.success
    assert any(row['name'] == 'project-map' for row in listing.data['skills'])
    result = SkillReadTool().execute(name='project-map')
    assert result.success and result.data['content']
    assert len(result.data['sha256']) == 64
    assert result.data['executed'] is False
    assert result.data['grants_permissions'] is False


@pytest.mark.parametrize('name', ['../../config.yaml', 'C:\\secrets', '', 'unknown-skill'])
def test_skill_names_cannot_address_arbitrary_files(name):
    from lilith_cli.agent_skills import SkillReadTool
    assert not SkillReadTool().execute(name=name).success


def test_natural_language_verification_is_rejected_before_provider(tmp_path, monkeypatch):
    from lilith_cli import config, work_session
    monkeypatch.setattr(config, 'load_config', lambda *a: pytest.fail('must fail before model/config'))
    with pytest.raises(SystemExit, match='verific'):
        work_session.task('Investiga los pendientes', root=str(tmp_path), verify='quiero que me digas que tenemos pendiente')


def test_invalid_task_spec_has_no_run_directory(tmp_path):
    from lilith_cli.task_workspace import TaskRun, TaskSpec
    with pytest.raises(ValueError, match='verific'):
        TaskRun(TaskSpec(tmp_path, 'Investiga', ['README.md'], verify='quiero que me digas que tenemos pendiente'), directory=tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


def test_verification_parser_preserves_quoted_windows_interpreter(tmp_path):
    from lilith_cli.verification_input import verification_argv
    argv = verification_argv(f'"{sys.executable}" -B -c "print(123)"', tmp_path)
    assert Path(argv[0]).resolve() == Path(sys.executable).resolve()
    assert argv[1:] == ['-B', '-c', 'print(123)']


@pytest.mark.parametrize('command', ['  ', '"unterminated', 'not-a-real-command--lilith-test --flag'])
def test_invalid_verification_has_actionable_error(tmp_path, command):
    from lilith_cli.verification_input import verification_argv
    with pytest.raises(ValueError, match='verific'):
        verification_argv(command, tmp_path)


def test_start_selects_agent_profile_and_console_with_actual_session(tmp_path, monkeypatch):
    from lilith_cli import hearth, repl, session_runtime
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig
    cfg = YggdrasilConfig(provider='local', model='fixture', base_url='http://localhost/v1', memory={'enabled': False})
    monkeypatch.setattr(hearth.config_module, 'load_config', lambda path: cfg)
    provider = MagicMock(close=AsyncMock())
    monkeypatch.setattr(session_runtime, 'create_session', lambda config: AgentSession(config, provider=provider))
    seen = []
    async def run(session):
        seen.append(session)
        assert session._execution_profile == 'agent'
        assert session._console_style == 'agent'
        assert 'LILITH_AGENT_WORKFLOW_V2' in session.system_prompt
        assert Path(session._project_root) == tmp_path
    monkeypatch.setattr(repl, 'run_repl', run)
    hearth.start(str(tmp_path), profile='agent')
    assert len(seen) == 1
    provider.close.assert_awaited_once()
