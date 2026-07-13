"""Workflow Package Validator — semantic checks beyond basic ``validate()``.

A :class:`WorkflowDefinition`'s built-in ``validate()`` checks structural
correctness (names, retries, timeouts, gate agent). This module adds the
*semantic* layer:

  - **R01: Unique step names** (also covered by base — duplicate here
    produces a clearer error location).
  - **R02: Known agents** — every ``step.agent`` is in the agent-card set.
  - **R03: Output keys unique** — no two steps write to the same
    ``output_key`` (otherwise last-writer-wins silently).
  - **R04: Input keys reference real outputs** — every
    ``step.input_key`` (or each ``input_keys`` entry) matches a real
    ``output_key`` of an earlier step, OR is declared in
    ``workflow.variables``.
  - **R05: No cycles in input chain** — when a graph of
    ``output_key -> input_key`` dependency is built, it must be acyclic.
  - **R06: Gate consistency** — every gate's referenced
    ``reviewer_agent`` exists in the agent set.
  - **R07: Unused variables** — variables defined in
    ``workflow.variables`` but never consumed by any step.
  - **R08: Orphan output keys** — steps that write to ``output_key`` but
    no later step reads from it (informational, NOT a hard error:
    workflow outputs may be intentionally terminal).

All checks return a list of :class:`ValidationIssue` objects. The
top-level :func:`validate_package` (and ``validate_definition``) return a
:class:`ValidationReport` with severity counts.

Inspired by the validation gap surfaced in cycle 9 — prior
``validate()`` was shallow, no semantic checks for cross-step data flow.

Example::

    from lilith_orchestrator.workflow import WorkflowDefinition
    from lilith_orchestrator.workflow_validator import validate_definition

    workflow = WorkflowDefinition(...)
    report = validate_definition(workflow, known_agents={"odin", "mimir"})
    if not report.ok:
        for issue in report.issues:
            print(issue.severity, issue.code, issue.message)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from lilith_orchestrator.workflow import (
    GateType,
    QualityGate,
    WorkflowDefinition,
    WorkflowStep,
)
from lilith_orchestrator.workflow_packages import WorkflowPackage

__all__ = [
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "rule_unique_step_names",
    "rule_known_agents",
    "rule_unique_output_keys",
    "rule_input_keys_resolvable",
    "rule_no_cycles_in_input_chain",
    "rule_gate_consistency",
    "rule_unused_variables",
    "rule_orphan_output_keys",
    "ALL_RULES",
    "validate_definition",
    "validate_package",
]


# ── Enums & DTOs ─────────────────────────────────────────────────────────────


class Severity:
    """Issue severity (string constants — dataclass-friendly)."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """A single validation finding.

    Attributes:
        code: Stable rule identifier (e.g. ``"R03"``).
        severity: One of :class:`Severity` constants.
        message: Human-readable description.
        step_name: Optional step the issue refers to.
        field: Optional field/key name (``"input_key"``, ``"gate.reviewer_agent"``...).
    """

    code: str
    severity: str
    message: str
    step_name: str | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.step_name:
            out["step_name"] = self.step_name
        if self.field:
            out["field"] = self.field
        return out


@dataclass
class ValidationReport:
    """Aggregate result of running a set of validation rules.

    Attributes:
        issues: All issues found (across all rules), in rule-execution order.
        workflow_name: Name of the validated workflow (informational).
    """

    issues: list[ValidationIssue] = field(default_factory=list)
    workflow_name: str = ""

    @property
    def ok(self) -> bool:
        """``True`` if no ERROR-severity issues were found."""
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.INFO]

    @property
    def counts(self) -> dict[str, int]:
        """Return ``{severity -> count}`` for quick display."""
        c: dict[str, int] = defaultdict(int)
        for i in self.issues:
            c[i.severity] += 1
        return dict(c)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "ok": self.ok,
            "counts": self.counts,
            "issues": [i.to_dict() for i in self.issues],
        }

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)


# ── Rule helpers ─────────────────────────────────────────────────────────────


def _all_input_keys(step: WorkflowStep) -> list[str]:
    """Return every input key a step reads from.

    WorkflowStep only carries a single ``input_key`` field in the current
    schema, but we also accept ``variables_used`` for future-proofing.
    """
    keys: list[str] = []
    if getattr(step, "input_key", None):
        keys.append(step.input_key)
    extras = getattr(step, "variables_used", None)
    if isinstance(extras, (list, tuple)):
        keys.extend(str(k) for k in extras if k)
    return keys


def _index_steps(workflow: WorkflowDefinition) -> dict[str, WorkflowStep]:
    return {s.name: s for s in workflow.steps if s.name}


# ── Individual rules ─────────────────────────────────────────────────────────


def rule_unique_step_names(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,  # noqa: ARG001 — unified signature
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R01: every step has a unique non-empty name."""
    seen: dict[str, int] = {}
    issues: list[ValidationIssue] = []
    for i, step in enumerate(workflow.steps):
        if not step.name:
            issues.append(
                ValidationIssue(
                    code="R01",
                    severity=Severity.ERROR,
                    message=f"Step at index {i} has empty name",
                    step_name=None,
                    field="name",
                )
            )
            continue
        if step.name in seen:
            issues.append(
                ValidationIssue(
                    code="R01",
                    severity=Severity.ERROR,
                    message=(
                        f"Duplicate step name '{step.name}' "
                        f"(first at index {seen[step.name]})"
                    ),
                    step_name=step.name,
                    field="name",
                )
            )
        else:
            seen[step.name] = i
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_known_agents(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R02: every ``step.agent`` is in the allowed agent set (if provided).

    If ``known_agents`` is ``None``, no check is performed (the validator
    is in "unrestricted" mode — useful for ad-hoc validation when no
    agent card catalog is loaded).
    """
    if known_agents is None:
        return []
    issues: list[ValidationIssue] = []
    lowered = {a.lower() for a in known_agents}
    for step in workflow.steps:
        if not step.agent:
            issues.append(
                ValidationIssue(
                    code="R02",
                    severity=Severity.ERROR,
                    message=f"Step '{step.name}' has no agent assigned",
                    step_name=step.name,
                    field="agent",
                )
            )
            continue
        if step.agent.lower() not in lowered:
            issues.append(
                ValidationIssue(
                    code="R02",
                    severity=Severity.ERROR,
                    message=(
                        f"Step '{step.name}' uses unknown agent "
                        f"'{step.agent}' (allowed: {sorted(lowered)})"
                    ),
                    step_name=step.name,
                    field="agent",
                )
            )
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_unique_output_keys(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,  # noqa: ARG001
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R03: every step's ``output_key`` is unique."""
    issues: list[ValidationIssue] = []
    seen: dict[str, str] = {}
    for step in workflow.steps:
        if not step.output_key:
            continue
        if step.output_key in seen:
            issues.append(
                ValidationIssue(
                    code="R03",
                    severity=Severity.ERROR,
                    message=(
                        f"Output key '{step.output_key}' is written by both "
                        f"'{seen[step.output_key]}' and '{step.name}' "
                        f"(last writer wins)"
                    ),
                    step_name=step.name,
                    field="output_key",
                )
            )
        else:
            seen[step.output_key] = step.name
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_input_keys_resolvable(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,  # noqa: ARG001
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R04: every input_key resolves to a workflow variable or earlier output_key."""
    issues: list[ValidationIssue] = []
    declared = set((workflow.variables or {}).keys())
    outputs_by_step: dict[str, set[str]] = defaultdict(set)
    for step in workflow.steps:
        outputs_by_step[step.name].add(step.output_key)
    # Make outputs available *up to and including* each step (each step
    # may read its own declared output if engine runs self-loop).
    cumulative: set[str] = set(declared)
    seen_steps: set[str] = set()
    for step in workflow.steps:
        for key in _all_input_keys(step):
            if not key:
                continue
            if key in cumulative:
                continue
            # Maybe the key is a forward reference (a step later declares
            # it) — only flag if at the END no one declared it either.
            declared_everywhere = any(
                step.output_key == key for step in workflow.steps
            ) or key in declared
            if declared_everywhere and key not in cumulative:
                issues.append(
                    ValidationIssue(
                        code="R04",
                        severity=Severity.WARNING,
                        message=(
                            f"Step '{step.name}' reads input_key '{key}' "
                            f"declared by a later step (forward reference)"
                        ),
                        step_name=step.name,
                        field="input_key",
                    )
                )
            else:
                issues.append(
                    ValidationIssue(
                        code="R04",
                        severity=Severity.ERROR,
                        message=(
                            f"Step '{step.name}' references undeclared "
                            f"input_key '{key}' (not in variables, not "
                            f"written by any step)"
                        ),
                        step_name=step.name,
                        field="input_key",
                    )
                )
        cumulative.add(step.output_key)
        seen_steps.add(step.name)
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_no_cycles_in_input_chain(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,  # noqa: ARG001
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R05: dependency graph (output_key -> consumers) must be acyclic.

    Builds a directed graph where an edge ``A -> B`` means step A's
    output is read by step B (via input_key). If a step reads its own
    output, that's a self-loop and a cycle.
    """
    issues: list[ValidationIssue] = []
    # Map output_key -> step name that produces it
    producer: dict[str, str] = {}
    for step in workflow.steps:
        if step.output_key:
            producer[step.output_key] = step.name
    # Build adjacency: step_name -> set of step_names it depends on (upstream)
    deps: dict[str, set[str]] = defaultdict(set)
    for step in workflow.steps:
        for key in _all_input_keys(step):
            upstream = producer.get(key)
            if upstream:
                deps[step.name].add(upstream)
    # Detect cycles with iterative DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {s.name: WHITE for s in workflow.steps}

    def visit(node: str, stack: list[str]) -> bool:
        color[node] = GRAY
        stack.append(node)
        for upstream in deps.get(node, ()):
            if upstream not in color:
                continue
            if color[upstream] == GRAY:
                cycle_start = stack.index(upstream)
                cycle = stack[cycle_start:] + [upstream]
                issues.append(
                    ValidationIssue(
                        code="R05",
                        severity=Severity.ERROR,
                        message=(
                            "Dependency cycle detected: "
                            + " -> ".join(cycle)
                        ),
                        step_name=node,
                        field="input_key",
                    )
                )
                stack.pop()
                color[node] = BLACK
                return True
            if color[upstream] == WHITE:
                if visit(upstream, stack):
                    stack.pop()
                    color[node] = BLACK
                    return True
        stack.pop()
        color[node] = BLACK
        return False

    for step in workflow.steps:
        if color[step.name] == WHITE:
            visit(step.name, [])

    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_gate_consistency(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R06: every gate references a known reviewer agent (when one is needed)."""
    issues: list[ValidationIssue] = []
    lowered = {a.lower() for a in known_agents} if known_agents else None
    for step in workflow.steps:
        gate: QualityGate | None = step.gate
        if not gate or gate.type == GateType.NONE:
            continue
        if gate.type == GateType.AGENT_REVIEW and not gate.reviewer_agent:
            issues.append(
                ValidationIssue(
                    code="R06",
                    severity=Severity.ERROR,
                    message=(
                        f"Step '{step.name}' uses AGENT_REVIEW gate but "
                        f"no reviewer_agent is set"
                    ),
                    step_name=step.name,
                    field="gate.reviewer_agent",
                )
            )
        if lowered is not None and gate.reviewer_agent:
            if gate.reviewer_agent.lower() not in lowered:
                issues.append(
                    ValidationIssue(
                        code="R06",
                        severity=Severity.ERROR,
                        message=(
                            f"Step '{step.name}' gate reviewer_agent "
                            f"'{gate.reviewer_agent}' is not in the known "
                            f"agent set"
                        ),
                        step_name=step.name,
                        field="gate.reviewer_agent",
                    )
                )
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_unused_variables(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,  # noqa: ARG001
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R07: variables declared in ``workflow.variables`` but never consumed."""
    issues: list[ValidationIssue] = []
    if not workflow.variables:
        if report is not None:
            for i in issues:
                report.add(i)
        return issues
    referenced: set[str] = set()
    for step in workflow.steps:
        for key in _all_input_keys(step):
            referenced.add(key)
    # Variables are usually referenced as ``{{ var.name }}`` in
    # intents/descriptions, but we can't parse Jinja cheaply. Best
    # approximation: check if var name appears as a literal in any
    # step's intent/description.
    var_names = set(workflow.variables.keys())
    for step in workflow.steps:
        haystack = " ".join(
            [step.intent or "", step.description or "", step.input_key or ""]
        )
        for var in var_names:
            if var in haystack or f"{{{{ {var} }}}}" in haystack:
                referenced.add(var)
    for var in sorted(var_names - referenced):
        issues.append(
            ValidationIssue(
                code="R07",
                severity=Severity.WARNING,
                message=(
                    f"Variable '{var}' is declared in workflow.variables "
                    f"but never referenced by any step"
                ),
                step_name=None,
                field=f"variables.{var}",
            )
        )
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


def rule_orphan_output_keys(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None = None,  # noqa: ARG001
    report: ValidationReport | None = None,
) -> list[ValidationIssue]:
    """R08: output_keys that no later step reads (informational).

    Workflows often *intentionally* have a terminal output (returned to
    the caller), so this is an INFO severity — surfaces candidates for
    cleanup but does not block.
    """
    issues: list[ValidationIssue] = []
    outputs = {s.output_key for s in workflow.steps if s.output_key}
    reads: set[str] = set()
    for step in workflow.steps:
        reads.update(_all_input_keys(step))
    for output in sorted(outputs - reads):
        producer = next(
            (s.name for s in workflow.steps if s.output_key == output),
            None,
        )
        issues.append(
            ValidationIssue(
                code="R08",
                severity=Severity.INFO,
                message=(
                    f"Output key '{output}' (produced by "
                    f"'{producer}') is never consumed — terminal output"
                ),
                step_name=producer,
                field="output_key",
            )
        )
    if report is not None:
        for i in issues:
            report.add(i)
    return issues


ALL_RULES: list = [
    rule_unique_step_names,
    rule_known_agents,
    rule_unique_output_keys,
    rule_input_keys_resolvable,
    rule_no_cycles_in_input_chain,
    rule_gate_consistency,
    rule_unused_variables,
    rule_orphan_output_keys,
]


def _run_all(
    workflow: WorkflowDefinition,
    known_agents: set[str] | None,
) -> ValidationReport:
    report = ValidationReport(workflow_name=workflow.name)
    for rule in ALL_RULES:
        rule(workflow, known_agents, report=report)
    return report


def validate_definition(
    workflow: WorkflowDefinition,
    known_agents: Iterable[str] | None = None,
    rules: Iterable[Any] | None = None,
) -> ValidationReport:
    """Run the full semantic validation suite against a :class:`WorkflowDefinition`.

    Args:
        workflow: The workflow to validate.
        known_agents: Optional iterable of allowed agent IDs. When
            provided, the validator checks that every ``step.agent`` and
            ``gate.reviewer_agent`` is in the set (case-insensitive).
            Pass ``None`` to skip agent-identity checks.
        rules: Optional subset of validation rules to run. Defaults to
            :data:`ALL_RULES`.

    Returns:
        A :class:`ValidationReport` aggregating all issues.
    """
    agents_set = {str(a) for a in known_agents} if known_agents is not None else None
    report = ValidationReport(workflow_name=workflow.name)
    selected = list(rules) if rules is not None else ALL_RULES
    for rule in selected:
        rule(workflow, agents_set, report=report)
    return report


def validate_package(
    package: WorkflowPackage,
    known_agents: Iterable[str] | None = None,
    rules: Iterable[Any] | None = None,
) -> ValidationReport:
    """Validate a :class:`WorkflowPackage` (same as ``validate_definition``).

    Convenience wrapper that uses ``package.workflow``.
    """
    return validate_definition(package.workflow, known_agents=known_agents, rules=rules)
