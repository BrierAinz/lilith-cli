"""Scenario-driven test harness for the Policy Engine.

Inspired by ``schema-driven-insight-agent``'s ``cmd/eval`` deterministic gate
and ``omnigent``'s policy validation tooling. Yggdrasil governance should
not depend on a live agent to be tested: ship a YAML scenario file with
inputs and expectations, run it through the engine, get a pass/fail report.

Why this matters:

  * Policies are security-relevant. A regression in a single rule is a
    serious bug. We want *deterministic* smoke tests.
  * Compliance workflows require reproducible proof. "Did we run the deny-
    shell test in CI?" → file the harness output in the audit folder.
  * The audit trail tells you what *happened*. The harness tells you
    what *should* happen. Together they close the loop.

Scenario file shape (YAML or JSON)::

    scenarios:
      - name: dangerous-shell-blocked
        agent: Odin
        session: prod-001
        hook_type: pre_tool_call
        tool: shell_exec
        data:
          cmd: "rm -rf /"
        expect:
          decision: deny
          matched_policies: [deny-dangerous-shell]

      - name: safe-read-allowed
        agent: Mimir
        hook_type: pre_tool_call
        tool: read_file
        data:
          path: "/etc/hostname"
        expect:
          decision: allow

Programmatic usage::

    from lilith_core.policy_harness import PolicyHarness, Scenario

    engine = PolicyEngine.from_yaml("policies.yaml")
    harness = PolicyHarness(engine)
    harness.add_scenario(Scenario(name="...", tool="...", expect="deny"))
    report = harness.run()
    print(report.summary())

The harness is fully deterministic: no IO, no LLM calls, no clock reads
beyond what the engine itself does internally. CI-friendly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from lilith_core.hooks import HookContext, HookType
from lilith_core.policy_engine import (
    PolicyAction,
    PolicyEngine,
    PolicyResult,
)

logger = logging.getLogger("lilith.policy_harness")


# ── Decision ──────────────────────────────────────────────────────────────


class Decision(str, Enum):
    """Expected decision for a scenario."""

    ALLOW = "allow"
    DENY = "deny"
    LOG = "log"
    FLAG = "flag"
    ANY = "any"  # Don't assert — just record what happened

    @classmethod
    def from_action(cls, action: PolicyAction) -> Decision:
        return {
            PolicyAction.ALLOW: cls.ALLOW,
            PolicyAction.DENY: cls.DENY,
            PolicyAction.LOG: cls.LOG,
            PolicyAction.FLAG: cls.FLAG,
        }.get(action, cls.LOG)

    def matches(self, action: PolicyAction) -> bool:
        if self is Decision.ANY:
            return True
        actual = Decision.from_action(action)
        return actual is self


# ── Scenario / Expectation ────────────────────────────────────────────────


@dataclass
class Expectation:
    """What a scenario expects the engine to produce."""

    decision: Decision = Decision.ANY
    matched_policies: list[str] = field(default_factory=list)
    """Optional: a subset of policy names that must match.

    An empty list means "don't check". Otherwise, every named policy must
    appear in ``result.matched_policies`` (subset match, not exact).
    """

    def matches_result(self, result: PolicyResult) -> tuple[bool, str]:
        """Return (ok, reason)."""
        if not self.decision.matches(result.action):
            return (
                False,
                f"expected decision={self.decision.value}, "
                f"got action={result.action.value}",
            )
        if self.matched_policies:
            missing = [
                p for p in self.matched_policies if p not in result.matched_policies
            ]
            if missing:
                return (
                    False,
                    f"expected policies {missing} in matched_policies, "
                    f"got {result.matched_policies}",
                )
        return (True, "")


@dataclass
class Scenario:
    """A single test scenario — a synthetic HookContext + expectation.

    The ``expect`` field can be either a ``Decision`` (only the action is
    checked) or a full ``Expectation`` (action + matched_policies subset).
    Any other value falls back to ``Decision.ANY`` (do not assert).
    """

    name: str
    agent: str = "test-agent"
    session: str = "test-session"
    hook_type: str = "pre_tool_call"
    tool: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    expect: Any = field(default_factory=lambda: Expectation())
    description: str = ""

    def _normalized_expectation(self) -> Expectation:
        exp = self.expect
        if isinstance(exp, Expectation):
            return exp
        if isinstance(exp, Decision):
            return Expectation(decision=exp)
        return Expectation()

    def to_context(self) -> HookContext:
        """Build a HookContext the engine can evaluate."""
        try:
            ht = HookType(self.hook_type)
        except ValueError:
            ht = HookType.PRE_TOOL_CALL
        ctx_data = dict(self.data)
        if self.tool and "tool_name" not in ctx_data:
            ctx_data["tool_name"] = self.tool
        return HookContext(
            agent_name=self.agent,
            session_id=self.session,
            hook_type=ht,
            data=ctx_data,
        )

    @classmethod
    def from_dict(cls, entry: dict[str, Any]) -> Scenario:
        expect_raw = entry.get("expect", {}) or {}
        if isinstance(expect_raw, str):
            expect_raw = {"decision": expect_raw}
        try:
            decision = Decision(expect_raw.get("decision", "any"))
        except ValueError:
            decision = Decision.ANY
        expect = Expectation(
            decision=decision,
            matched_policies=list(expect_raw.get("matched_policies", []) or []),
        )
        return cls(
            name=str(entry["name"]),
            agent=str(entry.get("agent", "test-agent")),
            session=str(entry.get("session", "test-session")),
            hook_type=str(entry.get("hook_type", "pre_tool_call")),
            tool=str(entry.get("tool", "") or ""),
            data=dict(entry.get("data", {}) or {}),
            expect=expect,
            description=str(entry.get("description", "") or ""),
        )


# ── Run result / report ──────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    """Per-scenario outcome."""

    name: str
    ok: bool
    reason: str = ""
    decision: str = ""
    matched: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class HarnessReport:
    """Aggregate run report."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0 = all passed, 1 = at least one failure."""
        return 0 if self.failed == 0 and self.errors == 0 else 1

    def summary(self) -> str:
        head = (
            f"[POLICY HARNESS] {self.passed}/{self.total} passed "
            f"({self.failed} failed, {self.errors} errors)"
        )
        if not self.results:
            return head
        head += "\n"
        for r in self.results:
            mark = "✓" if r.ok else "✗"
            line = f"  {mark} {r.name}"
            if r.decision:
                line += f" → {r.decision}"
            if not r.ok and r.reason:
                line += f"  ({r.reason})"
            head += line + "\n"
        return head.rstrip("\n")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Harness ──────────────────────────────────────────────────────────────


class PolicyHarness:
    """Deterministic scenario runner for a PolicyEngine.

    Parameters:
        engine: The PolicyEngine under test.
        on_failure: Optional callback fired for each failed scenario
            (receives a ScenarioResult). Useful for CI hooks.

    Adding scenarios::

        harness = PolicyHarness(engine)
        harness.add_scenario(Scenario(name="...", expect=Decision.DENY))
        harness.load_yaml_file("policy_tests.yaml")
        report = harness.run()

    Or run a one-shot with a path to a YAML file::

        report = PolicyHarness.from_yaml(engine, "policy_tests.yaml").run()
    """

    def __init__(
        self,
        engine: PolicyEngine,
        on_failure: Any = None,
    ) -> None:
        self.engine = engine
        self._scenarios: list[Scenario] = []
        self._on_failure = on_failure

    # ── Loading ───────────────────────────────────────────────────────

    def add_scenario(self, scenario: Scenario) -> None:
        self._scenarios.append(scenario)

    def extend(self, scenarios: Iterable[Scenario]) -> None:
        for s in scenarios:
            self.add_scenario(s)

    def load_dict(self, data: dict[str, Any]) -> int:
        raw = data.get("scenarios", []) if isinstance(data, dict) else []
        for entry in raw:
            self.add_scenario(Scenario.from_dict(entry))
        return len(raw)

    def load_yaml_file(self, path: str | Path) -> int:
        """Load scenarios from a YAML file. Requires PyYAML at call time."""
        import yaml  # local import keeps the test harness optional-dep-free

        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        return self.load_dict(data)

    def load_json_file(self, path: str | Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.load_dict(data)

    # ── Running ───────────────────────────────────────────────────────

    def run(self) -> HarnessReport:
        report = HarnessReport()
        report.total = len(self._scenarios)
        for scenario in self._scenarios:
            try:
                ctx = scenario.to_context()
                result = self.engine.evaluate(ctx)
                ok, reason = scenario._normalized_expectation().matches_result(result)
                entry = ScenarioResult(
                    name=scenario.name,
                    ok=ok,
                    reason=reason,
                    decision=result.action.value,
                    matched=list(result.matched_policies),
                    description=scenario.description,
                )
                if ok:
                    report.passed += 1
                else:
                    report.failed += 1
                    if self._on_failure is not None:
                        try:
                            self._on_failure(scenario, entry)
                        except Exception as exc:  # pragma: no cover
                            logger.warning("on_failure callback raised: %s", exc)
            except Exception as exc:  # pragma: no cover - defensive
                report.errors += 1
                entry = ScenarioResult(
                    name=scenario.name,
                    ok=False,
                    reason=f"harness error: {exc}",
                )
            report.results.append(entry)
        return report

    # ── Convenience constructors ──────────────────────────────────────

    @classmethod
    def from_yaml(
        cls,
        engine: PolicyEngine,
        path: str | Path,
        on_failure: Any = None,
    ) -> PolicyHarness:
        h = cls(engine, on_failure=on_failure)
        h.load_yaml_file(path)
        return h

    @classmethod
    def from_json(
        cls,
        engine: PolicyEngine,
        path: str | Path,
        on_failure: Any = None,
    ) -> PolicyHarness:
        h = cls(engine, on_failure=on_failure)
        h.load_json_file(path)
        return h


# ── Helpers ──────────────────────────────────────────────────────────────


def to_jsonl(report: HarnessReport) -> str:
    """Serialize a HarnessReport as JSON lines — useful for audit archives."""
    lines = []
    head = {
        "kind": "policy_harness_report",
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "errors": report.errors,
        "exit_code": report.exit_code,
    }
    lines.append(json.dumps(head, ensure_ascii=False))
    for r in report.results:
        lines.append(json.dumps({"kind": "scenario_result", **asdict(r)}, ensure_ascii=False))
    return "\n".join(lines) + "\n"
