"""Policy Engine for Lilith — declarative governance rules for agents.

Inspired by Omnigent's policy system and claude-code-agents' drift-reconciling.
Provides a governance layer ABOVE the hook system: instead of imperative
callbacks, you define declarative rules that are auto-registered as hooks.

Policy types:
    - TOOL_ALLOWLIST:  Only allow listed tools for an agent/scope.
    - TOOL_DENYLIST:   Deny specific tools for an agent/scope.
    - RATE_LIMIT:      Max N calls per time window (per agent or globally).
    - TOKEN_BUDGET:    Max tokens per session before gating LLM calls.
    - REQUIRE_APPROVAL: Flag calls for human review (returns context flag).
    - AUDIT_LOG:       Log all matching calls (non-blocking).

Usage::

    engine = PolicyEngine()

    # Only allow Odin to use terminal and read_file
    engine.add_policy(Policy(
        name="odin-tool-restrict",
        scope=PolicyScope(agent="Odin"),
        rule=ToolAllowlistRule(tools=["terminal", "read_file", "search_files"]),
        action=PolicyAction.DENY,
        priority=10,
    ))

    # Rate-limit all agents to 50 tool calls per minute
    engine.add_policy(Policy(
        name="global-rate-limit",
        scope=PolicyScope(),  # matches everything
        rule=RateLimitRule(max_calls=50, window_seconds=60),
        action=PolicyAction.DENY,
        priority=100,
    ))

    # Activate — registers hooks on the global HookRegistry
    engine.activate()
"""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from lilith_core.hooks import (
    HookContext,
    HookType,
    get_hook_registry,
)

logger = logging.getLogger("lilith.policy")


# ── Enums ────────────────────────────────────────────────────────────────────


class PolicyAction(Enum):
    """What happens when a policy condition matches."""

    ALLOW = "allow"  # Let it through (skip further policy checks)
    DENY = "deny"  # Block the action
    LOG = "log"  # Log but let through
    FLAG = "flag"  # Add a flag to context (for human review)


# ── Scope ────────────────────────────────────────────────────────────────────


@dataclass
class PolicyScope:
    """Defines what a policy applies to.

    All fields are optional — omitted fields match everything.
    E.g., PolicyScope(agent="Odin") matches all tools/sessions for Odin.
    PolicyScope() matches ALL agents, tools, and sessions.
    """

    agent: str = ""  # Agent name (substring match)
    tool: str = ""  # Tool name (substring match)
    session: str = ""  # Session ID (exact match)
    hook_type: HookType | None = None  # Only for specific hook types

    def matches(self, ctx: HookContext) -> bool:
        """Check if this scope matches the given hook context."""
        if self.agent and self.agent.lower() not in ctx.agent_name.lower():
            return False
        if self.session and self.session != ctx.session_id:
            return False
        if self.hook_type and self.hook_type != ctx.hook_type:
            return False
        if self.tool:
            tool_name = ctx.data.get("tool_name", "")
            if self.tool.lower() not in tool_name.lower():
                return False
        return True


# ── Rules (conditions) ──────────────────────────────────────────────────────


class PolicyRule(ABC):
    """Base class for policy rule conditions."""

    @abstractmethod
    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        """Return True if the rule matches (policy should fire)."""
        ...


@dataclass
class ToolAllowlistRule(PolicyRule):
    """Matches when the tool is NOT in the allowed list."""

    tools: list[str] = field(default_factory=list)

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        tool_name = ctx.data.get("tool_name", "")
        if not tool_name:
            return False  # Not a tool call — don't match
        return tool_name not in self.tools


@dataclass
class ToolDenylistRule(PolicyRule):
    """Matches when the tool IS in the denied list."""

    tools: list[str] = field(default_factory=list)

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        tool_name = ctx.data.get("tool_name", "")
        return tool_name in self.tools


@dataclass
class RateLimitRule(PolicyRule):
    """Matches when call count exceeds max_calls within window_seconds."""

    max_calls: int = 100
    window_seconds: float = 60.0

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        key = f"rate:{ctx.agent_name}:{ctx.session_id}"
        timestamps = state.get_list(key)
        now = time.time()
        cutoff = now - self.window_seconds
        # Count calls within window
        recent = [t for t in timestamps if t > cutoff]
        state.set_list(key, recent)
        return len(recent) >= self.max_calls


@dataclass
class TokenBudgetRule(PolicyRule):
    """Matches when token usage exceeds the budget for the session."""

    max_tokens: int = 100_000

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        key = f"tokens:{ctx.session_id}"
        used = state.get_counter(key)
        return used >= self.max_tokens


@dataclass
class ResourceLimitRule(PolicyRule):
    """Matches when concurrent resource usage for an agent exceeds per-agent caps.

    Three soft caps (all optional, default 0 = disabled):

        max_concurrent_calls — global cap on in-flight tool/LLM calls per agent
        max_payload_bytes    — reject ctx.data payloads larger than N bytes
        max_session_duration_seconds — reject long-running sessions

    Designed for use with **concurrent_or_recent**`` matching: rule fires when
    ANY cap is exceeded. Counting is per-agent+session; data-size accounting is
    per-call.

    Inspired by Omnigent's resource-governance layer: an agent that asks to
    upload a 2 GB file, run 100 calls in parallel, or stay alive for 8 hours
    gets denied automatically.
    """

    max_concurrent_calls: int = 0
    max_payload_bytes: int = 0
    max_session_duration_seconds: float = 0.0

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        # Payload-size cap (per-call)
        if self.max_payload_bytes:
            payload_size = 0
            for value in (ctx.data or {}).values():
                if isinstance(value, str):
                    payload_size += len(value.encode("utf-8"))
                elif isinstance(value, (bytes, bytearray)):
                    payload_size += len(value)
                elif isinstance(value, (dict, list)):
                    payload_size += len(json.dumps(value, default=str).encode("utf-8"))
                else:
                    payload_size += len(str(value).encode("utf-8"))
            if payload_size > self.max_payload_bytes:
                state.set(
                    f"resource_reason:{ctx.agent_name}:{ctx.session_id}",
                    f"payload_size={payload_size}>{self.max_payload_bytes}",
                )
                return True

        # Concurrent-call cap (per-agent+session). We track live calls by
        # looking at the difference between started and ended counters maintained
        # by PolicyEngine._record_call and lifecycle instrumentation.
        if self.max_concurrent_calls:
            inflight = state.get_counter(
                f"inflight:{ctx.agent_name}:{ctx.session_id}"
            )
            if inflight >= self.max_concurrent_calls:
                state.set(
                    f"resource_reason:{ctx.agent_name}:{ctx.session_id}",
                    f"inflight={inflight}>={self.max_concurrent_calls}",
                )
                return True

        # Session duration cap
        if self.max_session_duration_seconds:
            started = state.get(f"session_started:{ctx.agent_name}:{ctx.session_id}")
            if started:
                elapsed = time.time() - float(started)
                if elapsed > self.max_session_duration_seconds:
                    state.set(
                        f"resource_reason:{ctx.agent_name}:{ctx.session_id}",
                        f"elapsed={elapsed:.1f}>{self.max_session_duration_seconds}",
                    )
                    return True

        return False


@dataclass
class CircuitBreakerRule(PolicyRule):
    """Circuit breaker for runaway agents.

    Inspired by Omnigent's governance layer and the classic circuit-breaker
    pattern: when an agent accumulates too many violations within a sliding
    window, the breaker "trips" and denies ALL subsequent calls for a cooldown
    period, after which it automatically resets.

    Three-state machine (simple version):

        CLOSED  → counting violations, normal operation
        OPEN    → tripped; deny everything until cooldown elapses
        RESET   → cooldown elapsed; reset counters, return to CLOSED

    Parameters
    ----------
    max_violations : int
        Number of violations within ``window_seconds`` that triggers a trip.
        Default 5.
    window_seconds : float
        Sliding window used to count recent violations. Default 60s.
    cooldown_seconds : float
        How long the breaker stays OPEN before resetting. Default 30s.
    scope_key : str
        What to count separately. One of:
          - ``"agent_session"`` (default) — each (agent, session) pair
            has its own breaker. Same agent in a new session gets a fresh
            start.
          - ``"agent"`` — one breaker per agent across all sessions.
            Tripping affects every session for that agent.
          - ``"session"`` — one breaker per session across all agents.

    Violations are recorded by the ``PolicyEngine`` itself whenever a DENY
    action fires for this rule. The rule then evaluates whether the breaker
    is currently OPEN (deny) or CLOSED (allow). When CLOSED, it increments
    the violation counter using a windowed list.

    Example::

        # Trip after 3 violations within 60s; cool down for 30s
        engine.add_policy(Policy(
            name="global-circuit-breaker",
            scope=PolicyScope(),  # applies to everything
            rule=CircuitBreakerRule(
                max_violations=3,
                window_seconds=60,
                cooldown_seconds=30,
            ),
            action=PolicyAction.DENY,
            priority=1,  # evaluate early
        ))
    """

    max_violations: int = 5
    window_seconds: float = 60.0
    cooldown_seconds: float = 30.0
    scope_key: str = "agent_session"

    def _key(self, ctx: HookContext) -> str:
        if self.scope_key == "agent":
            return f"cb:agent:{ctx.agent_name}"
        if self.scope_key == "session":
            return f"cb:session:{ctx.session_id}"
        return f"cb:agent_session:{ctx.agent_name}:{ctx.session_id}"

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        """Return True if the breaker is currently OPEN (rule fires / deny).

        A fresh trip is recorded after the call returns True, so the caller
        increments once on the same evaluation that flipped the state.
        """
        key = self._key(ctx)
        now = time.time()

        # 1) Cooldown check — if tripped and cooldown not elapsed, deny
        trip_time_raw = state.get(f"{key}:trip_time")
        if trip_time_raw is not None:
            try:
                trip_time = float(trip_time_raw)
            except (TypeError, ValueError):
                trip_time = 0.0
            elapsed = now - trip_time
            if elapsed < self.cooldown_seconds:
                # Still OPEN — record the cool-down reason and deny
                state.set(
                    f"circuit_reason:{ctx.agent_name}:{ctx.session_id}",
                    f"open_for={self.cooldown_seconds - elapsed:.1f}s",
                )
                return True
            # Cooldown elapsed — reset state and fall through to CLOSED logic
            state.set(f"{key}:trip_time", "")
            state.set_list(f"{key}:violations", [])
            state.set(f"circuit_reason:{ctx.agent_name}:{ctx.session_id}", "")

        # 2) CLOSED — count recent violations within the window
        violations_key = f"{key}:violations"
        timestamps = state.get_list(violations_key)
        cutoff = now - self.window_seconds
        recent = [t for t in timestamps if t > cutoff]
        state.set_list(violations_key, recent)

        if len(recent) >= self.max_violations:
            # Threshold reached — trip the breaker right now
            state.set(f"{key}:trip_time", str(now))
            state.set_list(violations_key, [])
            state.set(
                f"circuit_reason:{ctx.agent_name}:{ctx.session_id}",
                f"tripped_after={len(recent)}_violations",
            )
            return True

        return False

    def record_violation(self, ctx: HookContext, state: PolicyState) -> None:
        """Record that this scope key just had a violation.

        Called by ``PolicyEngine`` after a DENY action involving this rule.
        Kept on the rule (rather than inlined in the engine) so each breaker
        instance owns its counter and there is no central registry to keep
        in sync.
        """
        key = self._key(ctx)
        state.append_list(f"{key}:violations", time.time())


@dataclass
class RegexRule(PolicyRule):
    """Matches when a field in ctx.data matches a regex pattern."""

    field_name: str = "message"
    pattern: str = ""

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        value = str(ctx.data.get(self.field_name, ""))
        if not self.pattern:
            return False
        return bool(re.search(self.pattern, value, re.IGNORECASE))


@dataclass
class AlwaysRule(PolicyRule):
    """Always matches. Useful for audit-logging or catch-all policies."""

    def evaluate(self, ctx: HookContext, state: PolicyState) -> bool:
        return True


# ── Policy State (shared mutable state for rules) ───────────────────────────


class PolicyState:
    """Shared mutable state that rules can read/write.

    Provides counters, lists, and a general key-value store.
    Thread-safe via simple dict operations (GIL protects CPython dicts).
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._lists: dict[str, list[float]] = defaultdict(list)
        self._kv: dict[str, Any] = {}

    def get_counter(self, key: str) -> int:
        return self._counters.get(key, 0)

    def increment(self, key: str, amount: int = 1) -> int:
        self._counters[key] = self._counters.get(key, 0) + amount
        return self._counters[key]

    def get_list(self, key: str) -> list[float]:
        return list(self._lists.get(key, []))

    def set_list(self, key: str, values: list[float]) -> None:
        self._lists[key] = values

    def append_list(self, key: str, value: float) -> None:
        self._lists[key].append(value)

    def get(self, key: str, default: Any = None) -> Any:
        return self._kv.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._kv[key] = value

    def reset(self) -> None:
        """Clear all state."""
        self._counters.clear()
        self._lists.clear()
        self._kv.clear()


# ── Policy ───────────────────────────────────────────────────────────────────


@dataclass
class Policy:
    """A single governance policy.

    Attributes:
        name: Unique identifier for this policy.
        scope: What this policy applies to (agent, tool, session).
        rule: The condition that determines when this policy fires.
        action: What to do when the rule matches.
        priority: Lower = evaluated first (default 50).
        description: Human-readable description.
        enabled: Can be toggled on/off without removing.
    """

    name: str
    scope: PolicyScope = field(default_factory=PolicyScope)
    rule: PolicyRule = field(default_factory=AlwaysRule)
    action: PolicyAction = PolicyAction.LOG
    priority: int = 50
    description: str = ""
    enabled: bool = True


# ── Policy Evaluation Result ─────────────────────────────────────────────────


@dataclass
class PolicyResult:
    """Result of evaluating all policies against a context."""

    action: PolicyAction
    matched_policies: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def allowed(self) -> bool:
        return self.action in (PolicyAction.ALLOW, PolicyAction.LOG, PolicyAction.FLAG)

    @property
    def denied(self) -> bool:
        return self.action == PolicyAction.DENY


# ── Policy Engine ────────────────────────────────────────────────────────────


class PolicyEngine:
    """Evaluates policies against hook contexts.

    Policies are evaluated in priority order (lowest number first).
    The first DENY wins. ALLOW short-circuits (skips remaining policies).
    LOG and FLAG are non-blocking — evaluation continues.

    Integration with hooks: ``activate()`` registers the engine as a
    ``pre_tool_call`` and ``pre_llm_call`` hook, so policies are enforced
    automatically whenever the hook registry fires.

    Usage::

        engine = PolicyEngine()
        engine.add_policy(Policy(...))
        engine.activate()  # Register hooks

        # Later:
        result = engine.evaluate(hook_ctx)
        if result.denied:
            print(f"Blocked by: {result.matched_policies}")
    """

    def __init__(self) -> None:
        self._policies: list[Policy] = []
        self._state = PolicyState()
        self._active = False
        self._violations: list[dict[str, Any]] = []

    # ── Policy management ─────────────────────────────────────────────────

    def add_policy(self, policy: Policy) -> None:
        """Add a policy and re-sort by priority."""
        self._policies.append(policy)
        self._policies.sort(key=lambda p: p.priority)

    def remove_policy(self, name: str) -> bool:
        """Remove a policy by name. Returns True if found."""
        before = len(self._policies)
        self._policies = [p for p in self._policies if p.name != name]
        return len(self._policies) < before

    def get_policy(self, name: str) -> Policy | None:
        """Get a policy by name."""
        for p in self._policies:
            if p.name == name:
                return p
        return None

    def list_policies(self) -> list[Policy]:
        """Return all policies (sorted by priority)."""
        return list(self._policies)

    def enable(self, name: str) -> bool:
        """Enable a policy by name."""
        p = self.get_policy(name)
        if p:
            p.enabled = True
            return True
        return False

    def disable(self, name: str) -> bool:
        """Disable a policy by name."""
        p = self.get_policy(name)
        if p:
            p.enabled = False
            return True
        return False

    @property
    def state(self) -> PolicyState:
        """Access the shared policy state."""
        return self._state

    @property
    def violations(self) -> list[dict[str, Any]]:
        """List of recorded policy violations."""
        return list(self._violations)

    # ── Evaluation ────────────────────────────────────────────────────────

    def evaluate(self, ctx: HookContext) -> PolicyResult:
        """Evaluate all matching policies against the context.

        Returns a PolicyResult with the final action and matched policy names.
        """
        matched: list[str] = []
        final_action = PolicyAction.LOG  # Default: allow (with logging)

        for policy in self._policies:
            if not policy.enabled:
                continue
            if not policy.scope.matches(ctx):
                continue

            # Special handling for circuit-breaker rules: every call is a
            # candidate violation, so we record BEFORE evaluating. This way
            # the breaker counter is consistent across calls and trips on
            # the Nth call, not requiring the rule to have fired previously.
            if isinstance(policy.rule, CircuitBreakerRule):
                policy.rule.record_violation(ctx, self._state)

            # Evaluate the rule
            if policy.rule.evaluate(ctx, self._state):
                matched.append(policy.name)
                logger.debug(
                    "Policy '%s' matched (action=%s)", policy.name, policy.action.value
                )

                if policy.action == PolicyAction.DENY:
                    # Record violation
                    self._violations.append(
                        {
                            "policy": policy.name,
                            "agent": ctx.agent_name,
                            "session": ctx.session_id,
                            "hook_type": ctx.hook_type.value,
                            "timestamp": time.time(),
                            "data_keys": list(ctx.data.keys()),
                        }
                    )
                    # Update rate-limit state even on deny
                    self._record_call(ctx)
                    return PolicyResult(
                        action=PolicyAction.DENY,
                        matched_policies=matched,
                        message=f"Blocked by policy '{policy.name}'",
                    )

                elif policy.action == PolicyAction.ALLOW:
                    # Short-circuit: allow and skip remaining
                    self._record_call(ctx)
                    return PolicyResult(
                        action=PolicyAction.ALLOW,
                        matched_policies=matched,
                        message=f"Explicitly allowed by policy '{policy.name}'",
                    )

                elif policy.action == PolicyAction.FLAG:
                    # Flag for review but continue
                    final_action = PolicyAction.FLAG
                    ctx.metadata["policy_flagged"] = True
                    ctx.metadata.setdefault("flagged_by", []).append(policy.name)

                elif policy.action == PolicyAction.LOG:
                    # Just log, continue
                    pass

        # Record the call for rate-limiting state
        self._record_call(ctx)

        return PolicyResult(
            action=final_action,
            matched_policies=matched,
            message="No blocking policy matched" if not matched else f"Matched: {matched}",
        )

    def _record_call(self, ctx: HookContext) -> None:
        """Record a call in the policy state for rate-limiting and budgeting."""
        # Rate limit tracking
        rate_key = f"rate:{ctx.agent_name}:{ctx.session_id}"
        self._state.append_list(rate_key, time.time())

        # Token tracking for LLM calls
        if ctx.hook_type == HookType.PRE_LLM_CALL:
            tokens_key = f"tokens:{ctx.session_id}"
            msg = ctx.data.get("message", "")
            estimated = max(1, len(str(msg)) // 4)
            self._state.increment(tokens_key, estimated)

    # ── Hook Integration ──────────────────────────────────────────────────

    def activate(self) -> None:
        """Register this engine as hooks on the global HookRegistry.

        Registers a pre_tool_call and pre_llm_call hook that evaluates
        all policies before each call.
        """
        if self._active:
            return

        registry = get_hook_registry()

        def _policy_hook(ctx: HookContext) -> HookContext | None:
            result = self.evaluate(ctx)
            if result.denied:
                logger.warning(
                    "Policy denied %s/%s: %s",
                    ctx.hook_type.value,
                    ctx.data.get("tool_name", ctx.agent_name),
                    result.message,
                )
                return None  # Abort the hook chain
            if result.action == PolicyAction.FLAG:
                ctx.metadata["policy_flagged"] = True
                ctx.metadata["flagged_by"] = result.matched_policies
            return ctx

        registry.register(
            HookType.PRE_TOOL_CALL,
            _policy_hook,
            name="policy_engine",
            priority=-10,  # Run before other hooks
        )
        registry.register(
            HookType.PRE_LLM_CALL,
            _policy_hook,
            name="policy_engine",
            priority=-10,
        )
        self._active = True
        logger.info(
            "PolicyEngine activated (%d policies)", len(self._policies)
        )

    def deactivate(self) -> None:
        """Unregister this engine's hooks."""
        if not self._active:
            return
        registry = get_hook_registry()
        registry.unregister("policy_engine")
        self._active = False
        logger.info("PolicyEngine deactivated")

    @property
    def is_active(self) -> bool:
        """Whether the engine is currently registered as hooks."""
        return self._active

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize the engine state to a dict."""
        return {
            "active": self._active,
            "policy_count": len(self._policies),
            "policies": [
                {
                    "name": p.name,
                    "description": p.description,
                    "action": p.action.value,
                    "priority": p.priority,
                    "enabled": p.enabled,
                    "scope": {
                        "agent": p.scope.agent,
                        "tool": p.scope.tool,
                        "session": p.scope.session,
                    },
                }
                for p in self._policies
            ],
            "violations": len(self._violations),
            "state_counters": dict(self._state._counters),
        }

    def stats(self) -> dict[str, Any]:
        """Return engine statistics."""
        return {
            "total_policies": len(self._policies),
            "enabled_policies": sum(1 for p in self._policies if p.enabled),
            "active": self._active,
            "total_violations": len(self._violations),
            "recent_violations": self._violations[-10:],
        }

    # ── YAML / dict loading ────────────────────────────────────────────────────
    #
    # Policies can be declared in YAML or JSON, then loaded into an engine.
    # Format:
    #
    #   policies:
    #     - name: odin-tool-restrict
    #       description: "Only allow safe tools for Odin"
    #       enabled: true
    #       priority: 10
    #       action: deny            # allow | deny | log | flag
    #       scope:
    #         agent: Odin
    #         tool: ""              # empty = any
    #         session: ""           # empty = any
    #       rule:
    #         type: tool_allowlist  # tool_allowlist | tool_denylist | rate_limit
    #                              # token_budget | regex | always
    #         tools: [terminal, read_file]   # rule-specific params

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyEngine:
        """Build a PolicyEngine from a dict (typically loaded from YAML).

        Recognised top-level keys:
          - ``policies``: list of policy definitions (required)
        """
        engine = cls()
        for entry in data.get("policies", []) or []:
            engine.add_policy(_policy_from_dict(entry))
        return engine

    @classmethod
    def from_yaml(cls, source: str | Path) -> PolicyEngine:
        """Build a PolicyEngine from a YAML file path or string.

        ``source`` may be a Path / str path to a .yaml file, or a YAML string
        directly (detected by the presence of a newline).
        """
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for PolicyEngine.from_yaml "
                "(install with `pip install pyyaml`)"
            ) from exc

        if isinstance(source, Path) or (
            isinstance(source, str) and "\n" not in source and Path(source).exists()
        ):
            text = Path(source).read_text(encoding="utf-8")
        else:
            text = source
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Policy YAML root must be a mapping, got {type(data).__name__}")
        return cls.from_dict(data)


# ── Serialization helpers ──────────────────────────────────────────────────────


_RULE_TYPES: dict[str, type[PolicyRule]] = {
    "tool_allowlist": ToolAllowlistRule,
    "tool_denylist": ToolDenylistRule,
    "rate_limit": RateLimitRule,
    "token_budget": TokenBudgetRule,
    "resource_limit": ResourceLimitRule,
    "circuit_breaker": CircuitBreakerRule,
    "regex": RegexRule,
    "always": AlwaysRule,
}


def _policy_from_dict(entry: dict[str, Any]) -> Policy:
    """Build a single ``Policy`` from a mapping."""
    if not isinstance(entry, dict):
        raise ValueError(f"Policy entry must be a mapping, got {type(entry).__name__}")
    if "name" not in entry:
        raise ValueError(f"Policy entry missing required key 'name': {entry}")

    scope_data = entry.get("scope", {}) or {}
    if not isinstance(scope_data, dict):
        raise ValueError(f"Policy scope must be a mapping: {scope_data}")
    rule_data = entry.get("rule", {}) or {}
    if not isinstance(rule_data, dict):
        raise ValueError(f"Policy rule must be a mapping: {rule_data}")

    # Build rule
    rule_type = str(rule_data.get("type", "always")).lower()
    rule_cls = _RULE_TYPES.get(rule_type)
    if rule_cls is None:
        raise ValueError(
            f"Unknown rule type: {rule_type!r}. "
            f"Choose from {sorted(_RULE_TYPES)}"
        )
    rule_kwargs = {k: v for k, v in rule_data.items() if k != "type"}
    rule = rule_cls(**rule_kwargs)

    # Build scope (skip empty fields)
    scope_kwargs = {
        k: v
        for k, v in scope_data.items()
        if v not in (None, "", "*", "any", "all")
    }
    scope = PolicyScope(**scope_kwargs) if scope_kwargs else PolicyScope()

    # Action
    try:
        action = PolicyAction(str(entry.get("action", "log")).lower())
    except ValueError as exc:
        raise ValueError(
            f"Unknown action {entry.get('action')!r}; "
            f"choose from {[a.value for a in PolicyAction]}"
        ) from exc

    return Policy(
        name=str(entry["name"]),
        description=str(entry.get("description", "")),
        enabled=bool(entry.get("enabled", True)),
        priority=int(entry.get("priority", 50)),
        scope=scope,
        rule=rule,
        action=action,
    )
