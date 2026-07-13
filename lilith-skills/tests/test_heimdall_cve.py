"""Tests for heimdall_auditor audit_dependencies and audit_requirements_file."""
import pytest
from pathlib import Path

from lilith_skills.heimdall_auditor import (
    audit_dependencies,
    audit_requirements_file,
)


# ── audit_dependencies ─────────────────────────────────────────


def test_audit_safe_deps():
    result = audit_dependencies(["requests==2.31.0", "click>=8.0"])
    assert result["summary"]["vulnerable_count"] == 0
    assert "requests==2.31.0" in result["safe"]


def test_audit_vulnerable_dep():
    result = audit_dependencies(["log4j-core<2.10.0"])
    assert result["summary"]["vulnerable_count"] >= 1
    assert any(v["cve_id"] == "CVE-2021-44228" for v in result["vulnerable"])


def test_audit_mixed():
    result = audit_dependencies([
        "log4j-core<2.10.0",  # vulnerable
        "requests==2.31.0",    # safe
    ])
    assert result["summary"]["total"] == 2
    assert result["summary"]["vulnerable_count"] >= 1
    assert result["summary"]["safe_count"] >= 1


def test_audit_critical_count():
    result = audit_dependencies(["log4j-core<2.10.0"])
    summary = result["summary"]
    # Log4Shell is critical (CVSS 10.0)
    assert summary["critical"] >= 1


def test_audit_with_custom_db():
    from lilith_tools.cve import CVEDatabase, CVEEntry

    custom = CVEDatabase([
        CVEEntry(
            cve_id="CVE-CUSTOM-1",
            title="Custom vuln",
            severity="medium",
            cvss_score=5.0,
            description="x",
            affected_packages=["my-package<1.0"],
        ),
    ])
    result = audit_dependencies(["my-package==0.5"], cve_db=custom)
    assert any(v["cve_id"] == "CVE-CUSTOM-1" for v in result["vulnerable"])


def test_audit_returns_correct_keys():
    result = audit_dependencies([])
    assert "safe" in result
    assert "vulnerable" in result
    assert "summary" in result
    assert "total" in result["summary"]
    assert "safe_count" in result["summary"]
    assert "vulnerable_count" in result["summary"]


# ── audit_requirements_file ─────────────────────────────────────


def test_audit_requirements_file(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        "# Production deps\n"
        "log4j-core==2.10.0  # vulnerable\n"
        "requests==2.31.0\n"
        "\n"
        "# More\n"
        "click>=8.0\n",
        encoding="utf-8",
    )
    result = audit_requirements_file(req_file)
    assert result["summary"]["total"] == 3
    # log4j-core<2.15.0 should match Log4Shell
    assert result["summary"]["vulnerable_count"] >= 1


def test_audit_requirements_strips_comments(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        "requests==2.31.0  # this is safe\n"
        "click>=8.0  # also safe\n",
        encoding="utf-8",
    )
    result = audit_requirements_file(req_file)
    assert result["summary"]["safe_count"] == 2


def test_audit_requirements_skips_options(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        "--index-url https://example.com\n"
        "requests==2.31.0\n",
        encoding="utf-8",
    )
    result = audit_requirements_file(req_file)
    assert result["summary"]["total"] == 1  # only the actual dep


def test_audit_requirements_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        audit_requirements_file(tmp_path / "nonexistent.txt")
