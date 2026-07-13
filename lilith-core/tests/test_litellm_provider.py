"""Tests for LiteLLMProvider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytest.importorskip("litellm", reason="litellm not installed")

from lilith_core.config import Config
from lilith_core.exceptions import LLMError
from lilith_core.providers.litellm_provider import LiteLLMProvider


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_response(content: str = "hello", model: str = "test-model") -> MagicMock:
    """Build a lightweight object that mimics litellm.ModelResponse."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = "stop"

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_litellm_import():
    """Verify that litellm can be imported (it's listed as a dependency)."""
    import litellm

    assert litellm is not None


def test_provider_init(tmp_path):
    """LiteLLMProvider initialises with a Config and resolves defaults."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)
    assert provider.config is config
    assert provider.list_models() == [f"openai/{config.get('lm_studio_url')}"]


@pytest.mark.asyncio
async def test_complete_mock(tmp_path):
    """complete() returns a normalised dict and delegates to litellm.acompletion."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)

    fake_resp = _make_response(content="world", model="gpt-4")

    with patch("lilith_core.providers.litellm_provider.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=fake_resp)
        result = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4",
        )

    assert result["content"] == "world"
    assert result["model"] == "gpt-4"
    assert result["usage"]["total_tokens"] == 15
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_fallback_to_local(tmp_path):
    """When model='auto' the provider routes to the local LM Studio URL."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)

    fake_resp = _make_response(content="local-reply", model="openai/local")

    with patch("lilith_core.providers.litellm_provider.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=fake_resp)
        result = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="auto",
        )

    called_model = mock_litellm.acompletion.call_args.kwargs.get(
        "model",
        mock_litellm.acompletion.call_args[1].get("model"),
    )
    expected = f"openai/{config.get('lm_studio_url')}"
    assert called_model == expected
    assert result["content"] == "local-reply"


@pytest.mark.asyncio
async def test_complete_retries_on_failure(tmp_path):
    """complete() retries up to _MAX_RETRIES times before raising LLMError."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)

    call_count = 0

    async def _flaky_completion(**_kwargs: Any):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient failure")
        return _make_response(content="recovered")

    with (
        patch("lilith_core.providers.litellm_provider.litellm") as mock_litellm,
        patch("lilith_core.providers.litellm_provider.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_litellm.acompletion = _flaky_completion
        result = await provider.complete(
            messages=[{"role": "user", "content": "retry me"}],
        )

    assert call_count == 3
    assert result["content"] == "recovered"


@pytest.mark.asyncio
async def test_complete_exhausts_retries(tmp_path):
    """When all retries fail, LLMError is raised."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)

    with (
        patch("lilith_core.providers.litellm_provider.litellm") as mock_litellm,
        patch("lilith_core.providers.litellm_provider.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(LLMError, match="LiteLLM failed after"),
    ):
        mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("boom"))
        await provider.complete(
            messages=[{"role": "user", "content": "fail"}],
        )


@pytest.mark.asyncio
async def test_stream_mock(tmp_path):
    """stream() yields structured chunks from litellm.acompletion(stream=True)."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)

    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta = MagicMock()
    chunk1.choices[0].delta.content = "hel"
    chunk1.choices[0].finish_reason = None
    chunk1.model = "gpt-4"

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta = MagicMock()
    chunk2.choices[0].delta.content = "lo"
    chunk2.choices[0].finish_reason = "stop"
    chunk2.model = "gpt-4"

    async def _fake_stream(**_kwargs: Any):
        for c in [chunk1, chunk2]:
            yield c

    with patch("lilith_core.providers.litellm_provider.litellm") as mock_litellm:
        # litellm.acompletion(stream=True) returns an async generator when awaited
        mock_litellm.acompletion = AsyncMock(return_value=_fake_stream())

        chunks = [
            chunk
            async for chunk in provider.stream(
                messages=[{"role": "user", "content": "stream me"}],
                model="gpt-4",
            )
        ]

    assert len(chunks) == 2
    assert chunks[0]["content"] == "hel"
    assert chunks[1]["content"] == "lo"
    assert chunks[1]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_stream_error(tmp_path):
    """stream() raises LLMError when litellm.acompletion fails."""
    config = Config(root_path=tmp_path)
    provider = LiteLLMProvider(config=config)

    async def _failing_stream(**_kwargs: Any):
        raise RuntimeError("connection lost")
        yield  # type: ignore[unreachable]

    with patch("lilith_core.providers.litellm_provider.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=_failing_stream())

        with pytest.raises(LLMError, match="LiteLLM streaming failed"):
            async for _ in provider.stream(
                messages=[{"role": "user", "content": "fail"}],
                model="gpt-4",
            ):
                pass
