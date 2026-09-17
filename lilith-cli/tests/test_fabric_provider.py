from __future__ import annotations

import json

import httpx
import pytest
from lilith_cli.config import YggdrasilConfig
from lilith_cli.providers import LLMProviderWrapper


def _config(**overrides) -> YggdrasilConfig:
    return YggdrasilConfig(provider="fabric", model="router", **overrides)


@pytest.mark.asyncio
async def test_fabric_provider_round_trips_structured_tool_call(monkeypatch):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("x-yggdrasil-token")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "response": {
                "text": "",
                "finish_reason": "tool_calls",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "file_read", "arguments": "{\"path\":\"README.md\"}"},
                }],
            },
            "provider": "experiential", "account_id": "luna",
            "model": "gpt-5.6-luna", "runtime": "direct-api",
            "usage": {"tokens_used": 21, "attempts": 1, "cost_usd": 0.0123},
            "fallback_used": False,
        })

    transport = httpx.MockTransport(handler)

    class ClientFactory(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "test-token")
    monkeypatch.setattr("lilith_cli.providers.httpx.AsyncClient", ClientFactory)
    provider = LLMProviderWrapper(_config())
    result = await provider.complete(
        [{"role": "user", "content": "lee README"}],
        tools=[{"type": "function", "function": {"name": "file_read"}}],
    )
    assert seen["token"] == "test-token"
    assert seen["body"]["client"] == "lilith"
    assert seen["body"]["payload"]["messages"][0]["content"] == "lee README"
    assert result["tool_calls"][0].name == "file_read"
    assert result["tool_calls"][0].arguments == {"path": "README.md"}
    assert result["model"] == "gpt-5.6-luna"
    assert result["usage"]["total_tokens"] == 21
    assert result["usage"]["cost_usd"] == 0.0123
    assert provider._health.get("fabric")["successes"] == 1
    account = provider._health.get("fabric-account:experiential:luna")
    assert account["successes"] == 1
    assert account["state"] == "closed"


@pytest.mark.asyncio
async def test_fabric_stream_is_honest_atomic_fallback(monkeypatch):
    provider = LLMProviderWrapper(_config())

    async def complete(*args, **kwargs):
        return {
            "content": "respuesta", "reasoning_content": None, "tool_calls": [],
            "usage": {"total_tokens": 4}, "finish_reason": "stop", "model": "routed",
        }

    monkeypatch.setattr(provider, "_fabric_complete", complete)
    chunks = [chunk async for chunk in provider.stream([{"role": "user", "content": "hola"}])]
    assert chunks == [{
        "content": "respuesta", "reasoning": None, "tool_calls": [],
        "usage": {"total_tokens": 4}, "finish_reason": "stop", "model": "routed",
    }]


@pytest.mark.asyncio
async def test_fabric_requires_token_reference(monkeypatch):
    monkeypatch.delenv("YGGDRASIL_FABRIC_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="YGGDRASIL_FABRIC_TOKEN"):
        await LLMProviderWrapper(_config()).complete([{"role": "user", "content": "hola"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "call_model", "expected"),
    [
        (YggdrasilConfig(provider="fabric"), None, None),
        (_config(), None, None),
        (YggdrasilConfig(provider="fabric", model="glm-5.2"), None, "glm-5.2"),
        (_config(), "glm-5.2", "glm-5.2"),
    ],
)
async def test_fabric_sends_only_concrete_preferred_model(
    monkeypatch, config, call_model, expected
):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"response": {"text": "ok"}})

    transport = httpx.MockTransport(handler)

    class ClientFactory(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "test-token")
    monkeypatch.setattr("lilith_cli.providers.httpx.AsyncClient", ClientFactory)
    await LLMProviderWrapper(config).complete(
        [{"role": "user", "content": "hola"}], model=call_model
    )

    if expected is None:
        assert "preferred_model" not in seen
    else:
        assert seen["preferred_model"] == expected


@pytest.mark.asyncio
async def test_fabric_circuit_opens_after_repeated_transport_failures(monkeypatch):
    from lilith_cli.provider_health import ProviderCircuitOpenError

    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, json={"error": "NoEligibleAccount", "detail": "quota"})

    transport = httpx.MockTransport(handler)

    class ClientFactory(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "test-token")
    monkeypatch.setattr("lilith_cli.providers.httpx.AsyncClient", ClientFactory)
    provider = LLMProviderWrapper(_config())
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.complete([{"role": "user", "content": "hola"}])
    assert provider._health.get("fabric")["state"] == "open"
    with pytest.raises(ProviderCircuitOpenError):
        await provider.complete([{"role": "user", "content": "hola"}])
    assert calls["count"] == 2
