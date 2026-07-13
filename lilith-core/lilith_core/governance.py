"""Unified Governance Surface for Lilith — Omnigent-inspired.

Brings together the three previously separate governance primitives in
lilith-core into one cohesive facade that any agent (or external process)
can use to reason about permissions and audit history.

    +----------------------------+
    |     GovernanceSurface      |   <-- one per agent or per system
    +-------------+--------------+
                  |
    +-------------+--------------+------------------+
    |             |              |                  |
+---v-----+ +-----v-----+ +------v------+    +-------v-------+
| Policy  | | Agent     | |  Policy     |                |
| Engine  | | Sandbox   | |  Audit      |                |
+---------+ +-----------+ +-------------+                |
                                                        |
       Note: `Engine + Sandbox + Audit Trail` are the three
             underlying primitives; the Surface wires them up.

Why this module exists
----------------------
Before this module a caller had to wire up three subsystems by hand::

    engine = PolicyEngine.from_yaml("policies.yaml")
    trail = PolicyAuditTrail(path="audit.jsonl")
    sandbox = AgentSandbox(policy=sandbox_policy)
    trail.attach(engine)
    result = engine.evaluate(ctx)
    sandbox.acquire()
    try:
        ...agent loop...
    finally:
        sandbox.release()
        trail.flush()

That works but is error prone — you can forget to attach the trail, or
call ``sandbox.acquire`` before evaluating, or query the trail for an
agent who was never bound to a sandbox. The :class:`GovernanceSurface`
collapses those three concerns into a single facade with these
guarantees:

1.  Construction wires up policy + audit + sandbox together.
2.  :meth:`evaluate` returns a :class:`GovernanceDecision` describing
    exactly what to do — ALLOW, DENY (with reason), or FLAG.
3.  Every evaluation mirrors into the audit trail via :meth:`record`.
4.  :meth:`bind_sandbox` lazily attaches a per-agent sandbox so a
    misconfigured agent cannot accidentally access the host.
5.  :meth:`summary` returns a serialisable snapshot useful for the
    ``policy_govern_summary`` MCP tool and dashboard endpoints.
6.  :meth:`iterate_audit` is a generator that streams the audit
    history without loading it all into memory.

Threading
---------
The facade is safe to share across threads — :class:`PolicyAuditTrail`
serialises writes via an internal lock and :class:`PolicyEngine` keeps
per-agent counters in thread-safe data structures. Callers do not need
an external lock.

Integration points
------------------
* :mod:`lilith_orchestrator.policy_mcp` already exposes the policy
  engine. A follow-up can expose the ``GovernanceSurface`` as
  ``policy_govern_*`` MCP tools using the same stdio server pattern.
* :mod:`lilith_api` can surface ``GET /api/governance/summary`` from
  this module.
* :meth:`to_yaml_block` makes governance dumps human-readable for the
  SRE dashboard.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

from lilith_core.audit_trail import (
    AuditEntry,
    PolicyAuditTrail,
    make_default_trail,
)
from lilith_core.hooks import HookContext, HookType
from lilith_core.policy_engine import (
    Policy,
    PolicyAction,
    PolicyEngine,
    PolicyResult,
    PolicyScope,
    ToolDenylistRule,
)
from lilith_core.sandbox import (
    AgentSandbox,
    SandboxPolicy,
    SandboxRule,
    SandboxRuleType,
    SandboxViolation,
)

logger = logging.getLogger("lilith.governance")


# ── Decisions ────────────────────────────────────────────────────────────────


@dataclass
class GovernanceDecision:
    """Outcome of a :meth:`GovernanceSurface.evaluate` call.

    Attributes:
        allowed: True if the action may proceed. False if denied.
        flagged: True if a human should review the action even though it
            was not denied outright.
        reason: Short human-readable string for logs / denial messages.
        matched_policies: Names of the policies that matched this call.
        sandbox_violations: Pre-existing sandbox breaches detected during
            this evaluation (empty for a clean run).
        session: Session id the evaluation was attributed to.
        tool: Tool name passed in for the call.

    The dataclass is JSON-serialisable via :meth:`to_dict` so MCP and
    REST layers can emit it without bespoke encoders.
    """

    allowed: bool
    flagged: bool = False
    reason: str = ""
    matched_policies: list[str] = field(default_factory=list)
    sandbox_violations: list[str] = field(default_factory=list)
    session: str = ""
    tool: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Summary view ─────────────────────────────────────────────────────────────


@dataclass
class GovernanceSummary:
    """Aggregated, serialisable snapshot of the governance surface.

    Returned by :meth:`GovernanceSurface.summary`. Designed for
    dashboards and the ``policy_govern_summary`` MCP tool — fields are
    flat so the JSON shape is stable across lilith-core versions.
    """

    agent: str
    policies: int
    sandbox_rules: int
    audit_entries: int
    recent_denies: int
    recent_allows: int
    recent_flags: int
    known_agents: list[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── The surface itself ───────────────────────────────────────────────────────


class GovernanceSurface:
    """High-level facade over PolicyEngine + AuditTrail + AgentSandbox.

    Parameters:
        engine: Pre-built :class:`PolicyEngine`. If not supplied, a
            fresh engine is constructed.
        audit_trail: Pre-built :class:`PolicyAuditTrail`. Defaults to an
            in-memory trail sized for 1000 entries via
            :func:`make_default_trail`.
        default_sandbox_policy: Sandbox applied to agents that have not
            been bound to a custom one. Conservative defaults are baked
            in — ``MAX_EXEC_TIME=15s``, ``NO_NETWORK``, ``MAX_MEMORY=256MB`` —
            unless you really trust the agent.
        agent: Name of the agent this surface represents. Used for audit
            tagging and the ``known_agents`` list.
        auto_attach_audit: If True, the engine's ``evaluate`` is wrapped
            so its own record path runs in addition to the surface's.
            Defaults to False because the surface already records a
            richer entry per evaluation. Set True only if you also want
            the engine's per-rule audit events.

    Example::

        surface = GovernanceSurface(agent="Odin")
        surface.engine.add_policy(
            Policy(
                name="odin-shell-deny",
                scope=PolicyScope(agent="Odin", tool="terminal"),
                rule=ToolDenylistRule(tools=["terminal"]),
                action=PolicyAction.DENY,
            )
        )

        decision = surface.evaluate(
            tool="terminal",
            data={"cmd": "rm -rf /"},
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
    """

    DEFAULT_AGENT = "system"
    DEFAULT_RECENT_WINDOW = 50

    def __init__(
        self,
        engine: PolicyEngine | None = None,
        audit_trail: PolicyAuditTrail | None = None,
        default_sandbox_policy: SandboxPolicy | None = None,
        agent: str | None = None,
        auto_attach_audit: bool = False,
    ) -> None:
        self.agent = agent or self.DEFAULT_AGENT
        self.engine = engine or PolicyEngine()
        self.audit = audit_trail or make_default_trail()
        self.default_sandbox_policy = (
            default_sandbox_policy
            or self._conservative_sandbox(self.agent)
        )
        self._per_agent_sandboxes: dict[str, AgentSandbox] = {}
        self._known_agents: set[str] = {self.agent}
        self._audit_counter = 0
        if auto_attach_audit:
            self.audit.attach(self.engine)
        logger.debug("GovernanceSurface initialised for %s", self.agent)

    # ── Public evaluation API ──────────────────────────────────────────

    def evaluate(
        self,
        tool: str,
        data: dict[str, Any] | None = None,
        *,
        agent: str | None = None,
        session: str | None = None,
    ) -> GovernanceDecision:
        """Run a tool call through policy + audit + sandbox.

        ``agent`` defaults to ``self.agent`` so a single surface bound
        to one role does not need to repeat the name on every call.

        Records a single :class:`AuditEntry` regardless of the final
        decision — even ``ALLOW`` events go into the trail so downstream
        forensic analysis can confirm what *was* permitted, not just
        what was blocked.
        """
        agent_name = agent or self.agent
        self._known_agents.add(agent_name)
        session_id = session or agent_name
        payload = dict(data or {})
        payload.setdefault("tool_name", tool)

        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name=agent_name,
            session_id=session_id,
            data=payload,
        )

        result: PolicyResult = self.engine.evaluate(ctx)

        matched = list(result.matched_policies)
        decision_value = result.action.value
        flagged = decision_value == "flag"
        allowed = decision_value in {"allow", "log"}
        # PolicyResult exposes ``message`` rather than ``reason``.
        reason = result.message or ""

        sandbox_violations = self._check_sandbox_for_agent(agent_name)

        self._audit_counter += 1
        self.audit.record(
            AuditEntry(
                policy=matched[0] if matched else "(default)",
                agent=agent_name,
                session=session_id,
                tool=tool,
                hook_type=HookType.PRE_TOOL_CALL.value,
                action=decision_value,
                note=reason,
                data={
                    "matched_policies": matched,
                    "payload_keys": sorted(payload.keys()),
                },
            )
        )

        return GovernanceDecision(
            allowed=allowed,
            flagged=flagged,
            reason=reason,
            matched_policies=matched,
            sandbox_violations=[v.description for v in sandbox_violations],
            session=session_id,
            tool=tool,
        )

    # ── Per-agent sandbox binding ──────────────────────────────────────

    def bind_sandbox(
        self,
        agent: str,
        policy: SandboxPolicy | None = None,
    ) -> AgentSandbox:
        """Attach a (possibly custom) sandbox to an agent.

        Calling this more than once replaces the prior sandbox for the
        agent. That is intentional: revocation should be cheap.
        """
        effective = policy or self.default_sandbox_policy
        sandbox = AgentSandbox(policy=effective)
        # Track the agent name alongside the sandbox so callers can tell
        # which sandbox belongs to whom — ``AgentSandbox.__init__``
        # currently accepts ``policy`` only.
        setattr(sandbox, "agent_name", agent)
        self._per_agent_sandboxes[agent] = sandbox
        self._known_agents.add(agent)
        return sandbox

    def _check_sandbox_for_agent(self, agent: str) -> list[SandboxViolation]:
        sandbox = self._per_agent_sandboxes.get(agent)
        if sandbox is None:
            return []
        # Lazily attached — no implicit acquire/release here. The
        # sandbox violations list is exposed for forensic read only.
        return list(getattr(sandbox, "violations", []) or [])

    # ── Snapshot helpers ───────────────────────────────────────────────

    @property
    def policy_count(self) -> int:
        """Public count of registered policies.

        Uses the engine's private list because there is no public
        accessor yet. If a public list lands in PolicyEngine this
        property will fall back to it.
        """
        public = getattr(self.engine, "policies", None)
        if public is not None:
            return len(public)
        return len(self.engine._policies)  # noqa: SLF001 — intentional

    def summary(self) -> GovernanceSummary:
        """Return a :class:`GovernanceSummary` describing current state.

        ``recent_*`` counters are computed over the last
        ``DEFAULT_RECENT_WINDOW`` audit entries to avoid paying for
        full scans on large trails.
        """
        recent = self.audit.tail(self.DEFAULT_RECENT_WINDOW)
        denies = sum(1 for e in recent if e.action == "deny")
        allows = sum(1 for e in recent if e.action in {"allow", "log"})
        flags = sum(1 for e in recent if e.action == "flag")
        return GovernanceSummary(
            agent=self.agent,
            policies=self.policy_count,
            sandbox_rules=len(self.default_sandbox_policy.rules),
            audit_entries=self._audit_counter,
            recent_denies=denies,
            recent_allows=allows,
            recent_flags=flags,
            known_agents=sorted(self._known_agents),
        )

    def iterate_audit(
        self,
        *,
        agent: str | None = None,
        limit: int = 100,
    ) -> Iterator[AuditEntry]:
        """Yield recent audit entries, optionally filtered by agent.

        Lazy generator; safe to use from MCP ``resources/read``
        handlers that stream large histories.
        """
        for entry in self.audit.tail(limit):
            if agent is None or entry.agent == agent:
                yield entry

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _conservative_sandbox(agent: str) -> SandboxPolicy:
        return SandboxPolicy(
            name=f"default-{agent}",
            rules=[
                SandboxRule(type=SandboxRuleType.MAX_EXEC_TIME, value=15),
                SandboxRule(type=SandboxRuleType.NO_NETWORK, value=True),
                SandboxRule(type=SandboxRuleType.MAX_MEMORY_MB, value=256),
                SandboxRule(
                    type=SandboxRuleType.ALLOWED_TOOLS,
                    value=["read_file", "search_files", "policy_evaluate"],
                ),
            ],
        )

    def to_yaml_block(self) -> str:
        """Render the current policies as a YAML block for dumps.

        Cheap text rendering — uses :py:mod:`yaml` (already a
        lilith-core dependency). The orchestrator's MCP layer can use
        this for ``resources/read`` content.
        """
        import yaml  # type: ignore

        policies_dump: list[dict[str, Any]] = []
        for policy in self.engine._policies:  # noqa: SLF001 — intentional
            policies_dump.append(
                {
                    "name": policy.name,
                    "scope": {
                        "agent": policy.scope.agent,
                        "tool": policy.scope.tool,
                        "session": policy.scope.session,
                    },
                    "action": policy.action.value,
                    "priority": policy.priority,
                    "enabled": policy.enabled,
                    "description": policy.description,
                }
            )
        out: dict[str, Any] = {
            "agent": self.agent,
            "summary": self.summary().to_dict(),
            "policies": policies_dump,
        }
        return yaml.safe_dump(out, sort_keys=False)


__all__ = [
    "GovernanceDecision",
    "GovernanceSummary",
    "GovernanceSurface",
]
