"""Tests for the /compact slash command."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilith_cli.commands import CompactCommand


class _DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.memory = MagicMock()
        self.memory.enabled = False
        self.history = MagicMock()
        self.history.max_turns = 50


class _DummySession:
    def __init__(self, history: list | None = None) -> None:
        self.config = _DummyConfig()
        self.memory = None
        self.history = list(history) if history is not None else []
        self.provider = MagicMock()
        self.provider.complete = AsyncMock(return_value={"content": "Resumen de prueba."})
        self.system_prompt = ""
        self._total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def get_tool_descriptions(self):
        return []

    def compact_history(self, summary: str, keep_recent: int = 2) -> None:
        kept = self.history[-keep_recent * 2:] if keep_recent > 0 else []
        self.history = [
            {"role": "assistant", "content": f"[Resumen de la conversación anterior]\n{summary}"},
            *kept,
        ]

    def clear_history(self) -> None:
        self.history = []

    async def generate_compact_summary(self) -> str:
        response = await self.provider.complete(
            [
                {
                    "role": "system",
                    "content": "Eres un asistente que resume conversaciones de forma concisa y precisa.",
                },
                {"role": "user", "content": "Resume la conversación."},
            ],
            tools=None,
        )
        return response.get("content", "").strip()

    @property
    def total_usage(self):
        return self._total_usage


@pytest.mark.asyncio
async def test_compact_command_empty_history() -> None:
    """CompactCommand with empty history should return without error."""
    session = _DummySession(history=[])
    cmd = CompactCommand(session)
    assert cmd.name == "compact"
    await cmd.execute("")
    assert session.history == []
    session.provider.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_command_creates_summary() -> None:
    """CompactCommand should create a summary message and keep recent pairs."""
    history = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "¡Hola!"},
        {"role": "user", "content": "¿Cómo estás?"},
        {"role": "assistant", "content": "Bien."},
        {"role": "user", "content": "Adiós"},
        {"role": "assistant", "content": "Hasta luego."},
    ]
    session = _DummySession(history=history)
    cmd = CompactCommand(session)

    await cmd.execute("2")

    # Provider should have been asked to summarize.
    session.provider.complete.assert_awaited_once()
    call_args = session.provider.complete.call_args
    if "messages" in call_args.kwargs:
        call_messages = call_args.kwargs["messages"]
    else:
        call_messages = call_args.args[0]
    assert any(msg["role"] == "user" for msg in call_messages)

    # History should now contain a summary + recent messages.
    # 1 summary + 2 recent user messages + 2 recent assistant messages = 5.
    assert len(session.history) == 5
    summary_msg = session.history[0]
    assert summary_msg["role"] == "assistant"
    assert "[Resumen de la conversación anterior]" in summary_msg["content"]
    assert "Resumen de prueba" in summary_msg["content"]
    assert session.history[-1] == {"role": "assistant", "content": "Hasta luego."}


@pytest.mark.asyncio
async def test_compact_command_invalid_argument() -> None:
    """CompactCommand with non-numeric argument should report an error."""
    session = _DummySession(history=[{"role": "user", "content": "hi"}])
    cmd = CompactCommand(session)
    await cmd.execute("abc")
    # Invalid arg means no LLM call and history unchanged.
    session.provider.complete.assert_not_awaited()
    assert session.history == [{"role": "user", "content": "hi"}]
