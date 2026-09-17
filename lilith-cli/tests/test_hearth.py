"""User journeys for standalone configuration, project work and recovery."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from lilith_cli import hearth, repl, session_runtime
from lilith_cli.config import YggdrasilConfig


@pytest.fixture(autouse=True)
def isolated_health(tmp_path, monkeypatch):
    monkeypatch.setenv("YGGDRASIL_PROVIDER_HEALTH_DB", str(tmp_path / "health.sqlite3"))


def test_setup_preserves_existing_settings_and_secret_references(tmp_path):
    path = tmp_path / "config.yaml"
    original = "api_key: ${OLD_KEY}\nsystem_prompt: Mi personalidad\nproviders: {}\n"
    path.write_text(original, encoding="utf-8")
    hearth.configure(path, model="my-model", base_url="https://example.com/v1", key_env="MY_KEY")
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["system_prompt"] == "Mi personalidad"
    assert saved["providers"]["local"]["api_key"] == "${MY_KEY}"
    assert saved["model"] == "my-model"
    assert next(tmp_path.glob("*.bak")).read_text(encoding="utf-8") == original


def test_personalization_preserves_provider_and_retains_backup(tmp_path):
    path = tmp_path / "config.yaml"
    original = "provider: custom\napi_key: ${MY_KEY}\nsystem_prompt: previous\n"
    path.write_text(original, encoding="utf-8")
    hearth.persona(apply=True, config=str(path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["provider"] == "custom"
    assert raw["api_key"] == "${MY_KEY}"
    assert raw["system_prompt"] == hearth.LILITH_PERSONA
    assert next(tmp_path.glob("*.bak")).read_text(encoding="utf-8") == original


@pytest.mark.parametrize("url,key", [("ftp://server", None), ("http://example.com/v1", "KEY"),
    ("https://user:pass@example.com/v1", "KEY"), ("https://example.com/v1?key=x", "KEY"),
    ("https://example.com/v1", None), ("https://example.com/v1", "secret-value")])
def test_setup_rejects_invalid_endpoints_without_writing(tmp_path, url, key):
    path = tmp_path / "config.yaml"
    with pytest.raises(ValueError):
        hearth.configure(path, model="m", base_url=url, key_env=key)
    assert not path.exists()


def _session():
    return SimpleNamespace(config=YggdrasilConfig(memory={"enabled": False}),
        history=[{"role": "user", "content": "Arregla mi proyecto"}], total_usage={},
        _per_model_usage={})


def test_autosave_updates_one_snapshot_and_honors_disabled_history(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    session = _session()
    first = repl._auto_save_conversation(session)
    session.history.append({"role": "assistant", "content": "Comprobado"})
    assert repl._auto_save_conversation(session) == first
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(json.loads(first.read_text(encoding="utf-8"))["messages"]) == 2
    session.config.history.save = False
    assert repl._auto_save_conversation(session) is None


def test_failed_snapshot_preserves_previous_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    session = _session()
    path = repl._auto_save_conversation(session)
    previous = path.read_bytes()
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(Path, "replace", fail)
    session.history.append({"role": "assistant", "content": "new"})
    assert repl._auto_save_conversation(session) is None
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))


def test_start_loads_project_config_then_resumes_and_restores_cwd(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", conversations)
    (conversations / "conv_test.json").write_text(json.dumps({
        "project_root": str(project.resolve()),
        "messages": [{"role": "user", "content": "previous task"}]}), encoding="utf-8")
    session = _session()
    session.provider = SimpleNamespace(close=AsyncMock())
    def load(path):
        assert Path.cwd() == project
        return YggdrasilConfig(provider="local", model="fixture", base_url="http://localhost:1234/v1")
    monkeypatch.setattr(hearth.config_module, "load_config", load)
    monkeypatch.setattr(session_runtime, "create_session", lambda cfg: session)
    async def run(actual):
        assert actual.history[0]["content"] == "previous task"
        assert Path.cwd() == project
    monkeypatch.setattr(repl, "run_repl", run)
    previous = Path.cwd()
    hearth.start(str(project), resume="conv_test")
    assert Path.cwd() == previous
    session.provider.close.assert_awaited_once()


def test_cross_project_resume_is_rejected_before_config_or_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    (tmp_path / "conv_wrong.json").write_text(json.dumps({
        "project_root": "another-project", "messages": []}), encoding="utf-8")
    def fail(*args):
        pytest.fail("must not load config")
    monkeypatch.setattr(hearth.config_module, "load_config", fail)
    with pytest.raises(SystemExit, match="otro proyecto"):
        hearth.start(str(tmp_path), resume="conv_wrong")


@pytest.mark.parametrize("explicit,saved_profile,expected", [
    ("coordinator", None, "coordinator"), (None, "coordinator", "coordinator"),
    ("standard", "coordinator", "standard"),
])
def test_start_applies_execution_profile_not_provider_profile(tmp_path, monkeypatch, explicit, saved_profile, expected):
    from unittest.mock import MagicMock
    from lilith_cli.agent import AgentSession
    from lilith_cli.robust_kit import PROFILES

    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    if saved_profile:
        (tmp_path / "conv_profile.json").write_text(json.dumps({
            "project_root": str(tmp_path), "messages": [{"role": "user", "content": "saved"}],
            "execution_profile": saved_profile,
        }))
    cfg = YggdrasilConfig(provider="local", model="fixture", base_url="http://localhost:1234/v1",
                         memory={"enabled": False}, providers={"local": {"model": "fixture-profile"}})
    monkeypatch.setattr(hearth.config_module, "load_config", lambda path: cfg)
    provider = MagicMock(close=AsyncMock())
    monkeypatch.setattr(session_runtime, "create_session", lambda config: AgentSession(config, provider=provider))
    async def run(current):
        assert current._execution_profile == expected
        assert {tool["name"] for tool in current.get_tool_descriptions()} <= PROFILES[expected]["tools"]
    monkeypatch.setattr(repl, "run_repl", run)
    hearth.start(str(tmp_path), profile=explicit, resume="conv_profile" if saved_profile else None)
    provider.close.assert_awaited_once()


def test_autosave_retains_execution_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    current = _session()
    current._execution_profile = "coordinator"
    path = repl._auto_save_conversation(current)
    assert json.loads(path.read_text())["execution_profile"] == "coordinator"


def test_hearth_filters_sessions_to_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    session = _session()
    session._project_root = str(tmp_path.resolve())
    repl._auto_save_conversation(session)
    hearth.home(str(tmp_path))
    output = capsys.readouterr().out
    assert "HOGUERA" in output
    assert "Arregla mi proyecto" in output


def test_real_agent_reads_project_file_and_answers(tmp_path, monkeypatch):
    from lilith_cli.agent import AgentSession
    from lilith_cli.providers import LLMProviderWrapper
    import httpx

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "state.json"))
    (tmp_path / "brief.txt").write_text("Una saga nórdica", encoding="utf-8")
    cfg = YggdrasilConfig(provider="local", model="fixture", base_url="http://localhost/v1",
                          memory={"enabled": False}, retry_max=0,
                          tools={"coding": False, "web_search": False, "browser": False, "system": False})
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            delta = {"tool_calls": [{"index": 0, "id": "call_read", "type": "function",
                "function": {"name": "file_read", "arguments": json.dumps({"path": "brief.txt"})}}]}
            delta["reasoning_content"] = "provider protocol context"
            finish = "tool_calls"
        else:
            assert any(m.get("role") == "tool" and "saga" in m.get("content", "") for m in body["messages"])
            assert any(m.get("role") == "assistant" and m.get("reasoning_content") == "provider protocol context" for m in body["messages"])
            delta, finish = {"content": "El proyecto trata de una saga nórdica."}, "stop"
        sse = "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})
    async def run():
        provider = LLMProviderWrapper(cfg)
        provider._client = httpx.AsyncClient(base_url=cfg.base_url, transport=httpx.MockTransport(respond))
        session = AgentSession(cfg, provider=provider)
        try:
            events = [event async for event in session.process_message_stream("Lee brief.txt y resúmelo")]
            assert events[-1]["type"] == "done"
            assert "saga nórdica" in events[-1]["content"]
            assert len(requests) == 2
        finally:
            await provider.close()
    asyncio.run(run())
