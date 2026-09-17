import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lilith_cli import repl
from lilith_cli.config import YggdrasilConfig
from lilith_cli.work_session import track_stream, restore_session, verification
from lilith_cli.work_memory import remember, forget, records, context


def session(root):
    return SimpleNamespace(config=YggdrasilConfig(memory={"enabled": False}),
        history=[{"role": "user", "content": "task"}], total_usage={},
        _per_model_usage={}, _project_root=str(root))


def test_delegation_identity_survives_real_snapshot_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    current = session(tmp_path)
    current._delegation_request_ids = {("vor_delegate", "call_one"): "a" * 32}
    path = repl._auto_save_conversation(current)
    assert path is not None
    saved = repl._load_conversation(path)
    reopened = session(tmp_path)
    restore_session(reopened, saved, tmp_path)
    assert reopened._delegation_request_ids == current._delegation_request_ids


@pytest.mark.parametrize("snapshot", [None, {"version": True, "entries": []},
    {"version": 1, "entries": [{"tool": "vor_delegate", "call_id": "one", "request_id": "../secret"}]},
    {"version": 1, "entries": [{"tool": "vor_delegate", "call_id": "one", "request_id": "a" * 32}] * 2}])
def test_invalid_identity_snapshot_cannot_partially_restore_session(tmp_path, snapshot):
    reopened = session(tmp_path)
    before = list(reopened.history)
    saved = {"project_root": str(tmp_path), "messages": [], "delegation_requests": snapshot}
    with pytest.raises(ValueError):
        restore_session(reopened, saved, tmp_path)
    assert reopened.history == before


def test_invalid_runtime_keys_preserve_previous_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    current = session(tmp_path)
    path = repl._auto_save_conversation(current)
    assert path is not None
    original = path.read_bytes()
    current._delegation_request_ids = {("vor_delegate", "call"): "invalid"}
    assert repl._auto_save_conversation(current) is None
    assert path.read_bytes() == original


def test_invalid_usage_does_not_partially_restore_work_session(tmp_path):
    current = session(tmp_path)
    current._delegation_request_ids = {("vor_delegate", "old"): "a" * 32}
    before = list(current.history)
    with pytest.raises(ValueError):
        restore_session(current, {"project_root": str(tmp_path), "messages": [],
                                  "usage": {"total_tokens": "bad"}}, tmp_path)
    assert current.history == before
    assert current._delegation_request_ids == {("vor_delegate", "old"): "a" * 32}


@pytest.mark.asyncio
async def test_checkpoint_contains_tool_result_before_safe_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    current = session(tmp_path)
    current._pause_after_tools = 1
    async def events():
        yield {"type": "tool_call", "id": "write", "name": "file_write"}
        current.history.append({"role": "tool", "tool_call_id": "write", "content": "saved"})
        yield {"type": "tool_result", "id": "write", "name": "file_write"}
        pytest.fail("must stop before another model call")
    result = [event async for event in track_stream(current, events())]
    assert result[-1]["type"] == "cancelled"
    saved = json.loads((tmp_path / "conversations" / current._conversation_name).read_text())
    assert saved["messages"][-1]["content"] == "saved"
    assert saved["progress"]["status"] == "paused"
    reopened = session(tmp_path)
    restore_session(reopened, saved, tmp_path)
    assert reopened.history[-1]["tool_call_id"] == "write"


@pytest.mark.asyncio
async def test_unknown_tool_effect_blocks_automatic_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    current = session(tmp_path)
    async def events():
        yield {"type": "tool_call", "id": "write", "name": "file_write"}
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        async for _ in track_stream(current, events()):
            pass
    saved = json.loads((tmp_path / "conversations" / current._conversation_name).read_text())
    assert saved["progress"]["status"] == "interrupted"
    with pytest.raises(ValueError, match="resultado desconocido"):
        restore_session(session(tmp_path), saved, tmp_path)


@pytest.mark.asyncio
async def test_completed_stream_is_not_relabelled_when_consumer_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    current = session(tmp_path)
    async def events():
        yield {"type": "done", "content": "response"}
    stream = track_stream(current, events())
    assert (await anext(stream))["type"] == "done"
    await stream.aclose()
    assert current._run_progress["status"] == "responded"


def test_memory_is_editable_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("LILITH_PREFERENCES_DB", str(tmp_path / "prefs.sqlite3"))
    remember("style", "Nordic")
    remember("style", "Minimal", project=str(tmp_path / "one"))
    assert "Minimal" in context(tmp_path / "one")
    assert "Minimal" not in context(tmp_path / "two")
    remember("style", "Anime")
    assert records()[0]["value"] == "Anime"
    forget("style")
    assert records() == []
    assert records(str(tmp_path / "one"))[0]["value"] == "Minimal"


def test_silent_zero_exit_does_not_certify_verification(tmp_path):
    import sys
    assert not verification(f'"{sys.executable}" -c "pass"', tmp_path)["passed"]
    assert verification(f'"{sys.executable}" -c "print(123)"', tmp_path)["passed"]


@pytest.mark.asyncio
async def test_resume_reuses_confirmed_write_without_reexecuting(tmp_path, monkeypatch):
    from lilith_cli.agent import AgentSession
    from lilith_cli.providers import ToolCall, ToolResult
    from unittest.mock import AsyncMock
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YGGDRASIL_PROVIDER_HEALTH_DB", str(tmp_path / "health.sqlite3"))
    cfg = YggdrasilConfig(memory={"enabled": False}, confirm_write=False)
    first = AgentSession(cfg)
    first._progress_enabled = True
    target = tmp_path / "result.txt"
    writes = []
    async def apply(call):
        writes.append(call)
        target.write_text("done", encoding="utf-8")
        return ToolResult(call.id, call.name, "File written")
    first._execute_tool_impl = apply
    call = ToolCall("one", "file_write", {"path": str(target), "content": "done"})
    await first.execute_tool(call)
    reopened = AgentSession(cfg)
    reopened._execute_tool_impl = AsyncMock(side_effect=AssertionError("must not replay"))
    restore_session(reopened, {"project_root": str(tmp_path), "messages": [],
        "tool_receipts": first._tool_receipts}, tmp_path, replay_guard=True)
    result = await reopened.execute_tool(call)
    assert "no se repiti" in result.content
    assert len(writes) == 1
    target.write_text("external change", encoding="utf-8")
    assert "inspección requerida" in (await reopened.execute_tool(call)).content
    assert target.read_text(encoding="utf-8") == "external change"


@pytest.mark.asyncio
async def test_deepseek_thinking_toggle_applies_to_both_transports(tmp_path, monkeypatch):
    import httpx
    from lilith_cli.providers import LLMProviderWrapper
    monkeypatch.setenv("YGGDRASIL_PROVIDER_HEALTH_DB", str(tmp_path / "health.sqlite3"))
    cfg = YggdrasilConfig(provider="deepseek", providers={"deepseek": {
        "model": "fixture", "base_url": "https://api.deepseek.com/v1", "thinking_enabled": False}})
    seen = []
    def reply(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert payload["thinking"] == {"type": "disabled"}
        if payload.get("stream"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})
    provider = LLMProviderWrapper(cfg)
    provider._client = httpx.AsyncClient(base_url="https://api.deepseek.com/v1", transport=httpx.MockTransport(reply))
    try:
        assert (await provider.complete([{"role": "user", "content": "test"}]))["content"] == "OK"
        assert [event async for event in provider.stream([{"role": "user", "content": "test"}])]
        assert len(seen) == 2
    finally:
        await provider.close()
