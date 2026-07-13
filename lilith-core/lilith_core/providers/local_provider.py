"""Local LM Studio / OpenAI-compatible provider using httpx."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from ..config import Config
from ..exceptions import LLMError
from .base import LLMProvider


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class LocalProvider(LLMProvider):
    """Provider that talks directly to a local OpenAI-compatible server.

    This is useful when litellm is not desired and the caller wants raw
    HTTP communication with LM Studio or any ``/v1/chat/completions``
    endpoint.
    """

    def __init__(self, config: Config | None = None) -> None:
        """Initialise the LocalProvider.

        Args:
            config: Optional Config instance. Defaults to a new Config().

        """
        self.config = config or Config()
        self.base_url: str = self.config.get("lm_studio_url", "http://localhost:1234/v1")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def list_models(self) -> list[str]:
        """Return the local server URL as the sole model identifier."""
        return [self.base_url]

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Post to ``/chat/completions`` and return a standardised dict."""
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or "local-model",
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.get("temperature", 0.7)),
            "max_tokens": kwargs.get("max_tokens", self.config.get("max_context", 8192)),
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            choice = data["choices"][0]
            usage = data.get("usage", {})
            return {
                "content": choice["message"].get("content", ""),
                "model": data.get("model", "local-model"),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "finish_reason": choice.get("finish_reason", "stop"),
            }
        except httpx.HTTPError as exc:
            raise LLMError(f"Local provider request failed: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected response shape from local server: {exc}") from exc

    async def stream(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream SSE chunks from ``/chat/completions``."""
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or "local-model",
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.get("temperature", 0.7)),
            "stream": True,
        }
        try:
            async with (
                httpx.AsyncClient(timeout=120.0) as client,
                client.stream("POST", url, json=payload) as resp,
            ):
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: ") :]
                    if data_str.strip() == "[DONE]":
                        return
                    chunk_data = json.loads(data_str)
                    delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                    finish = chunk_data.get("choices", [{}])[0].get("finish_reason")
                    yield {
                        "content": delta.get("content", ""),
                        "model": chunk_data.get("model", "local-model"),
                        "finish_reason": finish,
                    }
        except httpx.HTTPError as exc:
            raise LLMError(f"Local provider streaming failed: {exc}") from exc
