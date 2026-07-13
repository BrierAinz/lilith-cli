"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any


class LLMProvider(ABC):
    """Interface that every LLM provider must implement."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send messages to the model and return a standardized response.

        Returns:
            dict with keys: content (str), model (str),
            usage (dict), finish_reason (str).

        """
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chunks from the model.

        Yields:
            dict with keys: content (str), model (str),
            finish_reason (str | None).

        """
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return a list of model identifiers supported by this provider."""
        ...
