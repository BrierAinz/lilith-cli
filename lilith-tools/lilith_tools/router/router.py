"""SmartToolRouter — unified interface for semantic matching, chaining, recovery, analytics, and hook-gated execution."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_core.sandbox import AgentSandbox, SandboxPolicy, SandboxRegistry, get_sandbox_registry

from lilith_tools.base import ToolResult
from lilith_tools.router.analytics import ToolAnalytics, ToolUsage
from lilith_tools.router.chainer import ChainExecutor, ChainResult, ToolChain
from lilith_tools.router.matcher import MatchResult, ToolMatcher
from lilith_tools.router.recovery import FallbackChain, RecoveryManager, RetryPolicy


if TYPE_CHECKING:
    from pathlib import Path

    from lilith_tools.registry import ToolRegistry


class ToolGateError(Exception):
    """Raised when a pre_tool_call hook gates (blocks) a tool execution."""


class SmartToolRouter:
    """High-level router that combines semantic matching, chaining, recovery,
    analytics, and hook-gated execution into a single facade.

    Hook integration (inspired by Talon's hooks system):
        - ``pre_tool_call`` hooks fire before execution. A hook can:
            - Return None to GATE (block) the execution.
            - Modify params in the HookContext data dict.
            - Pass through unchanged.
        - ``post_tool_call`` hooks fire after execution. A hook can:
            - Modify the ToolResult (via ctx.data["result"]).
            - Return None to suppress the result.

    Usage::

        router = SmartToolRouter(ToolRegistry)
        matches = router.find_tools("search the web")
        result  = router.execute_tool("web_search", {"query": "python"})
    """

    def __init__(
        self,
        registry: ToolRegistry,
        analytics_path: Path | None = None,
        sandbox_registry: SandboxRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.matcher = ToolMatcher()
        self.executor = ChainExecutor(registry)
        self.recovery = RecoveryManager(registry)
        self.analytics = ToolAnalytics(db_path=analytics_path)
        self._hooks = get_hook_registry()
        self._sandbox_registry = sandbox_registry or get_sandbox_registry()
        self._active_sandboxes: dict[str, AgentSandbox] = {}

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    def find_tools(self, query: str, top_k: int = 3) -> list[MatchResult]:
        """Find the most relevant tools for a natural-language *query*."""
        tools = self.registry._tools
        return self.matcher.match(query, tools, top_k=top_k)

    # ------------------------------------------------------------------
    # Execution with retry + hooks
    # ------------------------------------------------------------------

    def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        retry_policy: RetryPolicy | None = None,
        agent_name: str = "",
        session_id: str = "",
    ) -> ToolResult:
        """Execute a tool by *name*, optionally retrying on failure.

        Pre-tool hooks fire before execution. If any hook returns None,
        the execution is gated (blocked) and a failed ToolResult is returned.

        Post-tool hooks fire after execution. If any hook returns None,
        the result is suppressed (returned as failed).

        Args:
            name: Tool name to execute.
            params: Parameters dict for the tool.
            retry_policy: Optional retry configuration.
            agent_name: Name of the calling agent (for hook context).
            session_id: Session ID (for hook context).
        """
        start = time.time()

        # ── Sandbox check: gate tool based on agent policy ────────────────
        sandbox = self._get_or_create_sandbox(agent_name)
        if sandbox.is_active:
            try:
                allowed = sandbox.check_tool(name)
            except Exception as exc:
                # Sandbox blocked the tool
                duration_ms = (time.time() - start) * 1000
                blocked_result = ToolResult(
                    success=False,
                    data=None,
                    error=f"Tool '{name}' blocked by sandbox: {exc}",
                )
                self.analytics.record(
                    ToolUsage(
                        tool_name=name,
                        timestamp=time.time(),
                        success=False,
                        duration_ms=duration_ms,
                        error=f"sandbox_blocked: {exc}",
                    )
                )
                return blocked_result
            if not allowed:
                duration_ms = (time.time() - start) * 1000
                blocked_result = ToolResult(
                    success=False,
                    data=None,
                    error=f"Tool '{name}' blocked by sandbox policy",
                )
                self.analytics.record(
                    ToolUsage(
                        tool_name=name,
                        timestamp=time.time(),
                        success=False,
                        duration_ms=duration_ms,
                        error="sandbox_blocked",
                    )
                )
                return blocked_result

        # ── Pre-tool hooks: gate / approve / rewrite ──────────────────────
        pre_ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name=agent_name,
            session_id=session_id,
            data={"tool_name": name, "params": dict(params)},
        )
        pre_result = self._hooks.fire(pre_ctx)
        if pre_result is None:
            # A hook gated this execution
            duration_ms = (time.time() - start) * 1000
            gated_result = ToolResult(
                success=False,
                data=None,
                error=f"Tool '{name}' gated by pre_tool_call hook",
            )
            self.analytics.record(
                ToolUsage(
                    tool_name=name,
                    timestamp=time.time(),
                    success=False,
                    duration_ms=duration_ms,
                    error="gated_by_hook",
                )
            )
            return gated_result

        # Hooks may have modified params
        effective_params = pre_result.data.get("params", params)

        # ── Execute ────────────────────────────────────────────────────────
        result = self.recovery.execute_with_retry(name, effective_params, policy=retry_policy)
        duration_ms = (time.time() - start) * 1000

        # ── Post-tool hooks: modify / suppress ────────────────────────────
        post_ctx = HookContext(
            hook_type=HookType.POST_TOOL_CALL,
            agent_name=agent_name,
            session_id=session_id,
            data={"tool_name": name, "params": effective_params, "result": result},
        )
        post_result = self._hooks.fire(post_ctx)
        if post_result is None:
            # A hook suppressed the result
            result = ToolResult(
                success=False,
                data=None,
                error=f"Tool '{name}' result suppressed by post_tool_call hook",
            )
        else:
            # Hooks may have modified the result
            result = post_result.data.get("result", result)

        # Record analytics
        self.analytics.record(
            ToolUsage(
                tool_name=name,
                timestamp=time.time(),
                success=result.success,
                duration_ms=duration_ms,
                error=result.error,
            )
        )
        return result

    # ------------------------------------------------------------------
    # Fallback execution
    # ------------------------------------------------------------------

    def execute_with_fallback(
        self,
        chain: FallbackChain,
        params: dict[str, Any],
        agent_name: str = "",
        session_id: str = "",
    ) -> ToolResult:
        """Execute a *FallbackChain*, trying each tool until one succeeds.

        Pre/post hooks fire for each tool attempt.
        """
        start = time.time()

        all_tools = [chain.primary] + chain.fallbacks
        for tool_name in all_tools:
            try:
                result = self.execute_tool(
                    tool_name, params,
                    agent_name=agent_name, session_id=session_id,
                )
                if result.success:
                    duration_ms = (time.time() - start) * 1000
                    self.analytics.record(
                        ToolUsage(
                            tool_name=tool_name,
                            timestamp=time.time(),
                            success=True,
                            duration_ms=duration_ms,
                        )
                    )
                    return result
            except Exception:
                continue

        duration_ms = (time.time() - start) * 1000
        result = self.recovery.execute_with_fallback(chain, params)
        self.analytics.record(
            ToolUsage(
                tool_name=chain.primary,
                timestamp=time.time(),
                success=result.success,
                duration_ms=duration_ms,
                error=result.error,
            )
        )
        return result

    # ------------------------------------------------------------------
    # Chain execution
    # ------------------------------------------------------------------

    def execute_chain(
        self,
        chain: ToolChain,
        context: dict[str, Any] | None = None,
        agent_name: str = "",
        session_id: str = "",
    ) -> ChainResult:
        """Execute a *ToolChain* and record analytics for each step.

        Pre/post hooks fire for each step in the chain.
        """
        start = time.time()

        # Apply pre-tool hooks to each step's params
        effective_context = dict(context or {})
        for step in chain.steps:
            pre_ctx = HookContext(
                hook_type=HookType.PRE_TOOL_CALL,
                agent_name=agent_name,
                session_id=session_id,
                data={"tool_name": step.tool_name, "params": dict(step.params or {})},
            )
            pre_result = self._hooks.fire(pre_ctx)
            if pre_result is None:
                # Step gated — skip it
                continue
            step.params = pre_result.data.get("params", step.params)

        result = self.executor.execute(chain, context=effective_context)
        duration_ms = (time.time() - start) * 1000

        for step in chain.steps:
            self.analytics.record(
                ToolUsage(
                    tool_name=step.tool_name,
                    timestamp=time.time(),
                    success=result.success,
                    duration_ms=duration_ms / max(len(chain.steps), 1),
                    error="; ".join(result.errors) if result.errors else "",
                )
            )
        return result

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Return analytics-based recommendations for tool optimisation."""
        return self.analytics.get_recommendations()

    # ------------------------------------------------------------------
    # Hook management
    # ------------------------------------------------------------------

    def register_pre_tool_hook(self, callback, name: str = "", priority: int = 0) -> None:
        """Register a pre-tool-call hook on this router's hook registry."""
        self._hooks.register(HookType.PRE_TOOL_CALL, callback, name=name, priority=priority)

    def register_post_tool_hook(self, callback, name: str = "", priority: int = 0) -> None:
        """Register a post-tool-call hook on this router's hook registry."""
        self._hooks.register(HookType.POST_TOOL_CALL, callback, name=name, priority=priority)

    # ------------------------------------------------------------------
    # Sandbox management
    # ------------------------------------------------------------------

    def _get_or_create_sandbox(self, agent_name: str) -> AgentSandbox:
        """Get or create an AgentSandbox for the given agent.

        If the agent has a registered policy in the sandbox registry,
        it is used. Otherwise, a permissive default sandbox is returned.
        """
        if not agent_name:
            # No agent specified — return a permissive default
            policy = SandboxPolicy(name="default", rules=[])
            return AgentSandbox(policy)

        key = agent_name.lower()
        if key in self._active_sandboxes:
            return self._active_sandboxes[key]

        policy = self._sandbox_registry.get(agent_name)
        if policy is None:
            policy = SandboxPolicy(name="default", rules=[])

        sandbox = AgentSandbox(policy)
        self._active_sandboxes[key] = sandbox
        return sandbox

    def start_sandbox(self, agent_name: str) -> AgentSandbox:
        """Explicitly start a sandbox for an agent.

        Returns the sandbox instance. Use as a context manager or
        call stop_sandbox() when done.
        """
        sandbox = self._get_or_create_sandbox(agent_name)
        sandbox.__enter__()
        self._active_sandboxes[agent_name.lower()] = sandbox
        return sandbox

    def stop_sandbox(self, agent_name: str) -> None:
        """Stop the sandbox for an agent."""
        key = agent_name.lower()
        sandbox = self._active_sandboxes.pop(key, None)
        if sandbox:
            sandbox.__exit__(None, None, None)

    def get_sandbox_stats(self, agent_name: str) -> dict[str, Any] | None:
        """Return sandbox statistics for an agent, or None if no sandbox."""
        sandbox = self._active_sandboxes.get(agent_name.lower())
        if sandbox:
            return sandbox.stats()
        return None

    def clear_hooks(self) -> None:
        """Clear all pre/post tool hooks."""
        self._hooks.clear(HookType.PRE_TOOL_CALL)
        self._hooks.clear(HookType.POST_TOOL_CALL)
