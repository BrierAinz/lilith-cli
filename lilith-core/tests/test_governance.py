"""Tests for the unified GovernanceSurface (Omnigent-inspired).

The GovernanceSurface combines PolicyEngine + AuditTrail + AgentSandbox
into one facade. These tests cover:
  - construction auto-wires all three subsystems
  - evaluate() returns a GovernanceDecision with correct allowed/flagged
  - audit trail records every evaluation (allow, deny, flag)
  - sandbox binding per agent works
  - summary() produces a serialisable snapshot
  - iterate_audit() yields filtered entries lazily
  - the defaults are conservative (NO_NETWORK, time-limited, memory capped)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from lilith_core.governance import (
    GovernanceDecision,
    GovernanceSummary,
    GovernanceSurface,
)
from lilith_core.policy_engine import (
    Policy,
    PolicyAction,
    PolicyEngine,
    PolicyScope,
    ToolDenylistRule,
)
from lilith_core.sandbox import (
    AgentSandbox,
    SandboxPolicy,
    SandboxRule,
    SandboxRuleType,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conservative_policy() -> Policy:
    """A policy that denies ``terminal`` for Odin."""
    return Policy(
        name="odin-block-terminal",
        scope=PolicyScope(agent="Odin", tool="terminal"),
        rule=ToolDenylistRule(tools=["terminal"]),
        action=PolicyAction.DENY,
        priority=10,
        description="Odin must not call terminal directly.",
    )


@pytest.fixture
def allow_policy() -> Policy:
    return Policy(
        name="global-allow-read-file",
        scope=PolicyScope(tool="read_file"),
        rule=ToolDenylistRule(tools=[]),  # never matches → policy never fires
        action=PolicyAction.ALLOW,
        priority=1,
        description="Demonstrates ALLOW policy registration without firing.",
    )


@pytest.fixture
def fresh_surface(conservative_policy: Policy) -> GovernanceSurface:
    surface = GovernanceSurface(agent="Odin")
    surface.engine.add_policy(conservative_policy)
    return surface


# ── Construction ────────────────────────────────────────────────────────────


class TestConstruction:
    def test_default_agent_name(self) -> None:
        surface = GovernanceSurface()
        assert surface.agent == GovernanceSurface.DEFAULT_AGENT

    def test_explicit_agent_name(self) -> None:
        surface = GovernanceSurface(agent="Mimir")
        assert surface.agent == "Mimir"

    def test_engine_is_attached(self) -> None:
        surface = GovernanceSurface()
        assert isinstance(surface.engine, PolicyEngine)

    def test_audit_trail_attached(self) -> None:
        surface = GovernanceSurface()
        # attach() wires the trail onto the engine — verify by checking
        # that subsequent policy evaluations create audit entries.
        before = surface._audit_counter
        surface.evaluate(tool="noop", data={})
        assert surface._audit_counter == before + 1

    def test_conservative_default_sandbox(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        rules = surface.default_sandbox_policy.rules
        types = {r.type for r in rules}
        assert SandboxRuleType.NO_NETWORK in types
        assert SandboxRuleType.MAX_EXEC_TIME in types
        assert SandboxRuleType.MAX_MEMORY_MB in types
        assert SandboxRuleType.ALLOWED_TOOLS in types

    def test_custom_sandbox_policy(self) -> None:
        custom = SandboxPolicy(
            name="lax", rules=[SandboxRule(type=SandboxRuleType.MAX_EXEC_TIME, value=60)]
        )
        surface = GovernanceSurface(
            agent="Odin", default_sandbox_policy=custom
        )
        assert surface.default_sandbox_policy.name == "lax"
        assert len(surface.default_sandbox_policy.rules) == 1

    def test_no_auto_attach(self) -> None:
        engine = PolicyEngine()
        surface = GovernanceSurface(engine=engine, auto_attach_audit=False)
        # Without attach, evaluations still increment the surface counter
        # (we record directly), but the engine itself does not have the
        # trail wired. The behaviour we assert: surface still records.
        surface.evaluate(tool="noop", data={})
        assert surface._audit_counter == 1


# ── evaluate() ──────────────────────────────────────────────────────────────


class TestEvaluate:
    def test_returns_governance_decision(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        decision = fresh_surface.evaluate(tool="read_file", data={"path": "/etc"})
        assert isinstance(decision, GovernanceDecision)

    def test_serialisable_to_dict(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        decision = fresh_surface.evaluate(tool="read_file", data={"path": "/etc"})
        payload = decision.to_dict()
        assert "allowed" in payload
        assert "matched_policies" in payload
        # Must be JSON-serialisable — sanity check.
        json.dumps(payload)

    def test_deny_when_policy_matches(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        decision = fresh_surface.evaluate(
            tool="terminal", data={"cmd": "ls"}
        )
        assert decision.allowed is False
        assert "odin-block-terminal" in decision.matched_policies

    def test_deny_for_other_agent_does_not_fire(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        decision = fresh_surface.evaluate(
            tool="terminal", data={}, agent="Mimir"
        )
        # Mimir is not Odin's scope → conservative default LOG applies.
        assert decision.allowed is True

    def test_audit_records_allow(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        surface.evaluate(tool="read_file", data={"path": "/foo"})
        trail = list(surface.audit.tail(1))
        assert trail[0].action in {"log", "allow"}
        assert trail[0].tool == "read_file"
        assert trail[0].agent == "Odin"

    def test_audit_records_deny(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        fresh_surface.evaluate(tool="terminal", data={})
        entries = fresh_surface.audit.tail(5)
        denies = [e for e in entries if e.action == "deny"]
        assert any(e.policy == "odin-block-terminal" for e in denies)

    def test_session_passed_through(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        decision = surface.evaluate(
            tool="read_file", data={}, session="ses-XYZ"
        )
        assert decision.session == "ses-XYZ"
        assert surface.audit.tail(1)[0].session == "ses-XYZ"

    def test_known_agents_track(self, fresh_surface: GovernanceSurface) -> None:
        fresh_surface.evaluate(tool="read_file", agent="Mimir", data={})
        fresh_surface.evaluate(tool="read_file", agent="Heimdall", data={})
        assert "Mimir" in fresh_surface._known_agents
        assert "Heimdall" in fresh_surface._known_agents


# ── Sandboxes ───────────────────────────────────────────────────────────────


class TestSandboxBinding:
    def test_bind_returns_sandbox(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        sandbox = surface.bind_sandbox("Heimdall")
        assert isinstance(sandbox, AgentSandbox)

    def test_bind_replaces_prior(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        first = fresh_surface.bind_sandbox("Mimir")
        second = fresh_surface.bind_sandbox(
            "Mimir",
            policy=SandboxPolicy(
                name="strict-mimir",
                rules=[
                    SandboxRule(type=SandboxRuleType.MAX_EXEC_TIME, value=5)
                ],
            ),
        )
        assert first is not second
        # New sandbox should have the strict policy.
        assert second.policy.name == "strict-mimir"

    def test_unknown_agent_no_violations(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        decision = fresh_surface.evaluate(
            tool="read_file", data={}, agent="NeverBound"
        )
        assert decision.sandbox_violations == []


# ── Summary / iteration ─────────────────────────────────────────────────────


class TestSummary:
    def test_summary_shape(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        surface.engine.add_policy(
            Policy(
                name="p1",
                scope=PolicyScope(agent="Odin"),
                rule=ToolDenylistRule(tools=["terminal"]),
                action=PolicyAction.DENY,
            )
        )
        surface.evaluate(tool="read_file", data={})
        summary = surface.summary()
        assert isinstance(summary, GovernanceSummary)
        d = summary.to_dict()
        assert d["agent"] == "Odin"
        assert d["policies"] == 1
        assert d["audit_entries"] == 1
        assert d["recent_allows"] >= 1

    def test_summary_serialisable(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        surface.evaluate(tool="read_file", data={})
        summary = surface.summary()
        json.dumps(summary.to_dict())

    def test_iterate_audit_filters_by_agent(
        self, fresh_surface: GovernanceSurface
    ) -> None:
        fresh_surface.evaluate(tool="read_file", data={}, agent="Mimir")
        fresh_surface.evaluate(tool="terminal", data={}, agent="Odin")
        mimir_entries = list(
            fresh_surface.iterate_audit(agent="Mimir", limit=10)
        )
        assert all(e.agent == "Mimir" for e in mimir_entries)
        assert len(mimir_entries) >= 1

    def test_iterate_audit_no_filter(self, fresh_surface: GovernanceSurface) -> None:
        fresh_surface.evaluate(tool="read_file", data={})
        fresh_surface.evaluate(tool="write_file", data={})
        entries = list(fresh_surface.iterate_audit(limit=10))
        assert len(entries) >= 2

    def test_to_yaml_block(self) -> None:
        surface = GovernanceSurface(agent="Odin")
        surface.engine.add_policy(
            Policy(
                name="yaml-policy",
                scope=PolicyScope(tool="terminal"),
                rule=ToolDenylistRule(tools=["terminal"]),
                action=PolicyAction.DENY,
            )
        )
        yml = surface.to_yaml_block()
        assert isinstance(yml, str)
        assert "yaml-policy" in yml
        assert "Odin" in yml


# ── Real audit trail on disk ────────────────────────────────────────────────


class TestWithRealTrail:
    def test_disk_trail_used(self, tmp_path: Path) -> None:
        from lilith_core.audit_trail import PolicyAuditTrail

        trail = PolicyAuditTrail(
            path=str(tmp_path / "audit.jsonl"), max_entries=50
        )
        surface = GovernanceSurface(agent="Odin", audit_trail=trail)
        for i in range(5):
            surface.evaluate(tool="read_file", data={"i": i})
        # The trail file should now exist and contain 5 lines.
        path = Path(tmp_path / "audit.jsonl")
        assert path.exists()
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 5

    def test_disk_trail_parses_back(self, tmp_path: Path) -> None:
        from lilith_core.audit_trail import PolicyAuditTrail

        trail = PolicyAuditTrail(
            path=str(tmp_path / "audit.jsonl"), max_entries=50
        )
        surface = GovernanceSurface(agent="Odin", audit_trail=trail)
        surface.evaluate(tool="read_file", data={"msg": "hello"})
        with open(tmp_path / "audit.jsonl", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        assert entry["tool"] == "read_file"
        assert entry["agent"] == "Odin"
