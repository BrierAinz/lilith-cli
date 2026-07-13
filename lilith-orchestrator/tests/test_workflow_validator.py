"""Tests for lilith_orchestrator.workflow_validator.

Covers the 8 semantic rules (R01-R08) + the ValidationReport/Issue DTOs
+ the validate_definition / validate_package entry points.
"""

from __future__ import annotations

import pytest

from lilith_orchestrator.workflow import (
    GateType,
    QualityGate,
    WorkflowDefinition,
    WorkflowStep,
)
from lilith_orchestrator.workflow_packages import (
    WorkflowPackage,
    WorkflowPackageManifest,
)
from lilith_orchestrator.workflow_validator import (
    ALL_RULES,
    Severity,
    ValidationIssue,
    ValidationReport,
    rule_gate_consistency,
    rule_input_keys_resolvable,
    rule_known_agents,
    rule_no_cycles_in_input_chain,
    rule_orphan_output_keys,
    rule_unique_output_keys,
    rule_unique_step_names,
    rule_unused_variables,
    validate_definition,
    validate_package,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _step(name: str, agent: str = "odin", **kwargs) -> WorkflowStep:
    """Helper to build a WorkflowStep with sane defaults."""
    defaults = dict(
        intent=f"Run {name}",
        description=f"Does {name}",
        tools=[],
        retry=0,
        timeout=60,
        parallel=False,
        input_key="",
        output_key="",
        gate=QualityGate(type=GateType.NONE),
    )
    defaults.update(kwargs)
    return WorkflowStep(name=name, agent=agent, **defaults)


def _wf(steps, name="test-wf", variables=None) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        description="",
        version="1.0",
        steps=list(steps),
        variables=variables or {},
        metadata={},
    )


# ── DTOs ────────────────────────────────────────────────────────────────────


def test_validation_issue_to_dict():
    issue = ValidationIssue(
        code="R03", severity=Severity.ERROR, message="dup", step_name="s1"
    )
    out = issue.to_dict()
    assert out == {"code": "R03", "severity": "error", "message": "dup", "step_name": "s1"}


def test_validation_issue_to_dict_minimal():
    issue = ValidationIssue(code="R01", severity=Severity.INFO, message="hi")
    out = issue.to_dict()
    # step_name and field are absent when None — keep payload compact
    assert out == {"code": "R01", "severity": "info", "message": "hi"}


def test_validation_report_empty_is_ok():
    r = ValidationReport()
    assert r.ok is True
    assert r.errors == []
    assert r.warnings == []
    assert r.infos == []
    assert r.counts == {}


def test_validation_report_severity_partition():
    r = ValidationReport()
    r.add(ValidationIssue("R01", Severity.ERROR, "e"))
    r.add(ValidationIssue("R02", Severity.WARNING, "w"))
    r.add(ValidationIssue("R03", Severity.INFO, "i"))
    r.add(ValidationIssue("R04", Severity.INFO, "i2"))
    assert r.ok is False
    assert len(r.errors) == 1
    assert len(r.warnings) == 1
    assert len(r.infos) == 2
    assert r.counts == {"error": 1, "warning": 1, "info": 2}


def test_validation_report_to_dict_shape():
    r = ValidationReport(workflow_name="abc")
    r.add(ValidationIssue("R03", Severity.ERROR, "dup", step_name="s", field="output_key"))
    out = r.to_dict()
    assert out["workflow_name"] == "abc"
    assert out["ok"] is False
    assert out["counts"] == {"error": 1}
    assert out["issues"][0]["step_name"] == "s"
    assert out["issues"][0]["field"] == "output_key"


# ── R01: unique step names ──────────────────────────────────────────────────


def test_r01_ok_when_all_names_unique():
    wf = _wf([_step("a"), _step("b"), _step("c")])
    assert rule_unique_step_names(wf) == []


def test_r01_flags_duplicate():
    wf = _wf([_step("a"), _step("b"), _step("a")])
    issues = rule_unique_step_names(wf)
    assert len(issues) == 1
    assert issues[0].code == "R01"
    assert issues[0].severity == Severity.ERROR
    assert "Duplicate step name 'a'" in issues[0].message
    assert issues[0].step_name == "a"


def test_r01_flags_empty_name():
    wf = _wf([_step("a"), _step("")])
    issues = rule_unique_step_names(wf)
    assert len(issues) == 1
    assert "empty name" in issues[0].message


def test_r01_no_known_agents_required():
    """R01 ignores ``known_agents`` — must work without it."""
    wf = _wf([_step("a", agent="nope"), _step("b", agent="still-nope")])
    assert rule_unique_step_names(wf) == []


# ── R02: known agents ───────────────────────────────────────────────────────


def test_r02_no_op_when_known_agents_none():
    wf = _wf([_step("a", agent="ghost"), _step("b", agent="phantom")])
    # No agent whitelist → no issue raised
    assert rule_known_agents(wf, known_agents=None) == []


def test_r02_flags_unknown_agent_case_insensitive():
    wf = _wf([_step("a", agent="Loki"), _step("b", agent="thor")])
    known = {"odin", "thor"}
    issues = rule_known_agents(wf, known_agents=known)
    assert len(issues) == 1
    assert "Loki" in issues[0].message
    assert "odin" in issues[0].message  # listed in allowed


def test_r02_flags_missing_agent():
    wf = _wf([_step("a", agent="")])
    known = {"odin"}
    issues = rule_known_agents(wf, known_agents=known)
    assert len(issues) == 1
    assert issues[0].code == "R02"
    assert "no agent assigned" in issues[0].message


def test_r02_passes_for_known_agents():
    wf = _wf([_step("a", agent="odin"), _step("b", agent="mimir")])
    assert rule_known_agents(wf, known_agents={"Odin", "Mimir"}) == []


# ── R03: unique output keys ─────────────────────────────────────────────────


def test_r03_ok_when_no_outputs():
    wf = _wf([_step("a"), _step("b")])
    assert rule_unique_output_keys(wf) == []


def test_r03_flags_duplicate_output_key():
    wf = _wf([
        _step("a", output_key="result"),
        _step("b", output_key="result"),
    ])
    issues = rule_unique_output_keys(wf)
    assert len(issues) == 1
    assert issues[0].code == "R03"
    assert "'a' and 'b'" in issues[0].message or "a" in issues[0].message


def test_r03_passes_unique_output_keys():
    wf = _wf([
        _step("a", output_key="foo"),
        _step("b", output_key="bar"),
    ])
    assert rule_unique_output_keys(wf) == []


# ── R04: input keys resolvable ──────────────────────────────────────────────


def test_r04_ok_when_input_matches_variable():
    wf = _wf([_step("a", input_key="topic")], variables={"topic": "ai"})
    assert rule_input_keys_resolvable(wf) == []


def test_r04_ok_when_input_matches_prior_output():
    wf = _wf([
        _step("a", output_key="alpha"),
        _step("b", input_key="alpha"),
    ])
    assert rule_input_keys_resolvable(wf) == []


def test_r04_flags_undeclared_input_key():
    wf = _wf([_step("a", input_key="ghost")])
    issues = rule_input_keys_resolvable(wf)
    assert len(issues) == 1
    assert "undeclared" in issues[0].message
    assert issues[0].severity == Severity.ERROR


def test_r04_warns_on_forward_reference():
    wf = _wf([
        _step("a", input_key="future"),
        _step("b", output_key="future"),
    ])
    issues = rule_input_keys_resolvable(wf)
    assert len(issues) == 1
    assert issues[0].severity == Severity.WARNING
    assert "forward reference" in issues[0].message


# ── R05: no cycles ───────────────────────────────────────────────────────────


def test_r05_ok_when_acyclic():
    wf = _wf([
        _step("a", output_key="o1"),
        _step("b", output_key="o2", input_key="o1"),
        _step("c", input_key="o2"),
    ])
    assert rule_no_cycles_in_input_chain(wf) == []


def test_r05_detects_self_loop():
    wf = _wf([_step("a", output_key="o", input_key="o")])
    issues = rule_no_cycles_in_input_chain(wf)
    assert len(issues) == 1
    assert "cycle" in issues[0].message.lower()
    assert issues[0].code == "R05"


def test_r05_detects_two_node_cycle():
    wf = _wf([
        _step("a", output_key="oa", input_key="ob"),
        _step("b", output_key="ob", input_key="oa"),
    ])
    issues = rule_no_cycles_in_input_chain(wf)
    assert any("cycle" in i.message.lower() for i in issues)


def test_r05_detects_three_node_cycle():
    wf = _wf([
        _step("a", output_key="o1", input_key="o3"),
        _step("b", output_key="o2", input_key="o1"),
        _step("c", output_key="o3", input_key="o2"),
    ])
    issues = rule_no_cycles_in_input_chain(wf)
    assert any("cycle" in i.message.lower() for i in issues)


# ── R06: gate consistency ───────────────────────────────────────────────────


def test_r06_no_issues_when_no_gates():
    wf = _wf([_step("a"), _step("b", input_key="")])
    assert rule_gate_consistency(wf) == []


def test_r06_flags_agent_review_without_reviewer():
    step = _step("a", gate=QualityGate(type=GateType.AGENT_REVIEW))
    wf = _wf([step])
    issues = rule_gate_consistency(wf)
    assert len(issues) == 1
    assert "AGENT_REVIEW" in issues[0].message or "reviewer_agent" in issues[0].message


def test_r06_passes_agent_review_with_reviewer():
    step = _step(
        "a",
        gate=QualityGate(type=GateType.AGENT_REVIEW, reviewer_agent="heimdall"),
    )
    wf = _wf([step])
    assert rule_gate_consistency(wf) == []


def test_r06_reviewer_must_be_known_when_set_provided():
    step = _step(
        "a",
        gate=QualityGate(type=GateType.AGENT_REVIEW, reviewer_agent="ghost"),
    )
    wf = _wf([step])
    known = {"odin", "heimdall"}
    issues = rule_gate_consistency(wf, known_agents=known)
    assert any("ghost" in i.message for i in issues)


def test_r06_custom_gate_does_not_need_reviewer():
    step = _step("a", gate=QualityGate(type=GateType.CUSTOM, custom_check="len(x)>0"))
    wf = _wf([step])
    assert rule_gate_consistency(wf) == []


def test_r06_content_check_gate_does_not_need_reviewer():
    step = _step("a", gate=QualityGate(type=GateType.CONTENT_CHECK))
    wf = _wf([step])
    assert rule_gate_consistency(wf) == []


def test_r06_custom_gate_with_reviewer_does_not_trigger_error():
    step = _step(
        "a",
        gate=QualityGate(type=GateType.CUSTOM, custom_check="True", reviewer_agent="ghost"),
    )
    wf = _wf([step])
    # CUSTOM gate + reviewer set is harmless — no error from R06
    assert rule_gate_consistency(wf) == []


def test_r06_content_check_with_reviewer_must_be_known():
    step = _step(
        "a",
        gate=QualityGate(type=GateType.CONTENT_CHECK, reviewer_agent="ghost"),
    )
    known = {"odin"}
    issues = rule_gate_consistency(_wf([step]), known_agents=known)
    assert any("ghost" in i.message for i in issues)


# ── R07: unused variables ───────────────────────────────────────────────────


def test_r07_no_warn_when_no_variables():
    wf = _wf([_step("a")])
    assert rule_unused_variables(wf) == []


def test_r07_warns_on_unused_variable():
    wf = _wf([_step("a", intent="something")], variables={"topic": "ai"})
    issues = rule_unused_variables(wf)
    assert len(issues) == 1
    assert "'topic'" in issues[0].message
    assert issues[0].severity == Severity.WARNING


def test_r07_silent_when_variable_referenced_in_intent():
    wf = _wf(
        [_step("a", intent="research the {{ topic }}")],
        variables={"topic": "ai"},
    )
    issues = rule_unused_variables(wf)
    assert issues == []


def test_r07_silent_when_variable_name_in_description():
    wf = _wf(
        [_step("a", description="uses topic context")],
        variables={"topic": "ai"},
    )
    issues = rule_unused_variables(wf)
    assert issues == []


# ── R08: orphan output keys ─────────────────────────────────────────────────


def test_r08_flags_unconsumed_output():
    wf = _wf([
        _step("a", output_key="alpha"),
        _step("b", output_key="beta"),
        _step("c", input_key="alpha"),  # reads alpha but not beta
    ])
    issues = rule_orphan_output_keys(wf)
    codes = [i.code for i in issues]
    assert "R08" in codes
    # beta should be in the orphan list
    assert any("beta" in i.message for i in issues)
    # alpha should NOT be orphan
    assert not any("alpha" in i.message for i in issues)


def test_r08_info_severity_means_ok_still_true_when_only_orphans():
    wf = _wf([_step("a", output_key="lonely")])
    report = validate_definition(wf)
    # No errors → report.ok True even if we have INFO orphans
    assert report.ok is True
    info_codes = [i.code for i in report.issues if i.severity == Severity.INFO]
    assert "R08" in info_codes


# ── validate_definition / validate_package end-to-end ───────────────────────


def test_validate_definition_runs_all_rules():
    wf = _wf([
        _step("a", agent="odin", output_key="o1"),
        _step("b", agent="odin", input_key="o1", output_key="o2"),
        _step("c", agent="odin", input_key="o2"),
    ])
    known = {"odin"}
    report = validate_definition(wf, known_agents=known)
    assert report.workflow_name == "test-wf"
    assert report.ok is True


def test_validate_definition_collects_multiple_issues():
    wf = _wf([
        _step("", agent="ghost"),  # R01 + R02
        _step("b", agent="odin", output_key="dup"),
        _step("b", agent="odin", output_key="dup"),  # R01 dup + R03
    ])
    known = {"odin"}
    report = validate_definition(wf, known_agents=known)
    codes = {i.code for i in report.issues}
    assert "R01" in codes
    assert "R02" in codes
    assert "R03" in codes


def test_validate_definition_skip_agent_checks_with_none():
    wf = _wf([
        _step("a", agent="anyone"),
        _step("b", agent="anyone-else", output_key="o1"),
    ])
    report = validate_definition(wf, known_agents=None)
    # R02 must not fire
    assert all(i.code != "R02" for i in report.issues)


def test_validate_definition_custom_rule_subset():
    """Caller can pass a subset of rules."""
    wf = _wf([
        _step("a", agent="odin"),
        _step("a"),  # duplicate name → R01 fires
    ])
    known = {"odin"}
    # Only run R02, so R01 must not appear
    report = validate_definition(
        wf, known_agents=known, rules=[rule_known_agents]
    )
    assert all(i.code != "R01" for i in report.issues)
    assert report.ok is True


def test_validate_package_runs_against_package_workflow():
    pkg = WorkflowPackage(
        manifest=WorkflowPackageManifest(
            name="pkg",
            description="",
            version="1.0",
            tags=[],
        ),
        workflow=_wf([_step("a", agent="odin"), _step("b", output_key="o")]),
        path=None,  # type: ignore[arg-type]
    )
    report = validate_package(pkg, known_agents={"odin"})
    assert report.workflow_name == "test-wf"
    assert report.ok is True


def test_validate_all_rules_constant_shape():
    """The ALL_RULES list has the expected 8 entries in expected order."""
    assert len(ALL_RULES) == 8
    assert ALL_RULES[0] is rule_unique_step_names
    assert ALL_RULES[1] is rule_known_agents
    assert ALL_RULES[2] is rule_unique_output_keys
    assert ALL_RULES[3] is rule_input_keys_resolvable
    assert ALL_RULES[4] is rule_no_cycles_in_input_chain
    assert ALL_RULES[5] is rule_gate_consistency
    assert ALL_RULES[6] is rule_unused_variables
    assert ALL_RULES[7] is rule_orphan_output_keys


# ── Integration: error propagation through full validate_definition ────────


def test_validate_integration_with_realistic_workflow():
    """Build a workflow that triggers errors in multiple rules at once."""
    # step 'bad' has an undeclared input AND a duplicate output AND an unknown agent
    good_a = _step("good_a", agent="odin", output_key="alpha")
    bad_b = _step(
        "bad",
        agent="phantom",
        output_key="alpha",  # dup with good_a
        input_key="nonexistent",  # R04 error
        gate=QualityGate(type=GateType.AGENT_REVIEW),  # R06 (no reviewer)
    )
    good_c = _step("good_c", agent="odin", input_key="alpha")  # reads alpha
    wf = _wf([good_a, bad_b, good_c])
    known = {"odin", "heimdall"}
    report = validate_definition(wf, known_agents=known)
    codes = {i.code for i in report.errors}
    # Every rule R02, R03, R04, R06 fires for 'bad'
    assert {"R02", "R03", "R04", "R06"}.issubset(codes)
    assert report.ok is False
    assert len(report.errors) >= 4


def test_validate_uses_report_add_helper_internally():
    """Direct call to a rule with ``report=`` appends to the report."""
    wf = _wf([_step("a"), _step("a")])
    report = ValidationReport(workflow_name=wf.name)
    rule_unique_step_names(wf, report=report)
    assert len(report.issues) == 1
    assert report.issues[0].code == "R01"


def test_validate_works_without_known_agents_kwarg():
    """Even when called without the kwarg, validation proceeds (R02 skipped)."""
    wf = _wf([_step("x"), _step("y", output_key="o")])
    report = validate_definition(wf)
    assert report.ok is True
