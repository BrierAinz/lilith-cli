"""Tests for FIX 1/2/3/4 in this commit.

- (a) Truncated JSON tool-call arguments must NOT execute the tool, and
  the resulting tool message must contain an explanatory error.
- (b) ``_resolve_max_tokens`` precedence: call kwarg > provider profile >
  global config.
- (c) ``delegate.execute`` always appends the no-tools system line.
- (d) Rich escape in render_tool_result.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_pg = str(Path(__file__).resolve().parent.parent)
if _pg not in sys.path:
    sys.path.insert(0, _pg)

from lilith_cli.providers import LLMProviderWrapper
from lilith_cli.config import YggdrasilConfig, ProviderProfile
from lilith_cli.agent import AgentSession, ToolResult
from lilith_cli import render


# ── (a) Truncated tool arguments are not executed ────────────────────


class _TruncatedArgsProvider:
    """First turn: tool call with broken JSON args + finish_reason=length.

    Second turn: plain stop (we don't care what it says).
    """

    def __init__(self) -> None:
        self.turn = 0
        self.config = MagicMock(model="local-model", temperature=1.0)

    async def stream(self, messages, tools=None, **kwargs):
        self.turn += 1
        if self.turn == 1:
            yield {
                "content": "",
                "finish_reason": "length",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "name": "todo_add",
                        "arguments": '{"text": "oops, truncado en el medio, sin cerrar',
                    }
                ],
            }
        else:
            yield {"content": "ok", "finish_reason": "stop", "tool_calls": None}


@pytest.mark.asyncio
async def test_truncated_args_skips_tool_and_returns_explanatory_message():
    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)
    session.provider = _TruncatedArgsProvider()
    session._tools_enabled = True
    session.get_tool_descriptions = lambda: [
        {"name": "todo_add", "description": "agrega un todo"}
    ]
    session.get_openai_tools = lambda: [
        {"type": "function", "function": {"name": "todo_add"}}
    ]
    session._init_tools = lambda: None

    executed: list[tuple[str, dict]] = []

    async def _fake_execute(tc):
        executed.append((tc.name, tc.arguments))
        return ToolResult(tool_call_id=tc.id, name=tc.name, content="ok")

    session.execute_tool = _fake_execute

    tool_results: list[dict] = []
    async for event in session.process_message_stream("test"):
        if event.get("type") == "tool_result":
            tool_results.append(event)

    # The tool must NOT have been executed.
    assert executed == []
    # The user (model) must have received an explanatory tool result.
    assert len(tool_results) == 1
    msg = tool_results[0]["content"]
    assert "todo_add" in msg
    assert "JSON" in msg or "truncado" in msg
    # finish_reason=length is surfaced.
    assert "length" in msg

    # And the explanatory message is also appended to history as a tool role.
    last_tool_msgs = [m for m in session.history if m.get("role") == "tool"]
    assert last_tool_msgs, "history must contain the explanatory tool message"
    assert "todo_add" in last_tool_msgs[-1]["content"]


# ── (b) max_tokens precedence ───────────────────────────────────────


def test_resolve_max_tokens_precedence():
    cfg = YggdrasilConfig(
        provider="local",
        model="local-model",
        max_tokens=4096,
        providers={
            "local": ProviderProfile(
                api_key="x", base_url="http://x", model="m", max_tokens=8192
            ),
        },
    )
    w = LLMProviderWrapper(cfg)

    # Provider profile wins over the global config default.
    assert w._resolve_max_tokens() == 8192

    # Explicit kwarg wins over the provider profile.
    assert w._resolve_max_tokens({"max_tokens": 16384}) == 16384

    # Provider without an override falls back to the global config.
    cfg2 = YggdrasilConfig(
        provider="local",
        model="local-model",
        max_tokens=4096,
        providers={
            "local": ProviderProfile(api_key="x", base_url="http://x", model="m"),
        },
    )
    w2 = LLMProviderWrapper(cfg2)
    assert w2._resolve_max_tokens() == 4096


def test_resolve_max_tokens_when_provider_has_none():
    cfg = YggdrasilConfig(
        provider="local",
        model="local-model",
        max_tokens=4096,
        providers={
            "local": ProviderProfile(
                api_key="x", base_url="http://x", model="m"
            ),
        },
    )
    w = LLMProviderWrapper(cfg)
    assert w._resolve_max_tokens() == 4096


# ── (c) delegate.execute appends the no-tools system line ────────────


def test_delegate_injects_no_tools_system_line(monkeypatch):
    """Smoke test: delegate.execute adds the no-tools line to the system prompt."""
    from lilith_tools import delegate as delegate_mod

    captured: dict = {}

    class _FakeProvider:
        def __init__(self, cfg):
            self.cfg = cfg
        async def complete(self, messages, **_):
            captured["messages"] = messages
            return {
                "content": "respuesta del sub-agente",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                "finish_reason": "stop",
                "model": self.cfg.model,
            }
        async def close(self):
            pass

    def _fake_load_config():
        cfg = YggdrasilConfig(provider="local", model="local-model")
        cfg.providers = {"local": ProviderProfile(api_key="x", base_url="http://x", model="local-model")}
        return cfg

    # LLMProviderWrapper is imported lazily inside delegate.execute; patch
    # the symbol on its actual import site (``lilith_cli.providers``).
    from lilith_cli import providers as _providers_mod
    from lilith_cli import config as _config_mod

    monkeypatch.setattr(_providers_mod, "LLMProviderWrapper", _FakeProvider)
    monkeypatch.setattr(_config_mod, "load_config", _fake_load_config)
    monkeypatch.setattr(
        "lilith_cli.main._load_subagent_presets",
        lambda config_path=None: {
            "fake-preset": {
                "provider": "local",
                "model": "local-model",
                "temperature": 0.5,
                "system_prompt": "You are a fake preset.",
            }
        },
    )

    tool = delegate_mod.DelegateSubagentTool()
    result = tool.execute(preset="fake-preset", prompt="hola")

    assert result.success, result.error
    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert "no tienes herramientas" in sys_msg["content"]
    assert "You are a fake preset." in sys_msg["content"]


# ── (d) Rich escape applied to default render path ───────────────────


def test_render_tool_result_escapes_default_brackets():
    out = render.render_tool_result("unknown_tool", "[bold]danger[/bold]")
    # ``rich.markup.escape`` doubles the brackets so Rich doesn't try to
    # interpret them as markup. That's the whole point of the fix.
    s = str(out)
    assert "\\[bold]danger\\[/bold]" in s
    # Sanity: brackets were actually transformed (the literal un-escaped
    # form must not survive).
    assert "[bold]danger[/bold]" not in s
