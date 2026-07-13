"""Plugin hooks system for Lilith agents.

Inspired by Aether-Agents' per-turn observability hooks.
Provides pre/post hooks for LLM calls, tool calls, and session lifecycle.

Hook types:
    - pre_llm_call:  Called before an LLM request. Can modify the prompt or abort.
    - post_llm_call: Called after an LLM response. Can modify or reject the response.
    - pre_tool_call: Called before a tool executes. Can gate, approve, or rewrite args.
    - post_tool_call: Called after a tool returns. Can modify the result.
    - on_session_start: Called when an agent session begins.
    - on_session_end: Called when an agent session ends.

Hooks are registered callbacks that fire at specific lifecycle points.
Each hook receives a context dict and can return a modified version or None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


class HookType(Enum):
    """Lifecycle hook types."""

    PRE_LLM_CALL = "pre_llm_call"
    POST_LLM_CALL = "post_llm_call"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    ON_SESSION_START = "on_session_start"
    ON_SESSION_END = "on_session_end"
    ON_ERROR = "on_error"
    ON_TOOL_RESULT = "on_tool_result"
    # Pipeline phase hooks (lilith-orchestrator)
    PRE_PIPELINE_PHASE = "pre_pipeline_phase"
    POST_PIPELINE_PHASE = "post_pipeline_phase"
    PRE_PIPELINE_START = "pre_pipeline_start"
    POST_PIPELINE_END = "post_pipeline_end"
    # Sub-agent spawn hooks (lilith-orchestrator.subagents)
    #   PRE_SUBAGENT_SPAWN   — fires before SubAgentRunner dispatches a
    #                          child. Payload data:
    #                            - agent_type: str
    #                            - user_input: str
    #                            - depth: int
    #                            - spawn_id: str
    #                            - allowed_tools: list[str]
    #                            - model_preference: str | None
    #                          Returning a *new* HookContext with
    #                          ``data["user_input"]`` or
    #                          ``data["allowed_tools"]`` REWRITES them
    #                          before the executor runs. Returning None
    #                          ABORTS the spawn (caller gets a
    #                          SubAgentResult with success=False,
    #                          error="aborted by hook").
    #   POST_SUBAGENT_RESULT — fires after a SubAgentResult is produced.
    #                          Payload data:
    #                            - agent_type: str
    #                            - spawn_id: str
    #                            - output: str
    #                            - success: bool
    #                            - error: str | None
    #                            - tools_used: list[str]
    #                            - duration_ms: float
    #                          Returning a HookContext with
    #                          ``data["output"]`` REWRITES the result's
    #                          output (audit / redaction use case).
    #                          Returning None has no effect on the result
    #                          itself (post-hook is observational).
    PRE_SUBAGENT_SPAWN = "pre_subagent_spawn"
    POST_SUBAGENT_RESULT = "post_subagent_result"


@dataclass
class HookContext:
    """Context passed to every hook callback.

    Attributes:
        hook_type: Which lifecycle event triggered this hook.
        agent_name: Name of the agent that fired the hook.
        session_id: Unique session identifier.
        data: Payload dict — structure depends on hook_type.
        metadata: Free-form dict for hooks to stash cross-hook state.
    """

    hook_type: HookType
    agent_name: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class HookCallback(Protocol):
    """Protocol for hook callbacks."""

    def __call__(self, ctx: HookContext) -> HookContext | None: ...


@dataclass
class HookRegistration:
    """A registered hook with optional priority and name."""

    callback: HookCallback
    hook_type: HookType
    name: str = ""
    priority: int = 0  # Lower = runs first


class HookRegistry:
    """Registry for plugin hooks.

    Hooks are fired in priority order (lower priority value = runs first).
    A hook can abort the chain by returning None.
    A hook can modify the context by returning a new HookContext.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookType, list[HookRegistration]] = {
            ht: [] for ht in HookType
        }

    def register(
        self,
        hook_type: HookType,
        callback: HookCallback,
        name: str = "",
        priority: int = 0,
    ) -> None:
        """Register a hook callback.

        Args:
            hook_type: When to fire this hook.
            callback: Callable that receives HookContext, returns HookContext | None.
            name: Optional human-readable name for debugging.
            priority: Lower runs first (default 0).
        """
        reg = HookRegistration(
            callback=callback, hook_type=hook_type, name=name, priority=priority
        )
        self._hooks[hook_type].append(reg)
        self._hooks[hook_type].sort(key=lambda r: r.priority)

    def unregister(self, name: str) -> int:
        """Remove all hooks with the given name.

        Returns the number of hooks removed.
        """
        removed = 0
        for ht in HookType:
            before = len(self._hooks[ht])
            self._hooks[ht] = [r for r in self._hooks[ht] if r.name != name]
            removed += before - len(self._hooks[ht])
        return removed

    def fire(self, ctx: HookContext) -> HookContext | None:
        """Fire all hooks for ctx.hook_type in priority order.

        If any hook returns None, the chain aborts (the action is cancelled).
        If a hook returns a modified HookContext, subsequent hooks see the modified version.

        Returns the final HookContext, or None if a hook aborted the chain.
        """
        hooks = self._hooks.get(ctx.hook_type, [])
        current = ctx
        for reg in hooks:
            result = reg.callback(current)
            if result is None:
                return None  # Chain aborted
            current = result
        return current

    def hooks_for(self, hook_type: HookType) -> list[HookRegistration]:
        """Return all registered hooks for a given type."""
        return list(self._hooks.get(hook_type, []))

    def clear(self, hook_type: HookType | None = None) -> None:
        """Clear hooks. If hook_type is None, clears all."""
        if hook_type is not None:
            self._hooks[hook_type] = []
        else:
            self._hooks = {ht: [] for ht in HookType}

    @property
    def hook_count(self) -> int:
        """Total number of registered hooks."""
        return sum(len(v) for v in self._hooks.values())


# Global registry singleton
_registry: HookRegistry | None = None


def get_hook_registry() -> HookRegistry:
    """Get the global HookRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = HookRegistry()
    return _registry


def register_hook(
    hook_type: HookType,
    callback: HookCallback,
    name: str = "",
    priority: int = 0,
) -> None:
    """Convenience: register a hook on the global registry."""
    get_hook_registry().register(hook_type, callback, name, priority)
