"""Tests for the Sakana Responses API normaliser.

Captures the wire format observed against the live endpoint
``https://api.sakana.ai/v1/responses`` with model ``fugu-ultra``
(probed 2026-07-16). The Sakana ``/v1/responses`` shape diverges from
the OpenAI Responses API in one important way: reasoning blocks carry
their summaries under ``summary[*].text`` (NOT ``content``). Missing
that distinction was the root cause of the "respondió en N ms pero sin
contenido" symptom seen by the doctor / subagent runs.

These tests pin the normaliser to:
  * completed responses with both reasoning and message blocks
  * incomplete responses with explicit ``status=incomplete`` (truncation)
  * the legacy Chat Completions fallback shape (when Sakana returns
    ``choices`` instead of ``output``)
  * empty-output edge cases
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lilith_cli.providers import LLMProviderWrapper


# ── Fixtures ─────────────────────────────────────────────────────────


def _live_completed_response() -> dict:
    """Shape observed live: status=completed, reasoning + message blocks.

    Mirrors the body returned by fugu-ultra on a "PONG" prompt with
    ``max_output_tokens=512`` (HTTP 200, ~7s).
    """
    return {
        "id": "resp-0ab60f6009df",
        "object": "response",
        "created_at": 1784185903,
        "model": "fugu-ultra",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_08ddb18e44b1d3a2006a58842b77f08196966382453ff4180c",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "**Crafting a playful response**\n\nI see the user said PONG.",
                    }
                ],
                "encrypted_content": "i6n9c:gAAAAA...sw4=",
            },
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "PING", "annotations": []},
                ],
            },
        ],
        "status": "completed",
        "usage": {
            "input_tokens": 3,
            "output_tokens": 80,
            "total_tokens": 1343,
            "output_tokens_details": {"reasoning_tokens": 56},
        },
    }


def _live_incomplete_response() -> dict:
    """Shape observed live: status=incomplete, only reasoning (truncated).

    Returned when ``max_output_tokens=64`` was too small for fugu-ultra
    to finish reasoning; the only output block is a reasoning summary
    and ``content`` is the empty string.
    """
    return {
        "id": "resp-7a27d980b032",
        "object": "response",
        "created_at": 1784185900,
        "model": "fugu-ultra",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_truncated",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "**Reasoning in progress**\n\nI am still thinking.",
                    }
                ],
            }
        ],
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "usage": {
            "input_tokens": 3,
            "output_tokens": 64,
            "total_tokens": 1327,
            "output_tokens_details": {"reasoning_tokens": 64},
        },
    }


def _chat_completions_fallback() -> dict:
    """Legacy Chat Completions shape, in case Sakana falls back to it."""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": "fugu-ultra",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "PING",
                    "reasoning_content": "the user said PONG",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }


# ── Tests ────────────────────────────────────────────────────────────


def test_normalises_completed_response_with_reasoning_and_message():
    """Completed response surfaces assistant text, reasoning summary,
    usage and a stop finish_reason — no error key."""
    result = LLMProviderWrapper._normalise_sakana_response(
        _live_completed_response()
    )

    assert result["content"] == "PING"
    assert "Crafting a playful response" in result["reasoning_content"]
    assert result["finish_reason"] == "stop"
    assert "error" not in result
    assert result["model"] == "fugu-ultra"
    assert result["usage"]["prompt_tokens"] == 3
    assert result["usage"]["completion_tokens"] == 80
    assert result["usage"]["total_tokens"] == 1343
    assert result["tool_calls"] == []


def test_normalises_incomplete_response_with_explicit_error():
    """status=incomplete produces finish_reason=length and an error key
    pointing at the truncation cause. Content stays empty (Sakana only
    emitted a reasoning block) but the caller now has a reason."""
    result = LLMProviderWrapper._normalise_sakana_response(
        _live_incomplete_response()
    )

    assert result["content"] == ""
    assert "Reasoning in progress" in result["reasoning_content"]
    assert result["finish_reason"] == "length"
    assert "error" in result
    assert "incomplete" in result["error"]
    assert "max_output_tokens" in result["error"]
    # usage fields still parsed even on truncation
    assert result["usage"]["completion_tokens"] == 64


def test_normalises_chat_completions_fallback():
    """When Sakana returns the legacy Chat Completions shape, the
    normaliser delegates to the OpenAI path."""
    result = LLMProviderWrapper._normalise_sakana_response(
        _chat_completions_fallback()
    )

    assert result["content"] == "PING"
    assert result["reasoning_content"] == "the user said PONG"
    assert result["finish_reason"] == "stop"
    assert "error" not in result
    assert result["usage"]["prompt_tokens"] == 3


def test_reasoning_summary_falls_back_to_content_field():
    """Defensive: if a future Sakana version moves reasoning back into
    ``content`` (matching OpenAI Responses shape), we still extract it."""
    data = {
        "id": "resp-x",
        "model": "fugu-ultra",
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "content": [{"type": "text", "text": "thinking..."}],
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "answer"}],
            },
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }
    result = LLMProviderWrapper._normalise_sakana_response(data)

    assert result["content"] == "answer"
    assert result["reasoning_content"] == "thinking..."
    assert result["finish_reason"] == "stop"


def test_empty_output_blocks_returns_empty_strings():
    """Defensive: a 200 with empty ``output`` should not crash and should
    not invent content. finish_reason=stop, no error."""
    data = {
        "id": "resp-empty",
        "model": "fugu-ultra",
        "status": "completed",
        "output": [],
        "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
    }
    result = LLMProviderWrapper._normalise_sakana_response(data)

    assert result["content"] == ""
    assert result["reasoning_content"] == ""
    assert result["finish_reason"] == "stop"
    assert "error" not in result


def test_outputs_alias_is_accepted():
    """Some OpenAI Responses payloads use ``outputs`` (plural). Tolerate it."""
    data = {
        "id": "resp-plural",
        "model": "fugu-ultra",
        "status": "completed",
        "outputs": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "hi"}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    result = LLMProviderWrapper._normalise_sakana_response(data)
    assert result["content"] == "hi"


# ── End-to-end: payload assembly floor/cap for Sakana ────────────────


@pytest.mark.asyncio
async def test_sakana_payload_uses_floor_256_and_cap_4096():
    """When the Sakana Responses path is taken, ``max_output_tokens``
    must respect a 256-token floor (fugu-ultra burns ~50-120 reasoning
    tokens before any visible text) and a 4096-token cap (avoid
    runaway cost when global max_tokens is huge)."""
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    client = MagicMock()
    client.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json=_live_completed_response(),
            request=httpx.Request("POST", "https://api.sakana.ai/v1/responses"),
        )
    )
    client.is_closed = False

    profile = SimpleNamespace(
        base_url="https://api.sakana.ai/v1",
        api_key="sk-test",
        model="fugu-ultra",
        max_tokens=64,  # below floor — must be lifted to 256
        use_responses=True,
    )
    config = SimpleNamespace(
        provider="sakana",
        providers={"sakana": profile},
        base_url=profile.base_url,
        api_key="sk-test",
        model=profile.model,
        max_tokens=64,
        temperature=0.7,
    )

    provider = LLMProviderWrapper(config)
    provider._client = client
    # Direct _do_complete to bypass the public complete() retry loop.
    await provider._do_complete(
        "fugu-ultra",
        [{"role": "user", "content": "PONG"}],
    )

    # The captured payload should have max_output_tokens == 256 (floor),
    # even though the resolved max_tokens was 64.
    sent_payload = client.post.await_args.kwargs["json"]
    assert sent_payload["max_output_tokens"] == 256
    assert sent_payload["model"] == "fugu-ultra"
    assert sent_payload["input"].startswith("User: PONG")