"""Failure recovery and fallback chains for resilient tool execution."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from lilith_tools.base import BaseTool, ToolResult


if TYPE_CHECKING:
    from lilith_tools.registry import ToolRegistry


class RetryPolicy(BaseModel):
    """Configuration for retry behaviour when a tool execution fails."""

    max_retries: int = 3
    backoff_factor: float = 1.5
    retry_on_errors: list[str] = []


class FallbackChain(BaseModel):
    """Ordered fallback strategy: try *primary*, then each *fallback* in order."""

    name: str
    primary: str
    fallbacks: list[str] = []
    condition: str = "on_failure"


# ------------------------------------------------------------------
# Default retry policies
# ------------------------------------------------------------------

DEFAULT_NETWORK_POLICY = RetryPolicy(
    max_retries=3,
    backoff_factor=1.5,
    retry_on_errors=["ConnectionError", "TimeoutError", "URLError", "network"],
)

DEFAULT_RATE_LIMIT_POLICY = RetryPolicy(
    max_retries=5,
    backoff_factor=2.0,
    retry_on_errors=["RateLimitError", "429", "rate_limit", "too many requests"],
)

DEFAULT_TIMEOUT_POLICY = RetryPolicy(
    max_retries=2,
    backoff_factor=1.0,
    retry_on_errors=["TimeoutError", "TimeoutExpired", "timeout", "timed out"],
)


class RecoveryManager:
    """Execute tools with retry logic and fallback chains."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute_with_retry(
        self,
        tool_name: str,
        params: dict[str, Any],
        policy: RetryPolicy | None = None,
    ) -> ToolResult:
        """Execute *tool_name* with *params*, retrying on matching errors."""
        policy = policy or RetryPolicy()
        tool_cls = self.registry.get(tool_name)
        if tool_cls is None:
            return ToolResult(success=False, data=None, error=f"Tool not found: {tool_name}")

        tool: BaseTool = tool_cls()
        last_error = ""

        for attempt in range(policy.max_retries + 1):
            result = tool.execute(**params)

            if result.success:
                return result

            last_error = result.error

            if not self._should_retry(last_error, policy):
                return result

            if attempt < policy.max_retries:
                wait = self._backoff(attempt, policy.backoff_factor)
                time.sleep(wait)

        return ToolResult(success=False, data=None, error=last_error)

    def execute_with_fallback(
        self,
        chain: FallbackChain,
        params: dict[str, Any],
    ) -> ToolResult:
        """Try the primary tool, then each fallback in order, returning first success."""
        candidates = [chain.primary, *chain.fallbacks]

        errors: list[str] = []
        for name in candidates:
            tool_cls = self.registry.get(name)
            if tool_cls is None:
                errors.append(f"Tool not found: {name}")
                continue

            tool: BaseTool = tool_cls()
            result = tool.execute(**params)

            if result.success:
                return result

            errors.append(f"{name}: {result.error}")

        all_errors = "; ".join(errors)
        return ToolResult(
            success=False,
            data=None,
            error=f"All fallbacks failed: {all_errors}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _should_retry(error: str, policy: RetryPolicy) -> bool:
        """Check if *error* matches any pattern in *policy.retry_on_errors*.

        If no patterns are specified, retry on any error.
        """
        if not policy.retry_on_errors:
            return True
        error_lower = error.lower()
        return any(pattern.lower() in error_lower for pattern in policy.retry_on_errors)

    @staticmethod
    def _backoff(attempt: int, factor: float) -> float:
        """Exponential backoff: *factor* ** *attempt* seconds."""
        return factor**attempt
