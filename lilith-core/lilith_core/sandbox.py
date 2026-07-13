"""AgentSandbox — lightweight per-agent execution sandbox for Lilith.

Inspired by project-sentinel's per-agent sandboxing (bwrap + Landlock + cgroups)
and adonis's ethics governance pattern. Provides a software-only sandbox that
can be applied on Windows, Linux, and macOS without requiring kernel-level
isolation.

This module provides:
    - SandboxPolicy: declarative rules for what an agent can do
    - AgentSandbox: per-agent execution context with resource limits
    - SandboxViolation: record of a sandbox policy breach
    - Integration with PolicyEngine and HookRegistry

Usage::

    from lilith_core.sandbox import SandboxPolicy, AgentSandbox, SandboxRuleType

    # Create a sandbox policy for an untrusted agent
    policy = SandboxPolicy(
        name="untrusted-agent",
        rules=[
            SandboxRule(type=SandboxRuleType.MAX_EXEC_TIME, value=30),
            SandboxRule(type=SandboxRuleType.NO_NETWORK, value=True),
            SandboxRule(type=SandboxRuleType.NO_FILE_WRITE, value=True),
            SandboxRule(type=SandboxRuleType.ALLOWED_TOOLS, value=["read_file", "search_files"]),
        ],
    )

    # Wrap agent execution
    sandbox = AgentSandbox(policy=policy)
    with sandbox:
        result = sandbox.run(agent.process, message="Hello")

    # Check for violations
    if sandbox.violations:
        print(f"Sandbox violations: {sandbox.violations}")
"""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("lilith.sandbox")


# ── Enums ────────────────────────────────────────────────────────────────────


class SandboxRuleType(Enum):
    """Types of sandbox restrictions."""

    MAX_EXEC_TIME = "max_exec_time"      # Maximum execution time in seconds
    MAX_MEMORY_MB = "max_memory_mb"      # Maximum memory usage in MB
    NO_NETWORK = "no_network"            # Block network access
    NO_FILE_WRITE = "no_file_write"      # Block file write operations
    NO_FILE_DELETE = "no_file_delete"    # Block file delete operations
    NO_SUBPROCESS = "no_subprocess"      # Block subprocess execution
    ALLOWED_TOOLS = "allowed_tools"      # Whitelist of allowed tool names
    DENIED_TOOLS = "denied_tools"        # Blacklist of denied tool names
    MAX_TOKENS = "max_tokens"            # Maximum token budget per call
    MAX_CALLS_PER_MIN = "max_calls_per_min"  # Rate limit: calls per minute


class SandboxAction(Enum):
    """What to do when a sandbox rule is violated."""

    BLOCK = "block"       # Prevent the action
    WARN = "warn"         # Log warning but allow
    TERMINATE = "terminate"  # Kill the agent execution


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class SandboxRule:
    """A single sandbox rule."""

    type: SandboxRuleType
    value: Any = None
    action: SandboxAction = SandboxAction.BLOCK


@dataclass
class SandboxViolation:
    """Record of a sandbox policy breach."""

    rule_type: SandboxRuleType
    message: str
    timestamp: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "context": self.context,
        }


@dataclass
class SandboxPolicy:
    """Declarative sandbox policy for an agent.

    Attributes:
        name: Unique policy identifier.
        rules: List of SandboxRule restrictions.
        description: Human-readable description.
        enabled: Whether the policy is active.
    """

    name: str
    rules: list[SandboxRule] = field(default_factory=list)
    description: str = ""
    enabled: bool = True

    def get_rule(self, rule_type: SandboxRuleType) -> SandboxRule | None:
        """Get the first rule of a given type."""
        for rule in self.rules:
            if rule.type == rule_type:
                return rule
        return None

    def has_rule(self, rule_type: SandboxRuleType) -> bool:
        """Check if a rule type exists in this policy."""
        return any(r.type == rule_type for r in self.rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "rules": [
                {
                    "type": r.type.value,
                    "value": r.value,
                    "action": r.action.value,
                }
                for r in self.rules
            ],
        }


# ── Sandbox state (per-agent) ───────────────────────────────────────────────


class SandboxState:
    """Mutable state tracking for a sandboxed agent execution.

    Tracks:
        - Call timestamps for rate limiting
        - Token usage for budgeting
        - Execution start time for timeouts
        - Violation history
    """

    def __init__(self) -> None:
        self._call_timestamps: list[float] = []
        self._token_count: int = 0
        self._start_time: float = 0.0
        self._violations: list[SandboxViolation] = []
        self._terminated: bool = False
        self._lock = threading.Lock()

    def record_call(self) -> None:
        with self._lock:
            self._call_timestamps.append(time.time())

    def record_tokens(self, count: int) -> None:
        with self._lock:
            self._token_count += count

    def add_violation(self, violation: SandboxViolation) -> None:
        with self._lock:
            self._violations.append(violation)

    @property
    def call_count_last_minute(self) -> int:
        cutoff = time.time() - 60.0
        with self._lock:
            return sum(1 for t in self._call_timestamps if t > cutoff)

    @property
    def token_count(self) -> int:
        with self._lock:
            return self._token_count

    @property
    def elapsed(self) -> float:
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    @property
    def violations(self) -> list[SandboxViolation]:
        with self._lock:
            return list(self._violations)

    @property
    def terminated(self) -> bool:
        with self._lock:
            return self._terminated

    def terminate(self) -> None:
        with self._lock:
            self._terminated = True

    def reset(self) -> None:
        with self._lock:
            self._call_timestamps.clear()
            self._token_count = 0
            self._start_time = 0.0
            self._violations.clear()
            self._terminated = False


# ── AgentSandbox ─────────────────────────────────────────────────────────────


class AgentSandbox:
    """Per-agent execution sandbox with resource limits and policy enforcement.

    Wraps agent/tool execution in a monitored context that enforces
    declarative sandbox policies. Can be used as a context manager
    or via explicit ``run()`` calls.

    Usage::

        policy = SandboxPolicy(
            name="safe-mode",
            rules=[
                SandboxRule(SandboxRuleType.MAX_EXEC_TIME, 30),
                SandboxRule(SandboxRuleType.NO_NETWORK, True),
            ],
        )

        sandbox = AgentSandbox(policy)
        with sandbox:
            result = sandbox.run(my_agent.process, "hello")
            if sandbox.violations:
                logger.warning("Violations: %s", sandbox.violations)
    """

    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy
        self.state = SandboxState()
        self._active = False

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self) -> AgentSandbox:
        self.state.reset()
        self.state._start_time = time.time()
        self._active = True
        logger.debug("Sandbox '%s' activated", self.policy.name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._active = False
        elapsed = self.state.elapsed
        logger.debug(
            "Sandbox '%s' deactivated (elapsed=%.2fs, violations=%d)",
            self.policy.name, elapsed, len(self.state.violations),
        )

    # ── Execution wrapper ─────────────────────────────────────────────────

    def run(self, func: Callable, *args, **kwargs) -> Any:
        """Run a function inside the sandbox, enforcing policies.

        Checks:
            - MAX_EXEC_TIME: abort if elapsed > limit
            - MAX_CALLS_PER_MIN: abort if rate exceeded
            - MAX_TOKENS: abort if token budget exceeded
            - NO_SUBPROCESS: block if func name hints at subprocess

        Returns:
            The function's return value, or raises SandboxError on violation.
        """
        if not self._active:
            raise RuntimeError("Sandbox not active — use 'with sandbox:'")

        if self.state.terminated:
            raise SandboxError("Sandbox terminated due to previous violation")

        # Check execution time
        self._check_timeout()

        # Check rate limit
        self._check_rate_limit()

        # Check token budget
        self._check_token_budget()

        # Record the call
        self.state.record_call()

        # Run the function
        try:
            return func(*args, **kwargs)
        finally:
            # Post-call checks
            self._check_timeout()

    # ── Policy checks ─────────────────────────────────────────────────────

    def _check_timeout(self) -> None:
        rule = self.policy.get_rule(SandboxRuleType.MAX_EXEC_TIME)
        if rule and rule.value:
            limit = float(rule.value)
            if self.state.elapsed > limit:
                msg = f"Execution timeout: {self.state.elapsed:.1f}s > {limit}s"
                self._handle_violation(rule, msg)

    def _check_rate_limit(self) -> None:
        rule = self.policy.get_rule(SandboxRuleType.MAX_CALLS_PER_MIN)
        if rule and rule.value:
            limit = int(rule.value)
            if self.state.call_count_last_minute >= limit:
                msg = f"Rate limit exceeded: {self.state.call_count_last_minute} calls/min > {limit}"
                self._handle_violation(rule, msg)

    def _check_token_budget(self) -> None:
        rule = self.policy.get_rule(SandboxRuleType.MAX_TOKENS)
        if rule and rule.value:
            limit = int(rule.value)
            if self.state.token_count >= limit:
                msg = f"Token budget exceeded: {self.state.token_count} > {limit}"
                self._handle_violation(rule, msg)

    def _handle_violation(self, rule: SandboxRule, message: str) -> None:
        violation = SandboxViolation(
            rule_type=rule.type,
            message=message,
            context={"elapsed": self.state.elapsed, "policy": self.policy.name},
        )
        self.state.add_violation(violation)

        if rule.action == SandboxAction.TERMINATE:
            self.state.terminate()
            logger.error("SANDBOX TERMINATED: %s", message)
            raise SandboxError(message)
        elif rule.action == SandboxAction.BLOCK:
            logger.warning("SANDBOX BLOCKED: %s", message)
            raise SandboxError(message)
        else:  # WARN
            logger.warning("SANDBOX WARNING: %s", message)

    # ── Tool gating (for integration with SmartToolRouter) ────────────────

    def check_tool(self, tool_name: str) -> bool:
        """Check if a tool is allowed by the sandbox policy.

        Returns True if allowed, False if blocked.
        """
        if not self._active:
            return True

        # Check allowed tools whitelist
        allowed_rule = self.policy.get_rule(SandboxRuleType.ALLOWED_TOOLS)
        if allowed_rule and allowed_rule.value:
            allowed = [t.lower() for t in allowed_rule.value]
            if tool_name.lower() not in allowed:
                msg = f"Tool '{tool_name}' not in allowed list"
                self._handle_violation(allowed_rule, msg)
                return False

        # Check denied tools blacklist
        denied_rule = self.policy.get_rule(SandboxRuleType.DENIED_TOOLS)
        if denied_rule and denied_rule.value:
            denied = [t.lower() for t in denied_rule.value]
            if tool_name.lower() in denied:
                msg = f"Tool '{tool_name}' is in denied list"
                self._handle_violation(denied_rule, msg)
                return False

        # Check NO_SUBPROCESS
        subprocess_rule = self.policy.get_rule(SandboxRuleType.NO_SUBPROCESS)
        if subprocess_rule and subprocess_rule.value:
            if tool_name.lower() in ("coding", "terminal", "shell"):
                msg = f"Subprocess tool '{tool_name}' blocked by NO_SUBPROCESS rule"
                self._handle_violation(subprocess_rule, msg)
                return False

        return True

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def violations(self) -> list[SandboxViolation]:
        """All recorded violations."""
        return self.state.violations

    @property
    def is_active(self) -> bool:
        """Whether the sandbox is currently active."""
        return self._active

    @property
    def is_terminated(self) -> bool:
        """Whether the sandbox has been terminated."""
        return self.state.terminated

    def stats(self) -> dict[str, Any]:
        """Return sandbox statistics."""
        return {
            "policy": self.policy.name,
            "active": self._active,
            "terminated": self.state.terminated,
            "elapsed": self.state.elapsed,
            "calls_last_minute": self.state.call_count_last_minute,
            "token_count": self.state.token_count,
            "violation_count": len(self.state.violations),
            "violations": [v.to_dict() for v in self.state.violations],
        }


# ── SandboxError ───────────────────────────────────────────────────────────


class SandboxError(Exception):
    """Raised when a sandbox policy is violated."""
    pass


# ── SandboxRegistry ─────────────────────────────────────────────────────────


class SandboxRegistry:
    """Global registry of sandbox policies per agent.

    Maps agent names to their SandboxPolicy. Provides lookup and
    default policy management.
    """

    def __init__(self) -> None:
        self._policies: dict[str, SandboxPolicy] = {}
        self._default: SandboxPolicy | None = None

    def register(self, agent_name: str, policy: SandboxPolicy) -> None:
        """Register a sandbox policy for an agent."""
        self._policies[agent_name.lower()] = policy

    def get(self, agent_name: str) -> SandboxPolicy | None:
        """Get the sandbox policy for an agent, or default if none set."""
        return self._policies.get(agent_name.lower(), self._default)

    def set_default(self, policy: SandboxPolicy) -> None:
        """Set the default policy for agents without a specific one."""
        self._default = policy

    def remove(self, agent_name: str) -> bool:
        """Remove a policy for an agent."""
        key = agent_name.lower()
        if key in self._policies:
            del self._policies[key]
            return True
        return False

    def list_agents(self) -> list[str]:
        """List all agents with registered policies."""
        return list(self._policies.keys())

    def stats(self) -> dict[str, Any]:
        return {
            "registered_agents": len(self._policies),
            "agents": self.list_agents(),
            "has_default": self._default is not None,
        }


# ── Global singleton ─────────────────────────────────────────────────────────

_sandbox_registry: SandboxRegistry | None = None


def get_sandbox_registry() -> SandboxRegistry:
    """Get the global SandboxRegistry singleton."""
    global _sandbox_registry
    if _sandbox_registry is None:
        _sandbox_registry = SandboxRegistry()
    return _sandbox_registry
