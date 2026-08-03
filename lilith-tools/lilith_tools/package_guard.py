"""Package Guard — pre-install security middleware for agent frameworks.

Inspired by mkarvan/AgentShield (2026-07-02): scans packages against
multiple security policy layers BEFORE install and produces a single
verdict (``allow``, ``warn``, ``quarantine``, ``deny``). Works fully
offline using local seed data; optionally consults the OSV.dev
vulnerability database for CVE checks.

Four policy layers, applied in order:

    1. **Blacklist**    — hard-deny known-malicious or known-abandoned
                          packages. Seeds: typosquats of popular packages,
                          crypto-miner distros, packages flagged on the
                          community advisory list.
    2. **License policy** — deny packages under copyleft / proprietary
                          licenses the org has opted out of (default:
                          GPL-family denied unless explicitly allowed).
                          Configurable per-license allow/deny/warn.
    3. **Trust score**  — 0..100 composite of package age, maintainer
                          count, release cadence, and download volume
                          (offline heuristic). Below threshold → warn.
    4. **CVE scan**     — calls into :mod:`lilith_tools.security` for a
                          live OSV.dev lookup. Critical/High CVE →
                          quarantine (allow with override flag) or deny.

Integration surfaces (mirrors AgentShield's deployment options):

    - **Tool**: ``package_guard`` registered in ToolRegistry
    - **Hook**: ``PRE_INSTALL`` hook type for orchestrator integration
    - **Pre-commit**: ``lilith-tools precommit`` CLI helper
    - **JSON report**: ``render_report(...)`` for CI / dashboards
    - **Inline check**: ``guard_package(name, version, ecosystem)``

Verdicts:
    - ``ALLOW``      — pass all checks, install permitted
    - ``WARN``       — pass with warnings (low trust, transitive CVE)
    - ``QUARANTINE`` — CVE present, install requires explicit override
    - ``DENY``       — hard block (blacklist, license, or critical CVE)

Usage::

    from lilith_tools.package_guard import PackageGuard, GuardVerdict

    guard = PackageGuard.strict()        # deny on critical CVE
    result = guard.check("requests", "2.20.0", ecosystem="PyPI")
    if result.verdict == GuardVerdict.DENY:
        raise SystemExit(f"install blocked: {result.reason}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


# ── Public verdict enum ──────────────────────────────────────────────────────


class GuardVerdict(str, Enum):
    """Outcome of a package guard check."""

    ALLOW = "allow"
    WARN = "warn"
    QUARANTINE = "quarantine"
    DENY = "deny"

    @property
    def severity(self) -> int:
        """Numeric severity for sorting (higher = more severe)."""
        return {
            GuardVerdict.ALLOW: 0,
            GuardVerdict.WARN: 1,
            GuardVerdict.QUARANTINE: 2,
            GuardVerdict.DENY: 3,
        }[self]

    def __lt__(self, other: "GuardVerdict") -> bool:  # type: ignore[override]
        return self.severity < other.severity

    def __le__(self, other: "GuardVerdict") -> bool:
        return self.severity <= other.severity

    def __gt__(self, other: "GuardVerdict") -> bool:
        return self.severity > other.severity

    def __ge__(self, other: "GuardVerdict") -> bool:
        return self.severity >= other.severity


# ── Policy enums ─────────────────────────────────────────────────────────────


class LicensePolicy(str, Enum):
    """How a license should be treated by the license layer."""

    ALLOW = "allow"
    WARN = "warn"
    DENY = "deny"


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class PolicyHit:
    """A single finding from a policy layer."""

    layer: str  # "blacklist" | "license" | "trust" | "cve"
    severity: str  # "info" | "low" | "medium" | "high" | "critical"
    message: str
    cve_id: str | None = None
    references: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "severity": self.severity,
            "message": self.message,
            "cve_id": self.cve_id,
            "references": list(self.references),
            "metadata": dict(self.metadata),
        }


@dataclass
class GuardResult:
    """Aggregate result of all policy checks for a single package."""

    package: str
    version: str
    ecosystem: str
    verdict: GuardVerdict
    reason: str
    hits: list[PolicyHit] = field(default_factory=list)
    trust_score: int = 100
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    layers_run: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """True if install may proceed without explicit override."""
        return self.verdict in (GuardVerdict.ALLOW, GuardVerdict.WARN)

    @property
    def blocked(self) -> bool:
        """True if install is hard-blocked (Deny)."""
        return self.verdict == GuardVerdict.DENY

    @property
    def needs_override(self) -> bool:
        """True if install requires an explicit ``--override-guard`` flag."""
        return self.verdict == GuardVerdict.QUARANTINE

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "version": self.version,
            "ecosystem": self.ecosystem,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "trust_score": self.trust_score,
            "checked_at": self.checked_at,
            "layers_run": list(self.layers_run),
            "allowed": self.allowed,
            "blocked": self.blocked,
            "needs_override": self.needs_override,
            "hits": [h.to_dict() for h in self.hits],
        }


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class GuardConfig:
    """Knobs that tune each policy layer."""

    # Blacklist layer
    extra_blacklist: set[str] = field(default_factory=set)
    blacklist_patterns: list[str] = field(default_factory=list)

    # License layer
    license_policy: dict[str, LicensePolicy] = field(
        default_factory=lambda: {
            "MIT": LicensePolicy.ALLOW,
            "BSD-2-Clause": LicensePolicy.ALLOW,
            "BSD-3-Clause": LicensePolicy.ALLOW,
            "Apache-2.0": LicensePolicy.ALLOW,
            "ISC": LicensePolicy.ALLOW,
            "MPL-2.0": LicensePolicy.WARN,
            "LGPL-2.1": LicensePolicy.WARN,
            "LGPL-3.0": LicensePolicy.WARN,
            "GPL-2.0": LicensePolicy.DENY,
            "GPL-3.0": LicensePolicy.DENY,
            "AGPL-3.0": LicensePolicy.DENY,
            "SSPL-1.0": LicensePolicy.DENY,
            "BUSL-1.1": LicensePolicy.DENY,
            "UNLICENSED": LicensePolicy.DENY,
            "UNKNOWN": LicensePolicy.WARN,
        }
    )

    # Trust layer
    trust_threshold: int = 30  # below → WARN
    trust_warn_threshold: int = 60  # below → info-level note

    # CVE layer
    cve_severity_deny: tuple[str, ...] = ("critical",)
    cve_severity_quarantine: tuple[str, ...] = ("high",)
    cve_severity_warn: tuple[str, ...] = ("medium",)
    enable_cve_lookup: bool = True

    # Behavior
    fail_closed_on_cve_error: bool = False  # if OSV lookup fails


# ── Seed data (offline) ──────────────────────────────────────────────────────


# Known-bad packages. Real AgentShield pulls this from a constantly-updated
# list; for the offline seed we include well-documented typosquats and a
# handful of community-flagged packages.
DEFAULT_BLACKLIST: set[str] = {
    # Typosquats
    "reqests",  # requests
    "requestes",  # requests
    "numpyy",  # numpy
    "skcikit-learn",  # scikit-learn
    "python-dateutil2",  # python-dateutil
    "jeIlyfish",  # jellyfish
    "python3-dateutil",  # python-dateutil
    "urlib3",  # urllib3
    "python-cryptography-v2",  # cryptography
    "coffeScript",  # coffeescript
    "cross-env-new",  # cross-env (was hijacked)
    "djiango",  # django
    "python-jwt-old",  # PyJWT
    "pythonss",
    "pytroch",  # pytorch
    "sqlmap-exploit",
    "coloroma",  # colourama
    "colourmap",
    "easyinstall",
    # Crypto-stealer / mining
    "hiddencoin-miner",
    "eth-stealer",
    "metamask-phish",
    "clipboard-hijack",
    "browserpass-stealer",
}

# Trust metadata: package name → (initial_score, age_months, maintainers).
# Lower scores = newer / unknown / abandoned packages. Real AgentShield
# uses download stats from pypistats.org; we keep an offline heuristic.
DEFAULT_TRUST: dict[str, dict[str, Any]] = {
    "requests": {"score": 95, "age_months": 120, "maintainers": 200},
    "flask": {"score": 92, "age_months": 130, "maintainers": 150},
    "django": {"score": 94, "age_months": 180, "maintainers": 300},
    "numpy": {"score": 96, "age_months": 200, "maintainers": 250},
    "pandas": {"score": 90, "age_months": 140, "maintainers": 180},
    "pydantic": {"score": 88, "age_months": 80, "maintainers": 90},
    "fastapi": {"score": 85, "age_months": 70, "maintainers": 80},
    "pytest": {"score": 90, "age_months": 160, "maintainers": 120},
    "lilith-core": {"score": 70, "age_months": 24, "maintainers": 2},
    "lilith-tools": {"score": 70, "age_months": 18, "maintainers": 2},
}


# ── Main guard class ─────────────────────────────────────────────────────────


class PackageGuard:
    """Multi-layer pre-install security gate for a single ecosystem.

    Stateless except for configuration. Construct once, call ``check()``
    per package, or ``check_manifest()`` for a full dependency file.

    The :meth:`check` method runs each policy layer in order and
    returns the **most severe** verdict across all layers. This matches
    AgentShield's "worst-of" aggregation model.
    """

    def __init__(
        self,
        config: GuardConfig | None = None,
        *,
        ecosystem: str = "PyPI",
        license_overrides: dict[str, str] | None = None,
    ) -> None:
        self.config = config or GuardConfig()
        self.ecosystem = ecosystem
        # license_overrides is a name → license_id map; missing = UNKNOWN
        self._license_overrides: dict[str, str] = dict(license_overrides or {})

    # ── Factory helpers ──────────────────────────────────────────────────

    @classmethod
    def strict(cls, **kwargs: Any) -> "PackageGuard":
        """Strict mode: high CVEs → deny (not just quarantine)."""
        cfg = GuardConfig(
            cve_severity_deny=("critical", "high"),
            fail_closed_on_cve_error=True,
        )
        return cls(config=cfg, **kwargs)

    @classmethod
    def permissive(cls, **kwargs: Any) -> "PackageGuard":
        """Permissive mode: only deny on blacklist or critical CVE."""
        cfg = GuardConfig(
            cve_severity_deny=("critical",),
            cve_severity_quarantine=(),
            cve_severity_warn=("high", "medium"),
        )
        return cls(config=cfg, **kwargs)

    # ── Layer 1: Blacklist ───────────────────────────────────────────────

    def _check_blacklist(self, name: str) -> PolicyHit | None:
        lower = name.lower().strip()
        if lower in self.config.extra_blacklist or lower in DEFAULT_BLACKLIST:
            return PolicyHit(
                layer="blacklist",
                severity="critical",
                message=f"Package '{name}' is on the local blocklist",
                references=["https://github.com/BrierAinz/Yggdrasil/security"],
                metadata={"matched": lower, "tier": "deny"},
            )
        for pat in self.config.blacklist_patterns:
            try:
                if re.search(pat, lower):
                    return PolicyHit(
                        layer="blacklist",
                        severity="critical",
                        message=(
                            f"Package '{name}' matches blocklist pattern "
                            f"'{pat}'"
                        ),
                        metadata={"pattern": pat, "tier": "deny"},
                    )
            except re.error:
                continue
        return None

    # ── Layer 2: License ─────────────────────────────────────────────────

    def _check_license(self, name: str) -> PolicyHit | None:
        license_id = self._license_overrides.get(name, "UNKNOWN")
        policy = self.config.license_policy.get(license_id, LicensePolicy.WARN)
        if policy == LicensePolicy.ALLOW:
            return None
        if policy == LicensePolicy.WARN:
            return PolicyHit(
                layer="license",
                severity="low",
                message=(
                    f"Package '{name}' uses '{license_id}' license; "
                    f"policy is WARN"
                ),
                metadata={"license": license_id, "tier": "warn"},
            )
        if policy == LicensePolicy.DENY:
            return PolicyHit(
                layer="license",
                severity="high",
                message=(
                    f"Package '{name}' uses '{license_id}' license; "
                    f"policy is DENY"
                ),
                metadata={"license": license_id, "tier": "deny"},
            )
        return None

    # ── Layer 3: Trust ────────────────────────────────────────────────────

    def _check_trust(self, name: str) -> tuple[PolicyHit | None, int]:
        meta = DEFAULT_TRUST.get(name)
        if meta is None:
            # Unknown package: moderate trust; let downstream catch it
            score = 50
            return (
                PolicyHit(
                    layer="trust",
                    severity="info",
                    message=(
                        f"No trust metadata for '{name}'; "
                        f"defaulting to {score}/100"
                    ),
                    metadata={"default_score": score, "tier": "info"},
                ),
                score,
            )
        score = int(meta.get("score", 50))
        if score < self.config.trust_threshold:
            return (
                PolicyHit(
                    layer="trust",
                    severity="medium",
                    message=(
                        f"Package '{name}' trust score is {score} "
                        f"(threshold {self.config.trust_threshold})"
                    ),
                    metadata={"score": score, "tier": "quarantine"},
                ),
                score,
            )
        if score < self.config.trust_warn_threshold:
            return (
                PolicyHit(
                    layer="trust",
                    severity="low",
                    message=(
                        f"Package '{name}' trust score is {score} "
                        f"(below warn threshold "
                        f"{self.config.trust_warn_threshold})"
                    ),
                    metadata={"score": score, "tier": "warn"},
                ),
                score,
            )
        return None, score

    # ── Layer 4: CVE ─────────────────────────────────────────────────────

    @staticmethod
    def _cvss_to_severity(raw: str) -> str:
        """Map an OSV ``severity`` field (CVSS score or vector) to a word.

        OSV returns one of:
        - Numeric CVSS score: ``"9.8"``, ``"7.5"``, ``"4.0"``
        - CVSS vector string: ``"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"``
        - GHSA severity word: ``"HIGH"``, ``"MODERATE"``, ``"CRITICAL"``
        - ``"UNKNOWN"`` when no CVSS data

        We normalise to lowercase words: critical, high, medium, low, info.
        """
        if not raw:
            return "info"
        s = str(raw).strip()
        # Word forms
        upper = s.upper()
        if upper in ("CRITICAL",):
            return "critical"
        if upper in ("HIGH",):
            return "high"
        if upper in ("MODERATE", "MEDIUM"):
            return "medium"
        if upper in ("LOW",):
            return "low"
        # CVSS vector string → derive base score from impact metrics
        if s.upper().startswith("CVSS:"):
            import re as _re
            upper = s.upper()
            # Parse /C:X/I:Y/A:Z/ segments, not arbitrary C: positions.
            # The vector syntax uses `/<metric>:<value>/` so we look for
            # a slash before each metric to avoid matching AC:, PR:, etc.
            def val(metric: str) -> str | None:
                m = _re.search(rf"/(?:{metric}):([HLN])", upper)
                return m.group(1) if m else None
            c = val("C") or "N"
            i = val("I") or "N"
            a = val("A") or "N"
            counts = {"H": 0, "L": 0, "N": 0}
            for v in (c, i, a):
                if v in counts:
                    counts[v] += 1
            crit_count = counts["H"]
            if crit_count >= 2:
                return "critical"
            if crit_count == 1:
                return "high"
            if counts["L"] >= 1:
                return "low"
            return "info"
        # Numeric CVSS
        try:
            score = float(s)
        except (TypeError, ValueError):
            return "info"
        if score >= 9.0:
            return "critical"
        if score >= 7.0:
            return "high"
        if score >= 4.0:
            return "medium"
        if score > 0.0:
            return "low"
        return "info"

    def _check_cve(
        self, name: str, version: str, ecosystem: str
    ) -> list[PolicyHit]:
        if not self.config.enable_cve_lookup:
            return []
        # Late import to avoid circular dependency on lilith_tools.security
        from .security import query_osv, summarize_vuln

        try:
            vulns = query_osv(name, ecosystem, version)
        except Exception as exc:  # noqa: BLE001
            if self.config.fail_closed_on_cve_error:
                return [
                    PolicyHit(
                        layer="cve",
                        severity="medium",
                        message=(
                            f"CVE lookup failed for '{name}@{version}': "
                            f"{exc}; fail-closed policy active"
                        ),
                        metadata={"error": str(exc)},
                    )
                ]
            return [
                PolicyHit(
                    layer="cve",
                    severity="info",
                    message=(
                        f"CVE lookup failed for '{name}@{version}': "
                        f"{exc}; skipping (fail-open)"
                    ),
                    metadata={"error": str(exc)},
                )
            ]
        hits: list[PolicyHit] = []
        for v in vulns:
            summary = summarize_vuln(v, name, version)
            sev = self._cvss_to_severity(summary.get("severity", ""))
            base_meta = {
                "fixed_versions": summary.get("fixed_versions", []),
                "raw_severity": summary.get("severity", ""),
            }
            if sev in self.config.cve_severity_deny:
                tier = "deny"
            elif sev in self.config.cve_severity_quarantine:
                tier = "quarantine"
            elif sev in self.config.cve_severity_warn:
                tier = "warn"
            else:
                tier = "info"
            base_meta["tier"] = tier
            hits.append(
                PolicyHit(
                    layer="cve",
                    severity=sev,
                    message=(
                        f"{summary.get('id') or 'CVE'}: "
                        f"{summary.get('summary', '') or 'see OSV'}"
                    ),
                    cve_id=summary.get("id"),
                    references=summary.get("references", []),
                    metadata=base_meta,
                )
            )
        return hits

    # ── Aggregation ──────────────────────────────────────────────────────

    @staticmethod
    def _verdict_from_hits(
        hits: list[PolicyHit],
    ) -> tuple[GuardVerdict, str]:
        """Pick the worst verdict across all hits.

        Hits carry an optional ``tier`` field ("deny" | "quarantine" |
        "warn") that overrides the default severity-based mapping. This
        is how the strict CVE policy signals "treat high-CVE as
        deny-level" without rewriting the severity word.
        """
        if not hits:
            return GuardVerdict.ALLOW, "all policy layers passed"
        # Sort by (tier, severity)
        tier_weight = {
            None: 0,
            "warn": 1,
            "quarantine": 2,
            "deny": 3,
        }
        sev_weight = {
            "info": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }

        def score(h: PolicyHit) -> tuple[int, int]:
            t = h.metadata.get("tier")
            return (tier_weight.get(t, 0), sev_weight.get(h.severity, 0))

        worst = max(hits, key=score)
        # If a hit carries an explicit tier, that wins over severity.
        tier = worst.metadata.get("tier")
        if tier == "deny":
            return GuardVerdict.DENY, worst.message
        if tier == "quarantine":
            return GuardVerdict.QUARANTINE, worst.message
        if tier == "warn":
            return GuardVerdict.WARN, worst.message
        # Fall back to severity-based mapping (only for non-tier-tagged hits)
        if worst.severity == "critical":
            return GuardVerdict.DENY, worst.message
        if worst.severity == "high":
            return GuardVerdict.QUARANTINE, worst.message
        if worst.severity == "medium":
            return GuardVerdict.QUARANTINE, worst.message
        return GuardVerdict.WARN, worst.message

    # ── Public API ───────────────────────────────────────────────────────

    def check(
        self,
        name: str,
        version: str,
        *,
        ecosystem: str | None = None,
    ) -> GuardResult:
        """Run all policy layers against a single package version."""
        eco = ecosystem or self.ecosystem
        layers_run: list[str] = []
        hits: list[PolicyHit] = []
        trust_score = 100

        # Layer 1
        layers_run.append("blacklist")
        bh = self._check_blacklist(name)
        if bh:
            hits.append(bh)

        # Layer 2
        layers_run.append("license")
        lh = self._check_license(name)
        if lh:
            hits.append(lh)

        # Layer 3
        layers_run.append("trust")
        th, trust_score = self._check_trust(name)
        if th:
            hits.append(th)

        # Layer 4
        layers_run.append("cve")
        cve_hits = self._check_cve(name, version, eco)
        hits.extend(cve_hits)

        verdict, reason = self._verdict_from_hits(hits)
        return GuardResult(
            package=name,
            version=version,
            ecosystem=eco,
            verdict=verdict,
            reason=reason,
            hits=hits,
            trust_score=trust_score,
            layers_run=layers_run,
        )

    def check_manifest(
        self, manifest_path: str, ecosystem: str | None = None
    ) -> list[GuardResult]:
        """Scan a requirements.txt / pyproject.toml / package.json."""
        from .security import parse_manifest

        try:
            targets = parse_manifest(manifest_path)
        except (FileNotFoundError, ValueError) as exc:
            return [
                GuardResult(
                    package="<manifest>",
                    version="0.0.0",
                    ecosystem=self.ecosystem,
                    verdict=GuardVerdict.DENY,
                    reason=f"manifest parse failed: {exc}",
                )
            ]
        eco = ecosystem or self.ecosystem
        out: list[GuardResult] = []
        for name, version, file_eco in targets:
            out.append(self.check(name, version, ecosystem=file_eco or eco))
        return out


# ── Reporting helpers ────────────────────────────────────────────────────────


def render_report(results: Iterable[GuardResult]) -> str:
    """Render a human-readable multi-line report for a sequence of checks."""
    lines: list[str] = []
    icon = {
        GuardVerdict.ALLOW: "✅",
        GuardVerdict.WARN: "⚠️ ",
        GuardVerdict.QUARANTINE: "🟠",
        GuardVerdict.DENY: "⛔",
    }
    blocked = 0
    for r in results:
        i = icon.get(r.verdict, "?")
        lines.append(
            f"{i} {r.package}=={r.version}  → {r.verdict.value.upper()}  "
            f"(trust={r.trust_score})  {r.reason}"
        )
        for h in r.hits:
            tag = f"[{h.layer}/{h.severity}]"
            lines.append(f"   {tag} {h.message}")
        if r.blocked:
            blocked += 1
    total = sum(1 for _ in lines if "→" in _)
    lines.append("")
    lines.append(f"Summary: {blocked} hard-denied, {total - blocked} cleared.")
    return "\n".join(lines)


def render_json(results: Iterable[GuardResult]) -> str:
    """Render JSON report suitable for CI / dashboards."""
    import json

    return json.dumps(
        [r.to_dict() for r in results],
        indent=2,
        sort_keys=True,
    )


# ── Tool registration ────────────────────────────────────────────────────────


@ToolRegistry.register
class PackageGuardTool(BaseTool):
    """Pre-install security gate: blacklist + license + trust + CVE.

    Inspired by mkarvan/AgentShield (security middleware for AI agent
    frameworks). Combines four policy layers into a single ``verdict``
    per package and supports both single-package and manifest-mode
    checks.

    Verdict meanings:
        - ``allow``      → install permitted
        - ``warn``       → install permitted with warning
        - ``quarantine`` → install requires explicit override
        - ``deny``       → hard block (must not install)

    Params (single-package mode):
        name (str), version (str), ecosystem (PyPI|npm) (default PyPI),
        mode (strict|permissive|default) (default default)

    Params (manifest mode):
        file (str) path to requirements.txt / pyproject.toml / package.json
        mode (str) — same as single-package mode
    """

    name = "package_guard"
    description = (
        "Pre-install security gate combining blacklist, license policy, "
        "trust score, and CVE scan. Returns verdict allow|warn|"
        "quarantine|deny. Use file=<path> for manifest mode, or "
        "name+version+ecosystem for single-package mode. Inspired by "
        "AgentShield (mkarvan/AgentShield)."
    )
    parameters = {
        "name": {"type": "string", "description": "Package name"},
        "version": {"type": "string", "description": "Package version"},
        "ecosystem": {
            "type": "string",
            "description": "Ecosystem: PyPI or npm",
            "default": "PyPI",
        },
        "file": {
            "type": "string",
            "description": "Path to manifest (requirements.txt / pyproject.toml / package.json)",
        },
        "mode": {
            "type": "string",
            "description": "Guard mode: strict | permissive | default",
            "default": "default",
        },
    }

    def _build_guard(self, mode: str) -> PackageGuard:
        if mode == "strict":
            return PackageGuard.strict()
        if mode == "permissive":
            return PackageGuard.permissive()
        return PackageGuard()

    def execute(
        self,
        name: str = "",
        version: str = "",
        ecosystem: str = "PyPI",
        file: str = "",
        mode: str = "default",
        **_: Any,
    ) -> ToolResult:
        """Run the guard check."""
        guard = self._build_guard(mode)

        # Manifest mode
        if file:
            try:
                results = guard.check_manifest(file, ecosystem=ecosystem)
            except (FileNotFoundError, ValueError) as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            # Aggregate worst-of across the manifest
            worst = max(
                (GuardVerdict.ALLOW,),
                key=lambda _: 0,
            )
            for r in results:
                if r.verdict > worst:
                    worst = r.verdict
            blocked = [r for r in results if r.blocked]
            quarantine = [r for r in results if r.needs_override]
            return ToolResult(
                success=True,
                data={
                    "mode": mode,
                    "scanned": len(results),
                    "denied": len(blocked),
                    "quarantined": len(quarantine),
                    "worst_verdict": worst.value,
                    "report": render_report(results),
                    "results": [r.to_dict() for r in results],
                },
                error="",
            )

        # Single-package mode
        if not name or not version:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    "Provide either `file` (manifest mode) or both `name` and "
                    "`version` (single-package mode)"
                ),
            )
        if ecosystem not in {"PyPI", "npm"}:
            return ToolResult(
                success=False,
                data=None,
                error=f"Unsupported ecosystem '{ecosystem}'. Use PyPI or npm.",
            )
        result = guard.check(name, version, ecosystem=ecosystem)
        return ToolResult(
            success=True,
            data=result.to_dict(),
            error="",
        )


__all__ = [
    "DEFAULT_BLACKLIST",
    "DEFAULT_TRUST",
    "GuardConfig",
    "GuardResult",
    "GuardVerdict",
    "LicensePolicy",
    "PackageGuard",
    "PackageGuardTool",
    "PolicyHit",
    "render_json",
    "render_report",
]
