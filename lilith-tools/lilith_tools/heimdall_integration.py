"""Heimdall integration with the SmartToolRouter.

Connects the Heimdall auditor agent (Vanaheim) to the lilith-tools
SmartToolRouter so that every tool call is audited before execution.

This module provides:
    - heimdall_pre_tool_hook: A pre_tool_call hook that audits tool params
      for secrets, dangerous commands, and prompt injection before execution.
    - heimdall_post_tool_hook: A post_tool_call hook that audits tool results
      for leaked secrets before they reach the user.
    - register_heimdall_hooks: Convenience function to register both hooks
      on the global hook registry.

Usage::

    from lilith_tools.heimdall_integration import register_heimdall_hooks

    # One-time setup — registers Heimdall on the global hook registry
    register_heimdall_hooks()

    # Now every SmartToolRouter.execute_tool() call is audited
    # Dangerous commands are gated, secrets in results are sanitized.
"""

from __future__ import annotations

import re
import logging
from typing import Any

from lilith_core.hooks import HookContext, HookType, get_hook_registry


logger = logging.getLogger("lilith_tools.heimdall")


# ── Security patterns (mirrored from Heimdall agent) ─────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
    re.compile(r"gho_[a-zA-Z0-9]{36}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"xoxb-[a-zA-Z0-9-]+"),
    re.compile(r"AIza[a-zA-Z0-9_-]{35}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-ant-[a-zA-Z0-9_-]+"),
]

_DANGEROUS_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/"),
    re.compile(r"mkfs\.\w+\s+/dev/"),
    re.compile(r":\(\)\{.*\};:"),
    re.compile(r"dd\s+if=.*of=/dev/[sh]d"),
    re.compile(r"chmod\s+-R\s+777\s+/"),
    re.compile(r"curl\s+.*\|\s*(ba)?sh"),
]


# ── Hook callbacks ───────────────────────────────────────────────────────────


def heimdall_pre_tool_hook(ctx: HookContext) -> HookContext | None:
    """Pre-tool-call hook: audit tool params before execution.

    Checks all string values in params for:
        - Secrets (API keys, tokens, private keys) → GATE
        - Dangerous commands (rm -rf, format, fork bombs) → GATE

    If a danger is found, returns None to gate the tool execution.
    Otherwise returns the context unchanged (approve).

    Args:
        ctx: HookContext with data["tool_name"] and data["params"].

    Returns:
        HookContext (approve) or None (gate).
    """
    params = ctx.data.get("params", {})
    tool_name = ctx.data.get("tool_name", "unknown")

    # Check all string values in params
    for key, value in params.items():
        if not isinstance(value, str):
            continue

        # Check for secrets
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                logger.warning(
                    "Heimdall GATED tool '%s': secret detected in param '%s'",
                    tool_name, key,
                )
                return None  # Gate it

        # Check for dangerous commands
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(value):
                logger.warning(
                    "Heimdall GATED tool '%s': dangerous command in param '%s'",
                    tool_name, key,
                )
                return None  # Gate it

    # All clear
    return ctx


def heimdall_post_tool_hook(ctx: HookContext) -> HookContext:
    """Post-tool-call hook: audit tool results for leaked secrets.

    Checks the ToolResult data and error for leaked secrets.
    If found, sanitizes the result by masking the secret with [REDACTED].

    Args:
        ctx: HookContext with data["result"] (ToolResult).

    Returns:
        HookContext with potentially sanitized result.
    """
    result = ctx.data.get("result")
    if result is None:
        return ctx

    # Check error field for secrets
    error = getattr(result, "error", "")
    if error and isinstance(error, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(error):
                result.error = pattern.sub("[REDACTED]", error)
                logger.warning("Heimdall: sanitized secret in tool error")

    # Check data field for secrets (if it's a dict with string values)
    data = getattr(result, "data", None)
    if data and isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str):
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(value):
                        data[key] = pattern.sub("[REDACTED]", value)
                        logger.warning(
                            "Heimdall: sanitized secret in tool result '%s'", key
                        )

    return ctx


# ── Registration ─────────────────────────────────────────────────────────────


def register_heimdall_hooks() -> None:
    """Register Heimdall pre/post tool hooks on the global hook registry.

    Call this once at application startup to enable Heimdall auditing
    on all SmartToolRouter.execute_tool() calls.

    The hooks are registered with:
        - pre_tool_call: priority 0 (runs first, before other hooks)
        - post_tool_call: priority 10 (runs after other post hooks)
    """
    registry = get_hook_registry()

    # Unregister existing Heimdall hooks (idempotent)
    registry.unregister("heimdall_pre_tool")
    registry.unregister("heimdall_post_tool")

    # Register with priority — pre runs first (0), post runs last (10)
    registry.register(
        HookType.PRE_TOOL_CALL,
        heimdall_pre_tool_hook,
        name="heimdall_pre_tool",
        priority=0,
    )
    registry.register(
        HookType.POST_TOOL_CALL,
        heimdall_post_tool_hook,
        name="heimdall_post_tool",
        priority=10,
    )

    logger.info("Heimdall hooks registered: pre_tool_call (gate) + post_tool_call (sanitize)")


def unregister_heimdall_hooks() -> None:
    """Remove Heimdall hooks from the global registry."""
    registry = get_hook_registry()
    registry.unregister("heimdall_pre_tool")
    registry.unregister("heimdall_post_tool")
    logger.info("Heimdall hooks unregistered")