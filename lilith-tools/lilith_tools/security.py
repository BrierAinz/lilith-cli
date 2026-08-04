"""Security / CVE vulnerability scanner tool for Lilith.

Inspired by Talon's CVE database: built-in vulnerability search and tracking.

Queries the open-source OSV.dev API (https://api.osv.dev) — a free, no-auth
vulnerability database backed by Google and used by ``pip-audit``, ``npm audit``
and others. No API key required.

Capabilities:
    - Scan a single package by (name, ecosystem, version)
    - Scan a dependency manifest file (requirements.txt, pyproject.toml,
      package.json) and report vulnerabilities for every pinned dependency
    - Aggregate CVE IDs, severity, summary, fixed versions and references

Ecosystems supported: PyPI (``PyPI``), npm (``npm``).

Usage::

    tool = SecurityScannerTool()
    # single package
    tool.execute(name="requests", ecosystem="PyPI", version="2.20.0")
    # manifest file
    tool.execute(file="requirements.txt")
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

# ── Constants ────────────────────────────────────────────────────────────────

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
SUPPORTED_ECOSYSTEMS = {"PyPI", "npm"}
SUPPORTED_MANIFESTS = {"requirements.txt", "pyproject.toml", "package.json"}
_REQUEST_TIMEOUT = 20  # seconds


# ── Manifest parsing ─────────────────────────────────────────────────────────


def parse_requirements_txt(text: str) -> list[tuple[str, str]]:
    """Parse a ``requirements.txt`` blob into ``(name, version)`` pairs.

    Only pinned (``==``) and version-range-bounded (``>=``, ``<=``, ``>``,
    ``<``, ``~=``) specs with an explicit version are returned; unpinned or
    VCS/url specs are skipped. Comments and blank lines are ignored.

    Returns:
        List of ``(package_name, version_string)`` tuples.
    """
    deps: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # strip environment markers and extras
        line = line.split(";", 1)[0].strip()
        line = re.sub(r"\[.*?\]", "", line).strip()
        # match name + comparator + version
        m = re.match(
            r"^([A-Za-z0-9_.\-]+)\s*(==|~=|>=|<=|>|<)\s*([A-Za-z0-9_.\-+!*]+)",
            line,
        )
        if m:
            name, _op, version = m.group(1), m.group(2), m.group(3)
            # normalize: for ~=, >=, > take the version as-is (lower bound)
            deps.append((name, version))
    return deps


def parse_pyproject_toml(text: str) -> list[tuple[str, str]]:
    """Parse a ``pyproject.toml`` blob into ``(name, version)`` pairs.

    Reads ``[project]`` and ``[tool.poetry]`` dependency tables. Only specs
    with an explicit pinned version (``==``) or a poetry-style pinned string
    (``"1.2.3"``) are returned.

    Returns:
        List of ``(package_name, version_string)`` tuples.
    """
    try:
        import tomllib  # Python 3.11+ stdlib
    except ModuleNotFoundError:  # pragma: no cover
        return _parse_pyproject_regex(text)

    data = tomllib.loads(text)
    deps: list[tuple[str, str]] = []

    # PEP 621 [project.dependencies]
    project = data.get("project", {})
    for spec in project.get("dependencies", []) or []:
        parsed = _parse_pep621_spec(spec)
        if parsed:
            deps.append(parsed)
    # optional-dependencies groups
    for group in (project.get("optional-dependencies", {}) or {}).values():
        for spec in group or []:
            parsed = _parse_pep621_spec(spec)
            if parsed:
                deps.append(parsed)

    # poetry [tool.poetry.dependencies]
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies", {}) or {}).items():
        if name.lower() in {"python", "python3"}:
            continue
        version = _parse_poetry_spec(spec)
        if version:
            deps.append((name, version))

    return deps


def _parse_pyproject_regex(text: str) -> list[tuple[str, str]]:
    """Fallback regex parser for pyproject.toml when tomllib is unavailable."""
    deps: list[tuple[str, str]] = []
    # match: name = "1.2.3"  or  name = {version = "1.2.3"}
    for m in re.finditer(
        r'^([A-Za-z0-9_.\-]+)\s*=\s*"([0-9][A-Za-z0-9_.\-+!*]*)"',
        text,
        re.MULTILINE,
    ):
        name, version = m.group(1), m.group(2)
        if name.lower() in {"python", "python3", "name", "version", "description"}:
            continue
        deps.append((name, version))
    return deps


def _parse_pep621_spec(spec: str) -> tuple[str, str] | None:
    """Parse a PEP 621 dependency spec like ``requests>=2.20`` / ``requests==2.31``.

    Returns ``(name, version)`` or ``None`` if not explicitly versioned.
    """
    spec = spec.split(";")[0].strip()
    spec = re.sub(r"\[.*?\]", "", spec).strip()
    m = re.match(
        r"^([A-Za-z0-9_.\-]+)\s*(==|~=|>=|<=|>|<)\s*([A-Za-z0-9_.\-+!*]+)",
        spec,
    )
    if m:
        return (m.group(1), m.group(3))
    return None


def _parse_poetry_spec(spec: Any) -> str | None:
    """Parse a poetry dependency spec value (str, list, or dict)."""
    if isinstance(spec, str):
        # "1.2.3" or "^1.2.3" or ">=1.2"
        m = re.search(r"([0-9][A-Za-z0-9_.\-+!*]*)", spec)
        return m.group(1) if m else None
    if isinstance(spec, dict):
        v = spec.get("version")
        if v:
            m = re.search(r"([0-9][A-Za-z0-9_.\-+!*]*)", str(v))
            return m.group(1) if m else None
    return None


def parse_package_json(text: str) -> list[tuple[str, str]]:
    """Parse a ``package.json`` blob into ``(name, version)`` pairs.

    Only exact semver pins (``1.2.3``) are returned; ranges (``^``, ``~``,
    ``>=``, ``*``) are skipped because OSV needs a concrete version.

    Returns:
        List of ``(package_name, version_string)`` tuples.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps: list[tuple[str, str]] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key, {}) or {}
        for name, spec in section.items():
            raw = str(spec).strip()
            # skip range specs: ^ ~ >= <= > < * x.. || hyphen-ranges
            if not raw or raw[0] in "^~><=*" or raw.startswith("x") or "||" in raw or " - " in raw:
                continue
            # strip optional leading "v"
            clean = raw.lstrip("v").strip()
            # only accept concrete semver-ish versions (no wildcards)
            if "x" in clean.lower() or "*" in clean:
                continue
            if re.match(r"^[0-9]+(\.[0-9]+){0,2}([-+][A-Za-z0-9._-]+)?$", clean):
                deps.append((name, clean))
    return deps


def parse_manifest(path: str) -> list[tuple[str, str, str]]:
    """Parse a dependency manifest file.

    Auto-detects the ecosystem from the filename.

    Args:
        path: Path to requirements.txt, pyproject.toml or package.json.

    Returns:
        List of ``(name, version, ecosystem)`` tuples.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the manifest type is unsupported.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Manifest file not found: {path}")
    fname = p.name.lower()
    text = p.read_text(encoding="utf-8", errors="ignore")
    if fname == "requirements.txt":
        return [(n, v, "PyPI") for n, v in parse_requirements_txt(text)]
    if fname == "pyproject.toml":
        return [(n, v, "PyPI") for n, v in parse_pyproject_toml(text)]
    if fname == "package.json":
        return [(n, v, "npm") for n, v in parse_package_json(text)]
    raise ValueError(
        f"Unsupported manifest: {fname}. Supported: {sorted(SUPPORTED_MANIFESTS)}"
    )


# ── OSV.dev querying ─────────────────────────────────────────────────────────


def query_osv(
    name: str,
    ecosystem: str,
    version: str,
    timeout: int = _REQUEST_TIMEOUT,
) -> list[dict[str, Any]]:
    """Query OSV.dev for vulnerabilities affecting a single package version.

    Args:
        name: Package name.
        ecosystem: Ecosystem identifier (``PyPI`` or ``npm``).
        version: Package version string.
        timeout: HTTP timeout in seconds.

    Returns:
        List of vulnerability dicts (as returned by OSV, filtered to the
        ``vulns`` array). Empty list if no vulnerabilities or request fails.
    """
    payload = json.dumps(
        {"package": {"name": name, "ecosystem": ecosystem}, "version": version}
    ).encode("utf-8")
    req = urllib.request.Request(
        OSV_QUERY_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="ignore")
    data = json.loads(body)
    return data.get("vulns", []) or []


def summarize_vuln(vuln: dict[str, Any], name: str, version: str) -> dict[str, Any]:
    """Reduce a raw OSV vulnerability entry to a compact summary dict."""
    vid = vuln.get("id", "unknown")
    aliases = vuln.get("aliases", []) or []
    summary = vuln.get("summary", "") or vuln.get("details", "")[:200]
    # severity
    severity = "UNKNOWN"
    sev_entries = vuln.get("severity", []) or []
    if sev_entries:
        severity = sev_entries[0].get("score", "UNKNOWN")
    # fixed versions: scan all affected ranges for a fixed event
    fixed: list[str] = []
    for aff in vuln.get("affected", []) or []:
        if aff.get("package", {}).get("name", "").lower() != name.lower():
            continue
        for rng in aff.get("ranges", []) or []:
            for ev in rng.get("events", []) or []:
                if "fixed" in ev:
                    fixed.append(str(ev["fixed"]))
    references = [r.get("url") for r in (vuln.get("references", []) or []) if r.get("url")]
    return {
        "id": vid,
        "aliases": aliases,
        "package": name,
        "version": version,
        "severity": severity,
        "summary": summary.strip(),
        "fixed_versions": sorted(set(fixed)),
        "references": references[:5],
    }


# ── Tool ─────────────────────────────────────────────────────────────────────


@ToolRegistry.register
class SecurityScannerTool(BaseTool):
    """CVE / vulnerability scanner backed by the OSV.dev database.

    Either scan a single package (``name`` + ``ecosystem`` + ``version``) or
    scan a dependency manifest file (``file``) to check every pinned
    dependency in one call.
    """

    name = "security_scan"
    description = (
        "Scan packages or dependency manifests for known CVEs/vulnerabilities "
        "via the OSV.dev database (no API key). Params: "
        "name (str) + ecosystem (PyPI|npm) + version (str) for single package, "
        "OR file (str) path to requirements.txt/pyproject.toml/package.json."
    )
    parameters = {
        "name": {"type": "string", "description": "Package name (single-package mode)"},
        "ecosystem": {
            "type": "string",
            "description": "Ecosystem: PyPI or npm",
            "default": "PyPI",
        },
        "version": {"type": "string", "description": "Package version (single-package mode)"},
        "file": {
            "type": "string",
            "description": "Path to a dependency manifest (file mode)",
        },
    }

    def execute(
        self,
        name: str = "",
        ecosystem: str = "PyPI",
        version: str = "",
        file: str = "",
        **_: Any,
    ) -> ToolResult:
        """Run the vulnerability scan."""
        # ── file mode ────────────────────────────────────────────────────────
        if file:
            try:
                targets = parse_manifest(file)
            except (FileNotFoundError, ValueError) as exc:
                return ToolResult(success=False, data=None, error=str(exc))
            if not targets:
                return ToolResult(
                    success=True,
                    data={"scanned": 0, "vulnerabilities": [], "note": "no pinned deps found"},
                    error="",
                )
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            for pkg_name, pkg_ver, eco in targets:
                try:
                    vulns = query_osv(pkg_name, eco, pkg_ver)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{pkg_name}@{pkg_ver}: {exc}")
                    continue
                for v in vulns:
                    results.append(summarize_vuln(v, pkg_name, pkg_ver))
            return ToolResult(
                success=True,
                data={
                    "scanned": len(targets),
                    "vulnerabilities": results,
                    "error_count": len(errors),
                    "errors": errors,
                },
                error="",
            )

        # ── single-package mode ──────────────────────────────────────────────
        if not name or not version:
            return ToolResult(
                success=False,
                data=None,
                error="Provide either `file` or both `name`+`version` (+ optional `ecosystem`)",
            )
        if ecosystem not in SUPPORTED_ECOSYSTEMS:
            return ToolResult(
                success=False,
                data=None,
                error=f"Unsupported ecosystem '{ecosystem}'. Use: {sorted(SUPPORTED_ECOSYSTEMS)}",
            )
        try:
            vulns = query_osv(name, ecosystem, version)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, data=None, error=f"OSV query failed: {exc}")
        summaries = [summarize_vuln(v, name, version) for v in vulns]
        return ToolResult(
            success=True,
            data={
                "package": name,
                "ecosystem": ecosystem,
                "version": version,
                "vulnerability_count": len(summaries),
                "vulnerabilities": summaries,
            },
            error="",
        )
