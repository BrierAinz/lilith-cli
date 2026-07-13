"""Sandbox-Hook Integration for Lilith.

Bridges AgentSandbox with the HookRegistry so that sandbox policies are
automatically enforced at the system level via hooks. This makes sandbox
restrictions apply to ALL tool calls and LLM calls without requiring explicit
context manager usage.

Inspired by jamjet-labs/jamjet's safety layer pattern: block unsafe tool calls,
enforce budgets, audit, and replay violations.

Integration with PolicyEngine: sandbox violations are also logged as policy
violations for unified governance reporting.

Usage::

    from lilith_core.sandbox_hooks import activate_sandbox_hooks, deactivate_sandbox_hooks

    # One-time setup — registers sandbox checks on the global hook registry
    activate_sandbox_hooks()

    # Now every tool call is checked against the agent's sandbox policy
    # and every LLM call is checked for token budget + rate limits.

    # To disable:
    deactivate_sandbox_hooks()
"""

from __future__ import annotations

import logging
from typing import Any

from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_core.sandbox import (
    AgentSandbox,
    SandboxAction,
    SandboxError,
    SandboxPolicy,
    SandboxRuleType,
    get_sandbox_registry,
)

logger = logging.getLogger("lilith.sandbox_hooks")

# Hook names for registration/unregistration
_SANDBOX_TOOL_HOOK = "sandbox_tool_gate"
_SANDBOX_LLM_HOOK = "sandbox_llm_gate"


def _get_agent_sandbox(agent_name: str) -> AgentSandbox | None:
    """Look up the sandbox policy for an agent and return an active AgentSandbox.

    Returns None if no policy is registered for this agent and no default exists.
    """
    registry = get_sandbox_registry()
    policy = registry.get(agent_name)
    if policy is None:
        return None
    if not policy.enabled:
        return None
    # Return a sandbox instance. We manage activation manually per-call
    # because the sandbox context manager is designed for longer-lived scopes.
    return AgentSandbox(policy)


def _sandbox_tool_hook(ctx: HookContext) -> HookContext | None:
    """Pre-tool-call hook: check if the tool is allowed by the agent's sandbox.

    Looks up the agent's sandbox policy and calls check_tool(). If the tool
    is blocked, returns None (aborting the hook chain and thus the tool call).
    If allowed, returns the context unchanged.
    """
    agent_name = ctx.agent_name
    tool_name = ctx.data.get("tool_name", "")
    if not tool_name:
        return ctx

    sandbox = _get_agent_sandbox(agent_name)
    if sandbox is None:
        return ctx  # No sandbox policy for this agent

    # We need to temporarily activate the sandbox to check the tool
    sandbox.state.reset()
    sandbox._active = True
    try:
        allowed = sandbox.check_tool(tool_name)
        if not allowed:
            # Violation was already logged inside check_tool
            logger.warning(
                "Sandbox blocked tool '%s' for agent '%s' (policy=%s)",
                tool_name,
                agent_name,
                sandbox.policy.name,
            )
            return None  # Abort the tool call
    except SandboxError as e:
        logger.warning(
            "Sandbox error for agent '%s' tool '%s': %s",
            agent_name,
            tool_name,
            e,
        )
        return None
    finally:
        sandbox._active = False

    return ctx


def _sandbox_llm_hook(ctx: HookContext) -> HookContext | None:
    """Pre-LLM-call hook: enforce token budget and rate limits.

    Looks up the agent's sandbox policy and checks:
        - MAX_TOKENS: estimated tokens in the message must not exceed budget
        - MAX_CALLS_PER_MIN: call rate must not exceed limit
        - MAX_EXEC_TIME: not checked here (per-call, not per-session)

    If any limit is exceeded, returns None (aborting the LLM call).
    """
    agent_name = ctx.agent_name
    session_id = ctx.session_id

    sandbox = _get_agent_sandbox(agent_name)
    if sandbox is None:
        return ctx

    # Temporarily activate sandbox state
    sandbox.state.reset()
    sandbox._active = True
    try:
        # Estimate tokens from the message
        message = ctx.data.get("message", "")
        if isinstance(message, str):
            estimated_tokens = max(1, len(message) // 4)
        else:
            estimated_tokens = 0

        # Check token budget
        token_rule = sandbox.policy.get_rule(SandboxRuleType.MAX_TOKENS)
        if token_rule and token_rule.value:
            budget = int(token_rule.value)
            # We need to track accumulated tokens across the session.
            # Use ctx.metadata as a cross-hook state store.
            used_key = f"sandbox_tokens_used:{session_id}"
            used = ctx.metadata.get(used_key, 0)
            if used + estimated_tokens > budget:
                logger.warning(
                    "Sandbox token budget exceeded for agent '%s': "
                    "used=%d + estimated=%d > budget=%d (policy=%s)",
                    agent_name,
                    used,
                    estimated_tokens,
                    budget,
                    sandbox.policy.name,
                )
                return None
            # Update accumulated usage in metadata
            ctx.metadata[used_key] = used + estimated_tokens

        # Check rate limit
        rate_rule = sandbox.policy.get_rule(SandboxRuleType.MAX_CALLS_PER_MIN)
        if rate_rule and rate_rule.value:
            limit = int(rate_rule.value)
            # Track calls per minute in metadata
            calls_key = f"sandbox_calls:{agent_name}:{session_id}"
            call_times = ctx.metadata.get(calls_key, [])
            now = __import__("time").time()
            # Filter to last 60 seconds
            recent = [t for t in call_times if now - t < 60.0]
            if len(recent) >= limit:
                logger.warning(
                    "Sandbox rate limit exceeded for agent '%s': "
                    "%d calls/min >= limit=%d (policy=%s)",
                    agent_name,
                    len(recent),
                    limit,
                    sandbox.policy.name,
                )
                return None
            recent.append(now)
            ctx.metadata[calls_key] = recent

    except SandboxError as e:
        logger.warning("Sandbox error for agent '%s' LLM call: %s", agent_name, e)
        return None
    finally:
        sandbox._active = False

    return ctx


def activate_sandbox_hooks() -> None:
    """Register sandbox checks as pre_tool_call and pre_llm_call hooks.

    This wires the SandboxRegistry into the global HookRegistry so that
    every tool call and LLM call is checked against the agent's sandbox
    policy automatically.

    Idempotent: calling multiple times is safe (old hooks are unregistered first).
    """
    registry = get_hook_registry()

    # Remove any existing sandbox hooks first (idempotent)
    registry.unregister(_SANDBOX_TOOL_HOOK)
    registry.unregister(_SANDBOX_LLM_HOOK)

    # Register tool gate hook (high priority, runs before other hooks)
    registry.register(
        HookType.PRE_TOOL_CALL,
        _sandbox_tool_hook,
        name=_SANDBOX_TOOL_HOOK,
        priority=-20,  # Run before policy_engine (-10) and other hooks
    )

    # Register LLM gate hook (high priority)
    registry.register(
        HookType.PRE_LLM_CALL,
        _sandbox_llm_hook,
        name=_SANDBOX_LLM_HOOK,
        priority=-20,
    )

    logger.info(
        "Sandbox hooks activated: tool_gate + llm_gate (priority=-20)"
    )


def deactivate_sandbox_hooks() -> None:
    """Unregister sandbox hooks from the global HookRegistry."""
    registry = get_hook_registry()
    registry.unregister(_SANDBOX_TOOL_HOOK)
    registry.unregister(_SANDBOX_LLM_HOOK)
    logger.info("Sandbox hooks deactivated")


def sandbox_hooks_active() -> bool:
    """Check if sandbox hooks are currently registered."""
    registry = get_hook_registry()
    tool_hooks = [h for h in registry.hooks_for(HookType.PRE_TOOL_CALL) if h.name == _SANDBOX_TOOL_HOOK]
    llm_hooks = [h for h in registry.hooks_for(HookType.PRE_LLM_CALL) if h.name == _SANDBOX_LLM_HOOK]
    return len(tool_hooks) > 0 and len(llm_hooks) > 0


def get_sandbox_hook_stats() -> dict[str, Any]:
    """Return statistics about sandbox hook registration."""
    registry = get_hook_registry()
    tool_hooks = [h for h in registry.hooks_for(HookType.PRE_TOOL_CALL) if h.name == _SANDBOX_TOOL_HOOK]
    llm_hooks = [h for h in registry.hooks_for(HookType.PRE_LLM_CALL) if h.name == _SANDBOX_LLM_HOOK]
    sandbox_reg = get_sandbox_registry()
    return {
        "tool_hook_registered": len(tool_hooks) > 0,
        "llm_hook_registered": len(llm_hooks) > 0,
        "registered_agents": sandbox_reg.list_agents(),
        "has_default_policy": sandbox_reg._default is not None,
    }
