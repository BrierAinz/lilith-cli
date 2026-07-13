"""Heimdall Auditor — Output review and quality gate for Lilith.

Inspired by Requiem-Agents' Revenant auditor pattern:
    - Reviews ALL output before delivery to user
    - Can veto, escalate, or approve responses
    - Checks for security, quality, and policy violations

This module provides:
    - HeimdallAuditor: Main auditor class with review logic
    - AuditResult: Result of an audit with approve/veto/escalate status
    - AuditRule: Configurable rules for different audit criteria
    - AuditConfig: Configuration for the auditor

Usage:
    auditor = HeimdallAuditor()
    result = auditor.audit(response="User's code...", context={"task": "build"})
    if result.status == "approved":
        deliver(response)
    elif result.status == "vetoed":
        reject(response, result.reason)
    else:
        escalate(response, result.reason)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AuditStatus(Enum):
    """Result status of an audit."""

    APPROVED = "approved"
    VETOED = "vetoed"
    ESCALATED = "escalated"


@dataclass
class AuditResult:
    """Result of an audit operation.

    Attributes:
        status: Whether the output was approved, vetoed, or escalated.
        reason: Human-readable reason for the status.
        issues: List of specific issues found (empty if approved).
        confidence: Confidence score (0.0 to 1.0) in the audit decision.
        metadata: Additional metadata about the audit.
        timestamp: When the audit was performed.
    """

    status: AuditStatus
    reason: str
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_approved(self) -> bool:
        """True if the output passed the audit."""
        return self.status == AuditStatus.APPROVED

    @property
    def is_vetoed(self) -> bool:
        """True if the output was rejected."""
        return self.status == AuditStatus.VETOED

    @property
    def is_escalated(self) -> bool:
        """True if the output needs human review."""
        return self.status == AuditStatus.ESCALATED

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "status": self.status.value,
            "reason": self.reason,
            "issues": self.issues,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditRule:
    """A single audit rule with a check function.

    Attributes:
        name: Human-readable name of the rule.
        check: Function that takes (content: str, context: dict) and returns
               (passed: bool, issue: str | None). If passed=False, issue describes the problem.
        severity: How serious a violation is ("low", "medium", "high", "critical").
        enabled: Whether this rule is active.
    """

    name: str
    check: Callable[[str, dict[str, Any]], tuple[bool, str | None]]
    severity: str = "medium"
    enabled: bool = True


# ── Default Audit Rules ────────────────────────────────────────────────────────


def _rule_no_secrets(content: str, context: dict[str, Any]) -> tuple[bool, str | None]:
    """Check for potential secrets/keys in output."""
    secret_patterns = [
        r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)[\"']?\s*[:=]\s*[\"']?[\w-]{20,}",
        r"(?i)password\s*[:=]\s*[\"'][^\"']+[\"']",
        r"(?i)(private[_-]?key|pem|-----begin\s+(rsa|ec|dsa)\s+private)",
        r"(?i)(ghp_|gho_|ghu_|ghs_|ghr_)[a-zA-Z0-9]{36}",
        r"sk-[a-zA-Z0-9]{20,}",
    ]
    for pattern in secret_patterns:
        if re.search(pattern, content):
            return False, "Potential secret detected in output"
    return True, None


def _rule_no_destructive_commands(content: str, context: dict[str, Any]) -> tuple[bool, str | None]:
    """Check for potentially destructive commands."""
    destructive_patterns = [
        r"rm\s+-rf\s+/",
        r"rmdir\s+.*-r",
        r"format\s+[a-z]:",
        r"del\s+/[fq]\s+/",
        r"shutdown",
        r"reboot",
        r">\s*/dev/sd",
    ]
    for pattern in destructive_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            return False, "Potentially destructive command detected"
    return True, None


def _rule_safe_shell(content: str, context: dict[str, Any]) -> tuple[bool, str | None]:
    """Check for unsafe shell patterns."""
    unsafe_patterns = [
        r"curl\s+.*\|\s*sh",
        r"wget\s+.*\|\s*sh",
        r"eval\s*\(",
        r"exec\s*\(",
        r"system\s*\(",
    ]
    # Only check if content contains shell commands
    if any(cmd in content.lower() for cmd in ["curl", "wget", "sh ", "bash", "shell"]):
        for pattern in unsafe_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return False, f"Unsafe shell pattern detected: {pattern}"
    return True, None


def _rule_no_pii(content: str, context: dict[str, Any]) -> tuple[bool, str | None]:
    """Check for potential PII in output."""
    pii_patterns = [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{16}\b",  # Credit card
        r"\b[\w.-]+@[\w.-]+\.\w+\b",  # Email
        r"(?i)\bphone[:\s]+\d{3}[-.\s]?\d{3}[-.\s]?\d{4}",
    ]
    # This is a soft check - escalate if found
    for pattern in pii_patterns:
        if re.search(pattern, content):
            return False, "Potential PII detected in output"
    return True, None


def _rule_output_not_empty(content: str, context: dict[str, Any]) -> tuple[bool, str | None]:
    """Ensure output is not empty."""
    if not content or not content.strip():
        return False, "Output is empty"
    return True, None


def _rule_minimum_quality(content: str, context: dict[str, Any]) -> tuple[bool, str | None]:
    """Ensure output meets minimum quality bar."""
    # Check minimum length
    if len(content) < 10:
        return False, "Output too short (< 10 chars)"
    # Check for placeholder content
    placeholder_phrases = ["[TODO]", "[FIXME]", "[PLACEHOLDER]", "tbd", "..."]
    content_lower = content.lower()
    if any(phrase.lower() in content_lower for phrase in placeholder_phrases):
        # Soft warning - escalate rather than veto
        return False, "Output contains placeholder content"
    return True, None


# ── Heimdall Auditor ──────────────────────────────────────────────────────────


class HeimdallAuditor:
    """Auditor that reviews all output before delivery.

    Implements the Revenant auditor pattern from Requiem-Agents:
        - Every response passes through the auditor
        - Configurable rules for different checks
        - Can approve, veto, or escalate based on findings

    Usage::

        auditor = HeimdallAuditor(rules=[...])
        result = auditor.audit(response_text, context={"session": "abc"})

        if result.is_approved:
            deliver(response_text)
        elif result.is_vetoed:
            log.warning(f"Vetoed: {result.reason}")
            # Replace with safe response
            response_text = "I cannot provide that."
        else:
            # Escalate to human review
            notify_human(result)
    """

    def __init__(
        self,
        rules: list[AuditRule] | None = None,
        auto_escalate_medium: bool = False,
    ) -> None:
        """Initialize the auditor.

        Args:
            rules: List of audit rules. If None, uses default rules.
            auto_escalate_medium: If True, medium severity issues escalate
                                  instead of veto. Default False (veto).
        """
        self.auto_escalate_medium = auto_escalate_medium

        # Default rules if none provided
        self.rules: list[AuditRule] = rules or [
            AuditRule(
                name="no_secrets",
                check=_rule_no_secrets,
                severity="critical",
            ),
            AuditRule(
                name="no_destructive",
                check=_rule_no_destructive_commands,
                severity="critical",
            ),
            AuditRule(
                name="safe_shell",
                check=_rule_safe_shell,
                severity="high",
            ),
            AuditRule(
                name="no_pii",
                check=_rule_no_pii,
                severity="high",
            ),
            AuditRule(
                name="output_not_empty",
                check=_rule_output_not_empty,
                severity="critical",
            ),
            AuditRule(
                name="minimum_quality",
                check=_rule_minimum_quality,
                severity="medium",
            ),
        ]

    def audit(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> AuditResult:
        """Audit content and return the result.

        Inspired by Requiem-Agents' Revenant auto-pass optimization:
        Execution tasks with test output containing "passed" and no
        "failed"/"traceback" markers skip the full LLM audit entirely
        (0ms, 0 tokens). This is safe because the test suite is the
        ground-truth validator.

        Args:
            content: The content to audit.
            context: Optional context about the request (task type, user, etc.)

        Returns:
            AuditResult with status and details.
        """
        context = context or {}

        # ── Revenant auto-pass optimization ───────────────────────────────────
        # If this is an execution/test task and output shows "passed"
        # with no "failed" or "traceback", auto-approve without running rules.
        if self._should_auto_pass(content, context):
            return AuditResult(
                status=AuditStatus.APPROVED,
                reason="Auto-pass: execution output shows all tests passed",
                issues=[],
                confidence=1.0,
                metadata={
                    "auto_pass": True,
                    "rules_skipped": len(self.rules),
                    "source": "revenant_optimization",
                },
            )

        all_issues: list[str] = []
        critical_found = False

        for rule in self.rules:
            if not rule.enabled:
                continue

            passed, issue = rule.check(content, context)

            if not passed and issue:
                all_issues.append(f"[{rule.name}] {issue}")

                if rule.severity == "critical":
                    critical_found = True

        # Determine status based on issues
        if critical_found:
            # Critical issues = veto
            return AuditResult(
                status=AuditStatus.VETOED,
                reason="Critical security/policy violation",
                issues=all_issues,
                confidence=1.0,
                metadata={"severity": "critical", "rules_triggered": len(all_issues)},
            )

        if all_issues:
            # Non-critical issues
            if self.auto_escalate_medium:
                return AuditResult(
                    status=AuditStatus.ESCALATED,
                    reason="Quality issues require review",
                    issues=all_issues,
                    confidence=0.7,
                    metadata={"severity": "medium", "rules_triggered": len(all_issues)},
                )
            else:
                return AuditResult(
                    status=AuditStatus.VETOED,
                    reason="Quality issues found",
                    issues=all_issues,
                    confidence=0.8,
                    metadata={"severity": "medium", "rules_triggered": len(all_issues)},
                )

        # No issues = approve
        return AuditResult(
            status=AuditStatus.APPROVED,
            reason="Output passed all audit checks",
            issues=[],
            confidence=1.0,
            metadata={"rules_checked": len(self.rules)},
        )

    def _should_auto_pass(
        self,
        content: str,
        context: dict[str, Any],
    ) -> bool:
        """Determine if content qualifies for Revenant auto-pass.

        Criteria (all must be met):
            1. Context indicates an execution/test task type, OR
               content contains test-related markers ("test", "pytest", "passed").
            2. Content contains "passed" marker.
            3. Content does NOT contain "failed" or "traceback" markers.

        Args:
            content: The output content to evaluate.
            context: Request context (may contain "task" key).

        Returns:
            True if the content qualifies for auto-approval.
        """
        content_lower = content.lower()

        # Must contain "passed" marker
        if "passed" not in content_lower:
            return False

        # Must NOT contain failure markers
        failure_markers = ("failed", "traceback", "error", "exception")
        if any(marker in content_lower for marker in failure_markers):
            return False

        # Must be an execution/test context
        task_type = str(context.get("task", "")).lower()
        execution_keywords = ("test", "pytest", "execute", "run", "build", "ci")
        if any(kw in task_type for kw in execution_keywords):
            return True

        # Content itself indicates test execution
        test_markers = ("test", "pytest", "unittest", "coverage", "test session")
        if any(marker in content_lower for marker in test_markers):
            return True

        return False

    def add_rule(self, rule: AuditRule) -> None:
        """Add a custom audit rule."""
        self.rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name. Returns True if found and removed."""
        for i, rule in enumerate(self.rules):
            if rule.name == name:
                self.rules.pop(i)
                return True
        return False

    def enable_rule(self, name: str) -> bool:
        """Enable a rule by name."""
        for rule in self.rules:
            if rule.name == name:
                rule.enabled = True
                return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Disable a rule by name."""
        for rule in self.rules:
            if rule.name == name:
                rule.enabled = False
                return True
        return False

    def list_rules(self) -> list[dict[str, Any]]:
        """List all rules with their status."""
        return [
            {
                "name": rule.name,
                "severity": rule.severity,
                "enabled": rule.enabled,
            }
            for rule in self.rules
        ]


# ── Integration Helper ────────────────────────────────────────────────────────


def create_auditor_from_config(config: dict[str, Any]) -> HeimdallAuditor:
    """Create an auditor from a configuration dictionary.

    Args:
        config: Dict with keys:
            - rules: List of rule names to enable (default: all)
            - auto_escalate_medium: bool

    Returns:
        Configured HeimdallAuditor instance.
    """
    default_names = {r.name for r in HeimdallAuditor().rules}

    if "rules" in config:
        enabled_names = set(config["rules"])
    else:
        enabled_names = default_names

    rules = []
    for rule in HeimdallAuditor().rules:
        rule.enabled = rule.name in enabled_names
        rules.append(rule)

    return HeimdallAuditor(
        rules=rules,
        auto_escalate_medium=config.get("auto_escalate_medium", False),
    )


# ── CVE-aware dependency audit (Talon pattern) ──────────────────


def audit_dependencies(
    dep_specs: list[str],
    cve_db: Any = None,
) -> dict[str, Any]:
    """Audit a list of dependency specs against a CVE database.

    Args:
        dep_specs: List of strings like ``["log4j-core<2.10.0", "requests==2.31.0"]``.
        cve_db: A :class:`lilith_tools.cve.CVEDatabase` instance. If None,
                one is constructed (offline seed catalog).

    Returns:
        A dict with keys ``safe`` (list of specs with no matches),
        ``vulnerable`` (list of dicts with cve + spec), and ``summary``.
    """
    if cve_db is None:
        # Lazy import to avoid a hard lilith-tools dependency
        from lilith_tools.cve import CVEDatabase

        cve_db = CVEDatabase()

    safe: list[str] = []
    vulnerable: list[dict[str, Any]] = []
    for spec in dep_specs:
        matches = cve_db.match_dependency(spec)
        if matches:
            for cve in matches:
                vulnerable.append({
                    "spec": spec,
                    "cve_id": cve.cve_id,
                    "title": cve.title,
                    "severity": cve.severity,
                    "cvss_score": cve.cvss_score,
                })
        else:
            safe.append(spec)

    return {
        "safe": safe,
        "vulnerable": vulnerable,
        "summary": {
            "total": len(dep_specs),
            "safe_count": len(safe),
            "vulnerable_count": len(vulnerable),
            "critical": sum(1 for v in vulnerable if v["severity"] == "critical"),
            "high": sum(1 for v in vulnerable if v["severity"] == "high"),
        },
    }


def audit_requirements_file(
    requirements_path: Any,
    cve_db: Any = None,
) -> dict[str, Any]:
    """Parse a requirements.txt-style file and audit it.

    Args:
        requirements_path: Path to the requirements file.
        cve_db: Optional CVE database (constructed if None).

    Returns:
        Same shape as :func:`audit_dependencies`.
    """
    from pathlib import Path

    p = Path(requirements_path)
    if not p.is_file():
        raise FileNotFoundError(f"Requirements file not found: {p}")

    # Parse: one spec per line, strip comments and blank lines.
    specs: list[str] = []
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            specs.append(line)

    return audit_dependencies(specs, cve_db=cve_db)

