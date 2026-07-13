"""Omnigent-inspired Policy Engine for workflow execution.

Provides policy enforcement with:
- Allow/deny lists for tool calls
- Resource limits per agent (time, memory, tool count)
- Sandboxed execution paths
- Audit trail for all policy decisions

Inspired by omnigent-ai/omnigent (5134 stars, June 2026).
Integrates with WorkflowEngine and PipelineRunner.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

logger = logging.getLogger("lilith.orchestrator.policy")


# ── Enums ──────────────────────────────────────────────────────────────────


class PolicyDecision(Enum):
    """Result of a policy check."""

    ALLOW = "allow"
    DENY = "deny"
    AUDIT = "audit"  # Allow but record in audit log


class PolicyViolation(Enum):
    """Categories of policy violations."""

    TOOL_NOT_ALLOWED = "tool_not_allowed"
    TOOL_FORBIDDEN = "tool_forbidden"
    RESOURCE_EXCEEDED = "resource_exceeded"
    PATH_NOT_ALLOWED = "path_not_allowed"
    RATE_LIMITED = "rate_limited"


# ── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class PolicyConfig:
    """Configuration for policy enforcement on an agent/workflow.

    Attributes:
        allowed_tools: Whitelist of tool names this policy permits.
        forbidden_tools: Blacklist of tool names this policy denies.
        allowed_paths: Glob patterns for filesystem paths that can be accessed.
        forbidden_paths: Glob patterns for filesystem paths that are denied.
        max_tool_calls: Maximum tool invocations per workflow execution.
        max_wall_time_seconds: Maximum total wall-clock time for a workflow.
        max_memory_mb: Maximum memory usage in MB (advisory).
        rate_limit_per_minute: Maximum tool calls per minute.
        audit_all: If True, log every decision to audit trail.
    """

    allowed_tools: set[str] = field(default_factory=set)
    forbidden_tools: set[str] = field(default_factory=set)
    allowed_paths: set[str] = field(default_factory=set)
    forbidden_paths: set[str] = field(default_factory=set)
    max_tool_calls: int = 1000
    max_wall_time_seconds: float = 3600.0
    max_memory_mb: int = 4096
    rate_limit_per_minute: int = 120
    audit_all: bool = False

    def is_path_allowed(self, path: str) -> bool:
        """Check if a path is allowed under this policy.

        Order of evaluation:
        1. If forbidden_paths matches → deny
        2. If allowed_paths is empty → allow (no restrictions)
        3. If allowed_paths matches → allow
        4. Otherwise → deny

        Cross-platform: handles both POSIX (/path) and Windows (C:\\path) styles.
        """
        # Normalize path: strip trailing separators, normalize slashes
        path_norm = path.replace("\\", "/").rstrip("/")
        if not path_norm:
            return True

        def matches_prefix(pattern: str) -> bool:
            """Check if path_norm equals pattern or is a child of it."""
            pat = pattern.replace("\\", "/").rstrip("/")
            if not pat:
                return False
            if path_norm == pat:
                return True
            # Must be under pattern with separator boundary
            if path_norm.startswith(pat + "/"):
                return True
            return False

        # Check forbidden first
        for pattern in self.forbidden_paths:
            if matches_prefix(pattern):
                return False
        # If no allowed list, allow (whitelist is optional)
        if not self.allowed_paths:
            return True
        # Check allowed
        for pattern in self.allowed_paths:
            if matches_prefix(pattern):
                return True
        return False


@dataclass
class PolicyEvent:
    """A single policy decision event for the audit trail."""

    timestamp: float
    agent_name: str
    decision: PolicyDecision
    violation: PolicyViolation | None
    tool_name: str | None
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyState:
    """Mutable state tracking policy enforcement during a workflow run.

    Tracks:
    - tool call counts (total + per-minute)
    - wall time elapsed
    - audit event history
    """

    config: PolicyConfig
    started_at: float = field(default_factory=time.time)
    tool_calls_total: int = 0
    _per_minute_buckets: dict[int, int] = field(default_factory=dict)
    audit_trail: list[PolicyEvent] = field(default_factory=list)

    @property
    def wall_time_elapsed(self) -> float:
        return time.time() - self.started_at

    @property
    def current_minute_bucket(self) -> int:
        return int(self.wall_time_elapsed // 60)

    def record_tool_call(self, minute: int | None = None) -> int:
        """Record a tool call and return the new total for the current minute."""
        bucket = minute if minute is not None else self.current_minute_bucket
        self._per_minute_buckets[bucket] = self._per_minute_buckets.get(bucket, 0) + 1
        self.tool_calls_total += 1
        return self._per_minute_buckets[bucket]


# ── Policy Engine ───────────────────────────────────────────────────────────


class PolicyEngine:
    """Omnigent-style policy engine.

    Evaluates tool calls and resource usage against a PolicyConfig.
    Returns ALLOW/DENY/AUDIT decisions and maintains an audit trail.
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()
        self._states: dict[str, PolicyState] = {}

    def create_state(self, agent_name: str) -> PolicyState:
        """Create a fresh PolicyState for a workflow run."""
        state = PolicyState(config=self.config)
        self._states[agent_name] = state
        return state

    def get_state(self, agent_name: str) -> PolicyState | None:
        return self._states.get(agent_name)

    def check_tool(
        self,
        agent_name: str,
        tool_name: str,
        *,
        path: str | None = None,
    ) -> tuple[PolicyDecision, PolicyViolation | None, str]:
        """Evaluate whether `agent_name` may invoke `tool_name`.

        Returns: (decision, violation_or_None, human_readable_detail)
        """
        state = self.get_state(agent_name) or self.create_state(agent_name)
        cfg = state.config

        # Check 1: Forbidden tools (blacklist wins)
        if tool_name in cfg.forbidden_tools:
            self._record(state, agent_name, PolicyDecision.DENY,
                         PolicyViolation.TOOL_FORBIDDEN, tool_name,
                         f"Tool '{tool_name}' is in forbidden list")
            return PolicyDecision.DENY, PolicyViolation.TOOL_FORBIDDEN, \
                f"Tool '{tool_name}' is forbidden"

        # Check 2: Allowed tools (if whitelist configured)
        if cfg.allowed_tools and tool_name not in cfg.allowed_tools:
            self._record(state, agent_name, PolicyDecision.DENY,
                         PolicyViolation.TOOL_NOT_ALLOWED, tool_name,
                         f"Tool '{tool_name}' not in allowed list")
            return PolicyDecision.DENY, PolicyViolation.TOOL_NOT_ALLOWED, \
                f"Tool '{tool_name}' is not allowed"

        # Check 3: Path-level (if path provided)
        if path is not None and not cfg.is_path_allowed(path):
            self._record(state, agent_name, PolicyDecision.DENY,
                         PolicyViolation.PATH_NOT_ALLOWED, tool_name,
                         f"Path '{path}' not allowed")
            return PolicyDecision.DENY, PolicyViolation.PATH_NOT_ALLOWED, \
                f"Path '{path}' not allowed by policy"

        # Check 4: Total tool call limit
        if state.tool_calls_total >= cfg.max_tool_calls:
            self._record(state, agent_name, PolicyDecision.DENY,
                         PolicyViolation.RESOURCE_EXCEEDED, tool_name,
                         f"Tool call limit {cfg.max_tool_calls} reached")
            return PolicyDecision.DENY, PolicyViolation.RESOURCE_EXCEEDED, \
                f"Tool call limit ({cfg.max_tool_calls}) exceeded"

        # Check 5: Wall time limit
        if state.wall_time_elapsed >= cfg.max_wall_time_seconds:
            self._record(state, agent_name, PolicyDecision.DENY,
                         PolicyViolation.RESOURCE_EXCEEDED, tool_name,
                         f"Wall time {cfg.max_wall_time_seconds}s exceeded")
            return PolicyDecision.DENY, PolicyViolation.RESOURCE_EXCEEDED, \
                f"Wall time limit ({cfg.max_wall_time_seconds}s) exceeded"

        # Check 6: Rate limit (per-minute)
        calls_this_minute = state.record_tool_call()
        if calls_this_minute > cfg.rate_limit_per_minute:
            self._record(state, agent_name, PolicyDecision.DENY,
                         PolicyViolation.RATE_LIMITED, tool_name,
                         f"Rate limit {cfg.rate_limit_per_minute}/min exceeded")
            return PolicyDecision.DENY, PolicyViolation.RATE_LIMITED, \
                f"Rate limit ({cfg.rate_limit_per_minute}/min) exceeded"

        # All checks passed → allow (audit if configured)
        decision = PolicyDecision.AUDIT if cfg.audit_all else PolicyDecision.ALLOW
        self._record(state, agent_name, decision, None, tool_name,
                     f"Tool '{tool_name}' allowed")
        return decision, None, f"Tool '{tool_name}' allowed"

    def audit(self, agent_name: str) -> list[PolicyEvent]:
        """Return the audit trail for an agent."""
        state = self.get_state(agent_name)
        return list(state.audit_trail) if state else []

    def reset(self, agent_name: str | None = None) -> None:
        """Clear state for one agent, or all agents."""
        if agent_name is None:
            self._states.clear()
        else:
            self._states.pop(agent_name, None)

    def _record(
        self,
        state: PolicyState,
        agent_name: str,
        decision: PolicyDecision,
        violation: PolicyViolation | None,
        tool_name: str | None,
        detail: str,
        **metadata: Any,
    ) -> None:
        """Append a PolicyEvent to the audit trail."""
        event = PolicyEvent(
            timestamp=time.time(),
            agent_name=agent_name,
            decision=decision,
            violation=violation,
            tool_name=tool_name,
            detail=detail,
            metadata=metadata,
        )
        state.audit_trail.append(event)
        if decision == PolicyDecision.DENY:
            logger.warning(f"POLICY DENY [{agent_name}]: {detail}")
        elif decision == PolicyDecision.AUDIT:
            logger.info(f"POLICY AUDIT [{agent_name}]: {detail}")


# ── Decorators / Helpers ────────────────────────────────────────────────────


def with_policy(engine: PolicyEngine, agent_name: str):
    """Decorator that wraps a function to enforce policy on its tool calls.

    The wrapped function should accept a 'tool_name' kwarg that the
    decorator checks against the policy engine.

    Usage:
        engine = PolicyEngine(PolicyConfig(forbidden_tools={'delete_file'}))

        @with_policy(engine, "my_agent")
        def invoke(tool_name: str, **kwargs):
            return do_work(tool_name, **kwargs)
    """
    def decorator(fn):
        def wrapper(*args, tool_name: str = "", **kwargs):
            decision, violation, detail = engine.check_tool(
                agent_name, tool_name, path=kwargs.get("path")
            )
            if decision == PolicyDecision.DENY:
                raise PermissionError(f"Policy denied: {detail}")
            return fn(*args, tool_name=tool_name, **kwargs)
        return wrapper
    return decorator


__all__ = [
    "PolicyConfig",
    "PolicyDecision",
    "PolicyViolation",
    "PolicyEvent",
    "PolicyState",
    "PolicyEngine",
    "with_policy",
]
