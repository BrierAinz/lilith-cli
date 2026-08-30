"""Stable runtime boundary for Lilith session consumers.

``AgentSession`` remains the concrete, backwards-compatible implementation.
CLI commands, the REPL, the IDE and external bridges should depend on the
structural contract in this module so they can be tested or embedded without
importing the full agent implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import YggdrasilConfig
    from .providers import LLMProviderWrapper
    from .session_telemetry import TelemetryRuntime


class SessionRuntime(Protocol):
    """Public capabilities shared by interactive and headless sessions.

    The runtime intentionally exposes the stable conversation surface and an
    ``Any`` fallback for legacy slash commands.  That fallback lets the command
    catalog migrate away from private ``AgentSession`` fields incrementally
    instead of forcing a breaking rewrite.
    """

    config: YggdrasilConfig
    provider: LLMProviderWrapper
    history: list[dict[str, Any]]
    system_prompt: str
    agent_mode: str

    def __getattr__(self, name: str) -> Any:
        """Allow legacy commands to use implementation-specific state."""
        ...

    def cancel(self) -> None:
        """Cancel the active stream or tool loop."""
        ...

    def attach_hooks(self, registry: Any, *, session_id: str = "") -> None:
        """Attach the optional hook registry."""
        ...

    @property
    def memory(self) -> Any:
        """Return the configured memory backend, when enabled."""
        ...

    def get_tool_descriptions(self) -> list[dict[str, Any]]:
        """Return provider-compatible tool descriptions."""
        ...

    async def execute_tool(self, tool_call: Any) -> Any:
        """Execute one structured tool call."""
        ...

    async def process_message(
        self,
        text: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Process one message and return the final assistant response."""
        ...

    def process_message_stream(
        self,
        text: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Process one message as a stream of structured events."""
        ...

    def session_duration(self) -> float:
        """Return the elapsed session duration in seconds."""
        ...

    @property
    def total_usage(self) -> dict[str, int]:
        """Return aggregate token usage."""
        ...

    @property
    def per_model_usage(self) -> dict[str, dict[str, Any]]:
        """Return token and cost usage grouped by model."""
        ...

    @property
    def telemetry(self) -> TelemetryRuntime:
        """Return the session's usage and activity telemetry."""
        ...


def create_session(
    config: YggdrasilConfig,
    provider: LLMProviderWrapper | None = None,
) -> SessionRuntime:
    """Build the default session without coupling callers to its class.

    The import remains local on purpose.  Besides avoiding a heavy import for
    command discovery, it preserves the established monkeypatch seam at
    ``lilith_cli.agent.AgentSession`` for existing integrations and tests.
    """

    from .agent import AgentSession

    if provider is None:
        return AgentSession(config)
    return AgentSession(config, provider=provider)


__all__ = ["SessionRuntime", "create_session"]
