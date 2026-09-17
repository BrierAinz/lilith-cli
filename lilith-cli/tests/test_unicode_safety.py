"""Regression coverage for lone surrogates entering a conversation."""

from __future__ import annotations

import json

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig


class _CapturingProvider:
    def __init__(self) -> None:
        self.messages = None

    async def stream(self, messages, model=None, tools=None, **kwargs):
        self.messages = messages
        # This is the operation that httpx performs before sending JSON.
        json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
        yield {"content": "ok", "finish_reason": "stop", "tool_calls": None}


@pytest.mark.asyncio
async def test_surrogate_de_entrada_no_contamina_estado_ni_payload():
    provider = _CapturingProvider()
    config = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(config, provider=provider)

    events = [event async for event in session.process_message_stream("antes\ud800despues")]

    assert events
    assert session.history[0]["content"] == "antes\ufffddespues"
    assert provider.messages is not None
    payload = json.dumps(
        {"messages": provider.messages}, ensure_ascii=False
    ).encode("utf-8")
    assert payload
    assert all(
        not (0xD800 <= ord(char) <= 0xDFFF)
        for message in session.history
        for char in str(message.get("content", ""))
    )


def test_autosave_recupera_historial_ya_contaminado(tmp_path, monkeypatch):
    from lilith_cli import repl

    config = YggdrasilConfig(
        provider="local",
        model="local-model",
        history={"save": True},
    )
    session = AgentSession(config, provider=_CapturingProvider())
    session.history = [{"role": "user", "content": "mal\udffftexto"}]
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")

    path = repl._auto_save_conversation(session)

    assert path is not None
    saved = path.read_text(encoding="utf-8")
    assert "mal\ufffdtexto" in saved
    assert not any(0xD800 <= ord(char) <= 0xDFFF for char in saved)
