"""Tests for HTTP retry classification and backoff in LLMProviderWrapper.complete().

Validates the t10 resiliency work:
  * 429 is retried with exponential back-off + jitter
  * 5xx is retried (server error)
  * 4xx other than 429 (400, 401, 403, 404, 422) is NOT retried
  * Retry-After header (seconds) is honoured when present
  * Retry-After is capped at retry_backoff_max (no infinite lockout)
  * Config knobs (retry_max, retry_backoff_base, retry_backoff_max,
    retry_jitter) are honoured
  * Network errors (ConnectError, TimeoutException) are retried
  * Programming errors (TypeError, ValueError) are NOT retried

The suite monkey-patches ``asyncio.sleep`` so the tests stay fast even
when exercising the back-off logic with realistic settings.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest


# Ensure the package root is on sys.path (same dance test_retry.py uses).
_PKG_ROOT = str(Path(__file__).resolve().parent.parent)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from lilith_cli.providers import LLMProviderWrapper  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────


def _config(**overrides):
    """Build a SimpleNamespace that looks like YggdrasilConfig to the wrapper.

    Only the keys LLMProviderWrapper.complete() touches need real values;
    everything else is a no-op SimpleNamespace stub.
    """
    base = dict(
        provider="test",
        model="test-model",
        api_key="sk-test",
        base_url="https://mock.example/v1",
        max_tokens=64,
        temperature=0.0,
        providers={},
        retry_max=3,
        retry_backoff_base=0.01,  # tiny so the suite stays fast
        retry_backoff_max=0.5,
        retry_jitter=0.0,  # deterministic by default
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_response(status: int, *, body: dict | None = None, headers: dict | None = None):
    """Return an httpx.Response with the given status."""
    return httpx.Response(
        status,
        json=body if body is not None else {"ok": True},
        headers=headers or {},
        request=httpx.Request("POST", "https://mock.example/v1/chat/completions"),
    )


class _ScriptedClient:
    """Async client whose ``post`` returns each response in sequence.

    Use this for cases where the wrapper will call ``raise_for_status``
    on the response and you want to control that status directly.
    """

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []
        self.is_closed = False

    async def post(self, path: str, *, json: dict):
        self.payloads.append(json)
        if not self.responses:
            raise AssertionError("ScriptedClient ran out of scripted responses")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.is_closed = True


class _SideEffectClient:
    """Async client whose ``post`` runs each side_effect in sequence.

    ``side_effects`` is a list where each item is either:
      * an ``httpx.Response`` — returned as-is
      * a ``BaseException`` instance — raised inside ``post()``
    Use the latter to simulate transport-level failures (ConnectError,
    TimeoutException) or programming errors (TypeError) without
    needing a real network.
    """

    def __init__(self, side_effects: list) -> None:
        self.side_effects = list(side_effects)
        self.payloads: list[dict] = []
        self.is_closed = False

    async def post(self, path: str, *, json: dict):
        self.payloads.append(json)
        if not self.side_effects:
            raise AssertionError("SideEffectClient ran out of effects")
        effect = self.side_effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    async def aclose(self) -> None:
        self.is_closed = True


def _success_body() -> dict:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


# ── 429 retry path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_429_then_429_then_200_is_retried_and_succeeds():
    """Two rate limits followed by a 200 should be retried twice and
    eventually return the 200 body. Sleep should be invoked between
    attempts."""
    client = _ScriptedClient(
        [
            _mock_response(429, headers={"Retry-After": "0"}),
            _mock_response(429, headers={"Retry-After": "0"}),
            _mock_response(200, body=_success_body()),
        ]
    )
    provider = LLMProviderWrapper(_config())
    provider._client = client

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("lilith_cli.providers.asyncio.sleep", side_effect=fake_sleep):
        result = await provider.complete([{"role": "user", "content": "hi"}])

    assert result["content"] == "ok"
    assert len(client.payloads) == 3, "should have made exactly 3 attempts"
    # Two sleeps in between.
    assert len(sleep_calls) == 2


@pytest.mark.asyncio
async def test_429_exhausts_retry_budget_then_raises():
    """When 429s exceed retry_max, complete() should raise RuntimeError
    mentioning the status code, not silently swallow."""
    client = _ScriptedClient(
        [_mock_response(429, headers={"Retry-After": "0"})] * 10
    )
    provider = LLMProviderWrapper(_config(retry_max=2))
    provider._client = client

    with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError) as excinfo:
            await provider.complete([{"role": "user", "content": "hi"}])

    # Initial + 2 retries = 3 total attempts.
    assert len(client.payloads) == 3
    assert "429" in str(excinfo.value)
    assert "2 retries" in str(excinfo.value)


# ── 5xx retry path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_5xx_is_retried_then_recovers():
    """Server errors (500, 502, 503, 504) are transient and should be
    retried just like 429."""
    client = _ScriptedClient(
        [
            _mock_response(503, headers={"Retry-After": "0"}),
            _mock_response(200, body=_success_body()),
        ]
    )
    provider = LLMProviderWrapper(_config())
    provider._client = client

    with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()):
        result = await provider.complete([{"role": "user", "content": "hi"}])

    assert result["content"] == "ok"
    assert len(client.payloads) == 2


# ── 4xx (non-429) NOT retried ───────────────────────────────────────


@pytest.mark.asyncio
async def test_400_is_not_retried():
    """400 Bad Request is deterministic — the server will say the same
    thing on every retry. The wrapper must surface it immediately."""
    client = _ScriptedClient([_mock_response(400, body={"error": "bad"})])
    provider = LLMProviderWrapper(_config(retry_max=5))
    provider._client = client

    with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await provider.complete([{"role": "user", "content": "hi"}])

    assert excinfo.value.response.status_code == 400
    assert len(client.payloads) == 1, "400 must not be retried"
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_401_and_404_are_not_retried():
    """Auth failures (401) and missing resources (404) are non-transient."""
    for status in (401, 404):
        client = _ScriptedClient([_mock_response(status, body={"error": "x"})])
        provider = LLMProviderWrapper(_config(retry_max=4))
        provider._client = client

        with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await provider.complete([{"role": "user", "content": "hi"}])

        assert excinfo.value.response.status_code == status
        assert len(client.payloads) == 1


# ── Retry-After honoured ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_after_header_is_honoured():
    """When the server returns ``Retry-After: 5``, the wrapper must
    sleep for approximately 5 seconds (capped by retry_backoff_max)."""
    client = _ScriptedClient(
        [
            _mock_response(429, headers={"Retry-After": "5"}),
            _mock_response(200, body=_success_body()),
        ]
    )
    # Use a cap lower than 5 to confirm the cap is honoured.
    provider = LLMProviderWrapper(_config(retry_backoff_max=2.0))
    provider._client = client

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("lilith_cli.providers.asyncio.sleep", side_effect=fake_sleep):
        await provider.complete([{"role": "user", "content": "hi"}])

    assert len(sleep_calls) == 1
    # Capped at retry_backoff_max=2.0, not the 5 the server asked for.
    assert sleep_calls[0] == 2.0


@pytest.mark.asyncio
async def test_retry_after_http_date_is_ignored():
    """An HTTP-date form of Retry-After (rare, brittle) must be ignored
    rather than parsed — clock skew makes it unreliable."""
    client = _ScriptedClient(
        [
            _mock_response(
                429,
                headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},
            ),
            _mock_response(200, body=_success_body()),
        ]
    )
    provider = LLMProviderWrapper(
        _config(retry_backoff_base=0.7, retry_backoff_max=5.0, retry_jitter=0.0)
    )
    provider._client = client

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("lilith_cli.providers.asyncio.sleep", side_effect=fake_sleep):
        await provider.complete([{"role": "user", "content": "hi"}])

    # Falls back to base * 2 ** 0 = 0.7s (no jitter).
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.7)


# ── Exponential back-off & jitter ──────────────────────────────────


@pytest.mark.asyncio
async def test_backoff_grows_exponentially_and_caps_at_max():
    """Without jitter, delays should be base * 2 ** (attempt-1),
    clipped at retry_backoff_max."""
    # base=1.0, max=4.0 → delays: 1, 2, 4, 4, 4, ...
    client = _ScriptedClient(
        [_mock_response(503, headers={"Retry-After": "0"})] * 6
    )
    provider = LLMProviderWrapper(
        _config(retry_max=5, retry_backoff_base=1.0, retry_backoff_max=4.0)
    )
    provider._client = client

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("lilith_cli.providers.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(RuntimeError):
            await provider.complete([{"role": "user", "content": "hi"}])

    # 5 retries → 5 sleeps. Delays (without jitter): 1, 2, 4, 4, 4.
    assert len(sleep_calls) == 5
    assert sleep_calls == [1.0, 2.0, 4.0, 4.0, 4.0]


@pytest.mark.asyncio
async def test_jitter_stays_within_fraction_band():
    """With jitter=0.5, each delay must lie in [centre*(1-0.5),
    centre*(1+0.5)]."""
    base = 1.0
    jitter = 0.5
    client = _ScriptedClient(
        [_mock_response(503, headers={"Retry-After": "0"})] * 4
    )
    provider = LLMProviderWrapper(
        _config(
            retry_max=3,
            retry_backoff_base=base,
            retry_backoff_max=10.0,
            retry_jitter=jitter,
        )
    )
    provider._client = client

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("lilith_cli.providers.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(RuntimeError):
            await provider.complete([{"role": "user", "content": "hi"}])

    # Expected un-jittered delays: 1, 2, 4. With ±50% jitter each.
    expected_centres = [1.0, 2.0, 4.0]
    for observed, centre in zip(sleep_calls, expected_centres):
        lo = centre * (1.0 - jitter)
        hi = centre * (1.0 + jitter)
        assert lo <= observed <= hi, (
            f"delay {observed} outside [{lo}, {hi}] for centre {centre}"
        )


# ── Network errors retried, programming errors not ──────────────────


@pytest.mark.asyncio
async def test_connect_error_is_retried():
    """httpx.ConnectError must be retried with backoff."""
    req = httpx.Request("POST", "https://mock.example/v1/chat/completions")
    client = _SideEffectClient(
        [
            httpx.ConnectError("connection refused", request=req),
            httpx.ConnectError("connection refused", request=req),
            _mock_response(200, body=_success_body()),
        ]
    )
    provider = LLMProviderWrapper(_config())
    provider._client = client

    with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()):
        result = await provider.complete([{"role": "user", "content": "hi"}])

    assert result["content"] == "ok"
    assert len(client.payloads) == 3


@pytest.mark.asyncio
async def test_type_error_is_not_retried():
    """Programming errors (TypeError) raised inside _do_complete must
    surface immediately — they reflect a bug in the caller or wrapper,
    not a transient server problem."""
    client = _SideEffectClient([TypeError("boom — programming error")])
    provider = LLMProviderWrapper(_config(retry_max=5))
    provider._client = client

    with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        with pytest.raises(TypeError):
            await provider.complete([{"role": "user", "content": "hi"}])

    assert len(client.payloads) == 1
    sleep_mock.assert_not_called()


# ── Config knobs honoured ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_max_zero_disables_retrying():
    """retry_max=0 should disable retrying: a 429 on the very first
    attempt bubbles up as a RuntimeError immediately."""
    client = _ScriptedClient([_mock_response(429, headers={"Retry-After": "0"})])
    provider = LLMProviderWrapper(_config(retry_max=0))
    provider._client = client

    with patch("lilith_cli.providers.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await provider.complete([{"role": "user", "content": "hi"}])

    assert len(client.payloads) == 1


# ── _is_retryable_status pure helper ───────────────────────────────


def test_is_retryable_status_classification():
    """The pure helper must mirror the policy in the docstring."""
    assert LLMProviderWrapper._is_retryable_status(429) is True
    assert LLMProviderWrapper._is_retryable_status(500) is True
    assert LLMProviderWrapper._is_retryable_status(502) is True
    assert LLMProviderWrapper._is_retryable_status(503) is True
    assert LLMProviderWrapper._is_retryable_status(504) is True
    # Non-retryable 4xx
    for s in (400, 401, 403, 404, 422):
        assert LLMProviderWrapper._is_retryable_status(s) is False, s


# ── _compute_retry_delay pure helper ───────────────────────────────


def test_compute_retry_delay_without_retry_after_or_jitter():
    """Pure exponential: base * 2 ** (attempt - 1), no jitter."""
    delays = [
        LLMProviderWrapper._compute_retry_delay(a, 1.0, 30.0, 0.0, None)
        for a in (1, 2, 3, 4)
    ]
    assert delays == [1.0, 2.0, 4.0, 8.0]


def test_compute_retry_delay_caps_at_backoff_max():
    delays = [
        LLMProviderWrapper._compute_retry_delay(a, 1.0, 5.0, 0.0, None)
        for a in (1, 2, 3, 4, 5)
    ]
    # 1, 2, 4, 5 (capped), 5 (capped).
    assert delays == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_compute_retry_delay_clamps_retry_after_to_max():
    """A malicious or buggy Retry-After must not lock us out for hours."""
    resp = _mock_response(429, headers={"Retry-After": "3600"})
    delay = LLMProviderWrapper._compute_retry_delay(1, 1.0, 30.0, 0.0, resp)
    assert delay == 30.0


def test_compute_retry_delay_ignores_zero_retry_after():
    """Retry-After: 0 carries no real information — the helper must fall
    back to the exponential schedule instead of returning a zero delay."""
    resp = _mock_response(429, headers={"Retry-After": "0"})
    delay = LLMProviderWrapper._compute_retry_delay(1, 1.0, 30.0, 0.0, resp)
    assert delay == 1.0  # base * 2 ** 0, no jitter


def test_compute_retry_delay_with_jitter_in_band():
    """Run many trials; each delay must fall in the jitter band."""
    base = 2.0
    jitter = 0.25
    lo = base * (1.0 - jitter)
    hi = base * (1.0 + jitter)
    for _ in range(50):
        d = LLMProviderWrapper._compute_retry_delay(1, base, 30.0, jitter, None)
        assert lo <= d <= hi