"""Lilith Core - LLM provider modules."""

from .base import LLMProvider
from .local_provider import LocalProvider


try:
    from .litellm_provider import LiteLLMProvider
except ImportError:  # litellm not installed
    LiteLLMProvider = None  # type: ignore[assignment,misc]

__all__ = ["LLMProvider", "LocalProvider"]
if LiteLLMProvider is not None:
    __all__.append("LiteLLMProvider")
