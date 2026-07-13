"""LiteLLM-based provider with retry and fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import litellm

from ..config import Config
from ..exceptions import LLMError
from .base import LLMProvider


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class LiteLLMProvider(LLMProvider):
    """Provider that delegates to ``litellm.acompletion``.

    When *model* is ``"auto"`` or ``None`` the provider falls back to the
    local LM Studio URL configured in :class:`Config`.
    """

    def __init__(self, config: Config | None = None) -> None:
        """Initialise the LiteLLMProvider.

        Args:
            config: Optional Config instance. Defaults to a new Config().

        """
        self.config = config or Config()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def _resolve_model(self, model: str | None) -> str:
        """Return the effective model identifier.

        If *model* is ``"auto"`` or ``None``, the local LM Studio endpoint
        (``openai/<base_url>``) is used so litellm routes to it correctly.
        """
        if model and model != "auto":
            return model
        base = self.config.get("lm_studio_url", "http://localhost:1234/v1")
        return f"openai/{base}"

    def list_models(self) -> list[str]:
        """Return model identifiers recognised by this provider.

        At minimum the local LM Studio fallback is listed; additional
        models depend on the litellm / environment configuration.
        """
        base = self.config.get("lm_studio_url", "http://localhost:1234/v1")
        return [f"openai/{base}"]

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Call ``litellm.acompletion`` with exponential-backoff retries."""
        resolved = self._resolve_model(model)
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await litellm.acompletion(
                    model=resolved,
                    messages=messages,
                    temperature=kwargs.get("temperature", self.config.get("temperature", 0.7)),
                    max_tokens=kwargs.get("max_tokens", self.config.get("max_context", 8192)),
                    **{k: v for k, v in kwargs.items() if k not in {"temperature", "max_tokens"}},
                )
                return self._normalise(response)
            except Exception as exc:
                last_exc = exc
                logger.warning("LiteLLM attempt %d/%d failed: %s", attempt, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        raise LLMError(f"LiteLLM failed after {_MAX_RETRIES} retries: {last_exc}")

    async def stream(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chunks via ``litellm.acompletion(stream=True)``."""
        resolved = self._resolve_model(model)
        try:
            response = await litellm.acompletion(
                model=resolved,
                messages=messages,
                stream=True,
                temperature=kwargs.get("temperature", self.config.get("temperature", 0.7)),
                **{k: v for k, v in kwargs.items() if k not in {"temperature", "max_tokens"}},
            )
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                content = delta.content if delta and delta.content else ""
                finish = chunk.choices[0].finish_reason if chunk.choices else None
                yield {
                    "content": content,
                    "model": chunk.model,
                    "finish_reason": finish,
                }
        except Exception as exc:
            raise LLMError(f"LiteLLM streaming failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(response: Any) -> dict[str, Any]:
        """Convert a litellm ModelResponse into a standardised dict."""
        choice = response.choices[0]
        return {
            "content": choice.message.content or "",
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            "finish_reason": choice.finish_reason or "stop",
        }
