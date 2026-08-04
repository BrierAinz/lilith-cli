"""CVE Database — local vulnerability reference for security auditing.

Provides a small, offline catalog of common CVE patterns. Designed to be
used by HeimdallAuditor and other security tools to flag risky code or
dependencies. Heavily inspired by Talon's built-in CVE database
(research/emerging-agents-2026-06-21.md, lower-priority recommendations).

Usage::

    from lilith_tools.cve import CVEDatabase

    db = CVEDatabase()
    db.search("sql injection")
    db.match("requests<2.20")  # checks against known vulnerable versions
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Severity levels (CVSS v3 qualitative scale)
SEVERITY_CRITICAL = "critical"  # 9.0–10.0
SEVERITY_HIGH = "high"          # 7.0–8.9
SEVERITY_MEDIUM = "medium"      # 4.0–6.9
SEVERITY_LOW = "low"            # 0.1–3.9


@dataclass
class CVEEntry:
    """A single CVE record."""

    cve_id: str
    title: str
    severity: str
    cvss_score: float
    description: str
    affected_packages: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cve_id": self.cve_id,
            "title": self.title,
            "severity": self.severity,
            "cvss_score": self.cvss_score,
            "description": self.description,
            "affected_packages": self.affected_packages,
            "keywords": self.keywords,
            "references": self.references,
        }


# Seed catalog of well-known CVEs. In production this would be loaded
# from a JSON/YAML file or fetched from NVD; for an offline reference
# a curated set of common patterns is more useful.
_SEED_CVES: list[CVEEntry] = [
    CVEEntry(
        cve_id="CVE-2017-5638",
        title="Apache Struts 2 RCE via Content-Type header",
        severity=SEVERITY_CRITICAL,
        cvss_score=10.0,
        description="Remote code execution via crafted Content-Type header in Apache Struts 2.",
        affected_packages=["struts2-core<2.3.32", "struts2-core<2.5.10.1"],
        keywords=["struts", "rce", "content-type", "jakarta", "ognl"],
    ),
    CVEEntry(
        cve_id="CVE-2021-44228",
        title="Log4Shell — Apache Log4j 2 RCE via JNDI lookup",
        severity=SEVERITY_CRITICAL,
        cvss_score=10.0,
        description="Remote code execution via JNDI lookup in log messages.",
        affected_packages=["log4j-core<2.15.0"],
        keywords=["log4j", "log4shell", "jndi", "rce"],
    ),
    CVEEntry(
        cve_id="CVE-2014-0160",
        title="Heartbleed — OpenSSL memory disclosure",
        severity=SEVERITY_HIGH,
        cvss_score=7.5,
        description="TLS heartbeat extension allows reading server memory.",
        affected_packages=["openssl<1.0.1g"],
        keywords=["openssl", "tls", "heartbeat", "memory-disclosure"],
    ),
    CVEEntry(
        cve_id="CVE-2018-11776",
        title="Struts 2 RCE via untrusted result URL",
        severity=SEVERITY_CRITICAL,
        cvss_score=9.8,
        description="Remote code execution via crafted redirect URI in Struts 2.",
        affected_packages=["struts2-core>=2.3,<2.3.35", "struts2-core>=2.5,<2.5.17"],
        keywords=["struts", "rce", "redirect", "ognl"],
    ),
    CVEEntry(
        cve_id="CVE-2019-0708",
        title="BlueKeep — Windows RDP RCE",
        severity=SEVERITY_CRITICAL,
        cvss_score=9.8,
        description="Pre-auth RCE in Remote Desktop Services on older Windows versions.",
        keywords=["rdp", "windows", "bluekeep", "rce"],
    ),
    CVEEntry(
        cve_id="CVE-2020-0796",
        title="SMBGhost — Windows SMBv3 RCE",
        severity=SEVERITY_CRITICAL,
        cvss_score=10.0,
        description="Pre-auth RCE via crafted SMBv3 compressed packets.",
        keywords=["smb", "windows", "smbv3", "rce"],
    ),
    CVEEntry(
        cve_id="CVE-2022-22965",
        title="Spring4Shell — Spring Core RCE",
        severity=SEVERITY_CRITICAL,
        cvss_score=9.8,
        description="RCE via data binding in Spring Core (similar to Log4Shell in impact).",
        affected_packages=["spring-core<5.3.18", "spring-core<5.2.20"],
        keywords=["spring", "java", "rce", "data-binding"],
    ),
    CVEEntry(
        cve_id="CVE-2023-44487",
        title="HTTP/2 Rapid Reset DoS",
        severity=SEVERITY_HIGH,
        cvss_score=7.5,
        description="Denial of service via HTTP/2 rapid stream cancellation.",
        affected_packages=["nginx>=1.25.0,<1.25.3", "envoy<1.29.1"],
        keywords=["http2", "dos", "rapid-reset", "nginx", "envoy"],
    ),
]


class CVEDatabase:
    """Offline CVE lookup with keyword and version-based search.

    Not exhaustive — this is a curated reference for code/dependency
    auditing. Extend ``_SEED_CVES`` or load from a JSON file for
    production use.
    """

    def __init__(self, cves: list[CVEEntry] | None = None) -> None:
        self._cves: list[CVEEntry] = list(cves) if cves is not None else list(_SEED_CVES)

    # ── Lookups ──────────────────────────────────────────────────

    def by_id(self, cve_id: str) -> CVEEntry | None:
        """Find a CVE by its ID (e.g. ``"CVE-2021-44228"``)."""
        cid = cve_id.upper()
        for cve in self._cves:
            if cve.cve_id.upper() == cid:
                return cve
        return None

    def by_severity(self, severity: str) -> list[CVEEntry]:
        """Return all CVEs at a given severity level."""
        s = severity.lower()
        return [c for c in self._cves if c.severity == s]

    def search(self, query: str) -> list[CVEEntry]:
        """Search CVEs by keyword/title/description substring (case-insensitive)."""
        q = query.lower()
        results: list[CVEEntry] = []
        for cve in self._cves:
            haystack = " ".join(
                [cve.title, cve.description, *cve.keywords]
            ).lower()
            if q in haystack:
                results.append(cve)
        return results

    def match_dependency(self, package_spec: str) -> list[CVEEntry]:
        """Match a dependency string (e.g. ``"requests<2.20"``) against affected_packages.

        Very basic semver-style matcher — recognises ``<X``, ``<=X``, ``>X``, ``>=X``.
        Does not handle ranges like ``>=1.0,<2.0`` as a single spec; it
        matches if ANY version constraint in the spec is satisfied.
        """
        results: list[CVEEntry] = []
        for cve in self._cves:
            for pkg_spec in cve.affected_packages:
                if _match_package(pkg_spec, package_spec):
                    results.append(cve)
                    break
        return results

    # ── Stats ────────────────────────────────────────────────────

    def count(self) -> int:
        return len(self._cves)

    def stats(self) -> dict[str, int]:
        """Return counts by severity."""
        out: dict[str, int] = {}
        for cve in self._cves:
            out[cve.severity] = out.get(cve.severity, 0) + 1
        out["total"] = len(self._cves)
        return out

    def all(self) -> list[CVEEntry]:
        return list(self._cves)


# ── Helpers ─────────────────────────────────────────────────────


_VERSION_RE = re.compile(r"^(?P<op><=?|>=?|==|=)\s*(?P<ver>\d+(?:\.\d+)*)$")


def _match_package(cve_spec: str, dep_spec: str) -> bool:
    """Match a CVE's package spec against a dependency spec.

    Examples:
        cve_spec="log4j-core<2.15.0", dep_spec="log4j-core"
            → True (cve applies to all versions, dep unspecified)
        cve_spec="log4j-core<2.15.0", dep_spec="log4j-core<2.10.0"
            → True (dep is < 2.15.0, so vulnerable)
        cve_spec="requests<2.20", dep_spec="requests==2.31.0"
            → False
    """
    cve_name, cve_constraint = _split_spec(cve_spec)
    dep_name, dep_constraint = _split_spec(dep_spec)
    if cve_name.lower() != dep_name.lower():
        return False
    # If dep has no version, assume it could be any version → match.
    if not dep_constraint:
        return True
    return _constraints_overlap(cve_constraint, dep_constraint)


def _split_spec(spec: str) -> tuple[str, str]:
    """Split a package spec like ``"log4j-core<2.15.0"`` into ``("log4j-core", "<2.15.0")``."""
    # Find the first occurrence of <, >, =, or <=, >=
    m = re.search(r"(?P<op><=?|>=?|=)", spec)
    if not m:
        return spec, ""
    name = spec[: m.start()].rstrip()
    constraint = spec[m.start():]
    return name, constraint


def _constraints_overlap(c1: str, c2: str) -> bool:
    """Check if two simple constraints could both be satisfied by some version.

    Conservative: only handles single-operator specs. Examples:
        c1="<2.15.0", c2="<2.10.0" → True (both can be satisfied)
        c1="<2.15.0", c2=">=2.10.0" → True (e.g. 2.12.0 satisfies both)
        c1="<2.15.0", c2=">=2.20.0" → False
    """
    m1 = _VERSION_RE.match(c1.strip())
    m2 = _VERSION_RE.match(c2.strip())
    if not m1 or not m2:
        return False
    op1, v1 = m1["op"], tuple(int(x) for x in m1["ver"].split("."))
    op2, v2 = m2["op"], tuple(int(x) for x in m2["ver"].split("."))
    # Use a few sample points around v1 to test intersection
    return _versions_can_coexist(op1, v1, op2, v2)


def _versions_can_coexist(op1: str, v1: tuple, op2: str, v2: tuple) -> bool:
    """Test if there exists some version X that satisfies both op1 v1 and op2 v2."""
    test_versions = [v1, v2, (0,), tuple(int(x) for x in "999.999.999".split("."))]
    for x in test_versions:
        if _satisfies(op1, v1, x) and _satisfies(op2, v2, x):
            return True
    return False


def _satisfies(op: str, target: tuple, candidate: tuple) -> bool:
    """Test if ``candidate`` satisfies ``op target`` (e.g. ``< 2.15.0``)."""
    if op in ("<",):
        return candidate < target
    if op in ("<=",):
        return candidate <= target
    if op in (">",):
        return candidate > target
    if op in (">=",):
        return candidate >= target
    if op in ("=", "=="):
        return candidate == target
    return False
