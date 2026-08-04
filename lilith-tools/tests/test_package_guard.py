"""Tests for the package_guard module (AgentShield-style pre-install gate).

All tests run with ``enable_cve_lookup=False`` (set in
``PackageGuard.__init__`` defaults) OR with OSV mocked out, so no
network is required.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from lilith_tools.base import ToolResult
from lilith_tools.package_guard import (
    DEFAULT_BLACKLIST,
    GuardConfig,
    GuardResult,
    GuardVerdict,
    LicensePolicy,
    PackageGuard,
    PackageGuardTool,
    PolicyHit,
    render_json,
    render_report,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _guard(**kwargs: Any) -> PackageGuard:
    """Construct a guard with CVE lookup disabled by default.

    Tests that need OSV behaviour pass a custom config or patch
    ``lilith_tools.security.query_osv``.
    """
    cfg = GuardConfig(enable_cve_lookup=False)
    cfg_kwargs = {"config": cfg}
    cfg_kwargs.update(kwargs)
    return PackageGuard(**cfg_kwargs)


# ── GuardVerdict enum ────────────────────────────────────────────────────────


class TestGuardVerdict:
    def test_severity_ordering(self):
        assert GuardVerdict.ALLOW < GuardVerdict.WARN
        assert GuardVerdict.WARN < GuardVerdict.QUARANTINE
        assert GuardVerdict.QUARANTINE < GuardVerdict.DENY

    def test_sortable_max(self):
        v = max([GuardVerdict.ALLOW, GuardVerdict.DENY, GuardVerdict.WARN])
        assert v == GuardVerdict.DENY

    def test_value_is_string(self):
        assert GuardVerdict.ALLOW.value == "allow"
        assert isinstance(GuardVerdict.DENY, str)  # str enum


# ── PolicyHit dataclass ──────────────────────────────────────────────────────


class TestPolicyHit:
    def test_to_dict_minimal(self):
        h = PolicyHit(layer="cve", severity="high", message="x")
        d = h.to_dict()
        assert d["layer"] == "cve"
        assert d["severity"] == "high"
        assert d["message"] == "x"
        assert d["cve_id"] is None
        assert d["references"] == []
        assert d["metadata"] == {}

    def test_to_dict_full(self):
        h = PolicyHit(
            layer="cve",
            severity="critical",
            message="bad",
            cve_id="CVE-2020-9999",
            references=["https://example.com"],
            metadata={"k": 1},
        )
        d = h.to_dict()
        assert d["cve_id"] == "CVE-2020-9999"
        assert d["references"] == ["https://example.com"]
        assert d["metadata"] == {"k": 1}


# ── GuardResult dataclass ────────────────────────────────────────────────────


class TestGuardResult:
    def test_allowed_property(self):
        r = GuardResult(
            package="x", version="1", ecosystem="PyPI",
            verdict=GuardVerdict.ALLOW, reason="ok",
        )
        assert r.allowed is True
        assert r.blocked is False
        assert r.needs_override is False

    def test_quarantine_needs_override(self):
        r = GuardResult(
            package="x", version="1", ecosystem="PyPI",
            verdict=GuardVerdict.QUARANTINE, reason="cve",
        )
        assert r.allowed is False
        assert r.blocked is False
        assert r.needs_override is True

    def test_deny_blocked(self):
        r = GuardResult(
            package="x", version="1", ecosystem="PyPI",
            verdict=GuardVerdict.DENY, reason="bad",
        )
        assert r.allowed is False
        assert r.blocked is True
        assert r.needs_override is False

    def test_to_dict_round_trip(self):
        r = GuardResult(
            package="x", version="1", ecosystem="PyPI",
            verdict=GuardVerdict.WARN, reason="low trust",
            trust_score=42, layers_run=["blacklist", "trust"],
            hits=[PolicyHit(layer="trust", severity="low", message="x")],
        )
        d = r.to_dict()
        assert d["package"] == "x"
        assert d["verdict"] == "warn"
        assert d["trust_score"] == 42
        assert d["layers_run"] == ["blacklist", "trust"]
        assert len(d["hits"]) == 1


# ── Blacklist layer ──────────────────────────────────────────────────────────


class TestBlacklistLayer:
    def test_typosquat_blocked(self):
        g = _guard()
        r = g.check("reqests", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY
        assert any(h.layer == "blacklist" for h in r.hits)

    def test_legitimate_package_passes_blacklist(self):
        g = _guard(license_overrides={"requests": "MIT"})
        r = g.check("requests", "2.31.0", ecosystem="PyPI")
        assert not any(h.layer == "blacklist" for h in r.hits)

    def test_extra_blacklist(self):
        cfg = GuardConfig(
            enable_cve_lookup=False,
            extra_blacklist={"my-bad-pkg"},
        )
        g = PackageGuard(
            config=cfg, license_overrides={"my-bad-pkg": "MIT"},
        )
        r = g.check("my-bad-pkg", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY

    def test_case_insensitive(self):
        g = _guard()
        r = g.check("REQESTS", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY

    def test_custom_pattern(self):
        cfg = GuardConfig(
            enable_cve_lookup=False,
            blacklist_patterns=[r"^evil-.*"],
        )
        g = PackageGuard(config=cfg)
        r = g.check("evil-toolkit", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY
        assert any(
            h.layer == "blacklist" and "pattern" in h.message
            for h in r.hits
        )

    def test_invalid_pattern_does_not_crash(self):
        cfg = GuardConfig(
            enable_cve_lookup=False,
            blacklist_patterns=[r"(invalid"],
        )
        g = PackageGuard(config=cfg, license_overrides={"requests": "MIT"})
        r = g.check("requests", "2.31.0", ecosystem="PyPI")
        # Invalid pattern is skipped, rest of layers run normally
        assert r.verdict == GuardVerdict.ALLOW
        assert not any(h.layer == "blacklist" for h in r.hits)


# ── License layer ────────────────────────────────────────────────────────────


class TestLicenseLayer:
    def test_unknown_license_warns(self):
        g = _guard()
        r = g.check("requests", "2.31.0", ecosystem="PyPI")
        warn_hits = [h for h in r.hits if h.layer == "license"]
        assert len(warn_hits) == 1
        assert warn_hits[0].severity == "low"

    def test_known_mit_license_passes(self):
        g = _guard(license_overrides={"requests": "MIT"})
        r = g.check("requests", "2.31.0", ecosystem="PyPI")
        assert not any(h.layer == "license" for h in r.hits)

    def test_gpl_license_denies(self):
        g = _guard(license_overrides={"somepkg": "GPL-3.0"})
        r = g.check("somepkg", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY
        lic_hits = [h for h in r.hits if h.layer == "license"]
        assert lic_hits[0].severity == "high"

    def test_lgpl_warns(self):
        g = _guard(license_overrides={"somepkg": "LGPL-2.1"})
        r = g.check("somepkg", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.WARN

    def test_agpl_denies(self):
        g = _guard(license_overrides={"somepkg": "AGPL-3.0"})
        r = g.check("somepkg", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY

    def test_custom_license_policy(self):
        cfg = GuardConfig(
            enable_cve_lookup=False,
            license_policy={"CUSTOM-LIC": LicensePolicy.DENY},
        )
        g = PackageGuard(
            config=cfg, license_overrides={"x": "CUSTOM-LIC"},
        )
        r = g.check("x", "1.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY


# ── Trust layer ──────────────────────────────────────────────────────────────


class TestTrustLayer:
    def test_known_high_trust(self):
        g = _guard(license_overrides={"requests": "MIT"})
        r = g.check("requests", "2.31.0", ecosystem="PyPI")
        assert r.trust_score == 95
        assert not any(h.layer == "trust" for h in r.hits)

    def test_unknown_package_default_score(self):
        g = _guard(license_overrides={"nonexistent-pkg-xyz": "MIT"})
        r = g.check(
            "nonexistent-pkg-xyz", "1.0", ecosystem="PyPI",
        )
        assert r.trust_score == 50
        info_hits = [h for h in r.hits if h.layer == "trust"]
        assert len(info_hits) == 1
        assert info_hits[0].severity == "info"

    def test_below_threshold_quarantines(self):
        from lilith_tools import package_guard as pg
        original = pg.DEFAULT_TRUST.copy()
        pg.DEFAULT_TRUST["low-trust-pkg"] = {"score": 10}
        try:
            cfg = GuardConfig(enable_cve_lookup=False)
            g = PackageGuard(config=cfg, license_overrides={"low-trust-pkg": "MIT"})
            r = g.check("low-trust-pkg", "1.0", ecosystem="PyPI")
            assert r.trust_score == 10
            trust_hits = [h for h in r.hits if h.layer == "trust"]
            assert len(trust_hits) == 1
            assert trust_hits[0].severity == "medium"
            assert r.verdict == GuardVerdict.QUARANTINE
        finally:
            pg.DEFAULT_TRUST.clear()
            pg.DEFAULT_TRUST.update(original)


# ── CVE layer (mocked OSV) ───────────────────────────────────────────────────


class TestCVELayer:
    def test_no_vulns_when_osv_returns_empty(self):
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"requests": "MIT"})
        with patch("lilith_tools.security.query_osv", return_value=[]):
            r = g.check("requests", "2.31.0", ecosystem="PyPI")
        cve_hits = [h for h in r.hits if h.layer == "cve"]
        assert cve_hits == []
        assert r.verdict == GuardVerdict.ALLOW

    def test_critical_cve_denies(self):
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"requests": "MIT"})
        fake_vuln = {
            "id": "CVE-2020-9999",
            "summary": "bad bug",
            "severity": [{"type": "CVSS_V3", "score": "9.8"}],
            "references": [{"url": "https://example.com"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("requests", "2.20.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY
        cve_hits = [h for h in r.hits if h.layer == "cve"]
        assert cve_hits[0].cve_id == "CVE-2020-9999"

    def test_high_cve_quarantines(self):
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"flask": "MIT"})
        fake_vuln = {
            "id": "CVE-2020-1111",
            "summary": "h bug",
            "severity": [{"type": "CVSS_V3", "score": "7.5"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.QUARANTINE
        assert r.needs_override is True

    def test_medium_cve_warns(self):
        # In default config, medium CVEs map to tier=warn → verdict=WARN
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"flask": "MIT"})
        fake_vuln = {
            "id": "CVE-2020-2222",
            "summary": "m bug",
            "severity": [{"type": "CVSS_V3", "score": "5.0"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.WARN

    def test_medium_cve_quarantines_when_configured(self):
        # Config that demotes medium to quarantine
        cfg = GuardConfig(
            enable_cve_lookup=True,
            cve_severity_warn=(),
            cve_severity_quarantine=("high", "medium"),
        )
        g = PackageGuard(config=cfg, license_overrides={"flask": "MIT"})
        fake_vuln = {
            "id": "CVE-2020-2222",
            "summary": "m bug",
            "severity": [{"type": "CVSS_V3", "score": "5.0"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.QUARANTINE

    def test_cvss_vector_string_high(self):
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"flask": "MIT"})
        # C:H/I:H/A:H → critical
        fake_vuln = {
            "id": "CVE-2024-X",
            "summary": "vec bug",
            "severity": [
                {"type": "CVSS_V3",
                 "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
            ],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        cve_hits = [h for h in r.hits if h.layer == "cve"]
        assert cve_hits[0].severity == "critical"
        assert r.verdict == GuardVerdict.DENY

    def test_cvss_vector_string_medium(self):
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"flask": "MIT"})
        # C:L/I:N/A:N → low
        fake_vuln = {
            "id": "CVE-2024-Y",
            "summary": "vec bug",
            "severity": [
                {"type": "CVSS_V3",
                 "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:N/A:N"},
            ],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        cve_hits = [h for h in r.hits if h.layer == "cve"]
        assert cve_hits[0].severity == "low"
        # low cve → warn (no quarantine)
        assert r.verdict == GuardVerdict.WARN

    def test_osv_error_fail_open(self):
        cfg = GuardConfig(enable_cve_lookup=True)
        g = PackageGuard(config=cfg, license_overrides={"requests": "MIT"})
        with patch(
            "lilith_tools.security.query_osv",
            side_effect=Exception("net"),
        ):
            r = g.check("requests", "2.31.0", ecosystem="PyPI")
        cve_hits = [h for h in r.hits if h.layer == "cve"]
        assert len(cve_hits) == 1
        assert cve_hits[0].severity == "info"
        assert r.verdict != GuardVerdict.DENY

    def test_osv_error_fail_closed(self):
        # PackageGuard.strict() → fail_closed_on_cve_error=True
        g = PackageGuard.strict()
        g._license_overrides["requests"] = "MIT"
        with patch(
            "lilith_tools.security.query_osv",
            side_effect=Exception("net"),
        ):
            r = g.check("requests", "2.31.0", ecosystem="PyPI")
        cve_hits = [h for h in r.hits if h.layer == "cve"]
        assert cve_hits[0].severity == "medium"
        assert r.verdict == GuardVerdict.QUARANTINE

    def test_disabled_cve_lookup(self):
        cfg = GuardConfig(enable_cve_lookup=False)
        g = PackageGuard(config=cfg, license_overrides={"requests": "MIT"})
        with patch("lilith_tools.security.query_osv") as mock:
            r = g.check("requests", "2.31.0", ecosystem="PyPI")
            mock.assert_not_called()
        assert not any(h.layer == "cve" for h in r.hits)


# ── Aggregation: worst-of ────────────────────────────────────────────────────


class TestAggregation:
    def test_critical_beats_high(self):
        g = _guard()
        # Manually compose a scenario: blacklisted + high-CVE
        with patch("lilith_tools.security.query_osv", return_value=[]):
            r = g.check("reqests", "1.0", ecosystem="PyPI")
        # Blacklist is critical → DENY
        assert r.verdict == GuardVerdict.DENY

    def test_clean_package_yields_allow(self):
        g = _guard(license_overrides={"requests": "MIT"})
        with patch("lilith_tools.security.query_osv", return_value=[]):
            r = g.check("requests", "2.31.0", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.ALLOW
        assert r.reason == "all policy layers passed"

    def test_layers_run_recorded(self):
        g = _guard(license_overrides={"requests": "MIT"})
        r = g.check("requests", "2.31.0", ecosystem="PyPI")
        assert "blacklist" in r.layers_run
        assert "license" in r.layers_run
        assert "trust" in r.layers_run
        assert "cve" in r.layers_run


# ── Mode factories ───────────────────────────────────────────────────────────


class TestModes:
    def test_strict_denies_high_cve(self):
        g = PackageGuard.strict()
        g._license_overrides["flask"] = "MIT"
        fake_vuln = {
            "id": "CVE-2020-X",
            "summary": "high",
            "severity": [{"type": "CVSS_V3", "score": "8.0"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY

    def test_permissive_warns_on_high_cve(self):
        g = PackageGuard.permissive()
        g._license_overrides["flask"] = "MIT"
        fake_vuln = {
            "id": "CVE-2020-X",
            "summary": "high",
            "severity": [{"type": "CVSS_V3", "score": "8.0"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        # In permissive mode, high → warn (cve_severity_quarantine=())
        assert r.verdict == GuardVerdict.WARN

    def test_permissive_denies_critical(self):
        g = PackageGuard.permissive()
        g._license_overrides["flask"] = "MIT"
        fake_vuln = {
            "id": "CVE-2020-X",
            "summary": "crit",
            "severity": [{"type": "CVSS_V3", "score": "9.9"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            r = g.check("flask", "0.1", ecosystem="PyPI")
        assert r.verdict == GuardVerdict.DENY


# ── Manifest mode ────────────────────────────────────────────────────────────


class TestManifestMode:
    def test_manifest_denies_blacklisted(self, tmp_path):
        f = tmp_path / "requirements.txt"
        f.write_text("reqests==1.0\nrequests==2.31.0\n")
        cfg = GuardConfig(enable_cve_lookup=False)
        g = PackageGuard(config=cfg, license_overrides={"requests": "MIT"})
        with patch("lilith_tools.security.query_osv", return_value=[]):
            results = g.check_manifest(str(f))
        assert len(results) == 2
        verdicts = {r.package: r.verdict for r in results}
        assert verdicts["reqests"] == GuardVerdict.DENY
        assert verdicts["requests"] == GuardVerdict.ALLOW

    def test_manifest_missing_file(self, tmp_path):
        g = _guard()
        # Unsupported filename = ValueError, parsed as a single deny result
        results = g.check_manifest(str(tmp_path / "missing.txt"))
        assert len(results) == 1
        assert results[0].verdict == GuardVerdict.DENY
        assert "parse failed" in results[0].reason

    def test_manifest_truly_missing_file(self, tmp_path):
        g = _guard()
        # Well-named but absent → FileNotFoundError → still one deny
        results = g.check_manifest(str(tmp_path / "requirements.txt"))
        assert len(results) == 1
        assert results[0].verdict == GuardVerdict.DENY
        assert "parse failed" in results[0].reason


# ── Reporting helpers ────────────────────────────────────────────────────────


class TestReporting:
    def test_render_report_includes_verdict_icons(self):
        g = _guard(license_overrides={"requests": "MIT"})
        with patch("lilith_tools.security.query_osv", return_value=[]):
            r1 = g.check("requests", "2.31.0", ecosystem="PyPI")
            r2 = g.check("reqests", "1.0", ecosystem="PyPI")
        out = render_report([r1, r2])
        assert "✅" in out  # ALLOW
        assert "⛔" in out  # DENY
        assert "Summary:" in out
        assert "1 hard-denied" in out

    def test_render_json_is_valid(self):
        g = _guard(license_overrides={"requests": "MIT"})
        with patch("lilith_tools.security.query_osv", return_value=[]):
            r = g.check("requests", "2.31.0", ecosystem="PyPI")
        out = render_json([r])
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert parsed[0]["package"] == "requests"


# ── Tool registration ────────────────────────────────────────────────────────


class TestPackageGuardTool:
    def test_registered(self):
        from lilith_tools.registry import ToolRegistry
        # Other tests may have cleared the registry; trigger (re-)import
        # to re-run the @ToolRegistry.register decorator.
        import importlib
        import lilith_tools.package_guard as _pg
        importlib.reload(_pg)
        tool_cls = ToolRegistry.get("package_guard")
        # `importlib.reload` rebinds the class in the module namespace,
        # so the locally-imported PackageGuardTool reference may differ
        # by identity. Compare by qualname + module instead.
        assert tool_cls is not None
        assert tool_cls.__module__ == PackageGuardTool.__module__
        assert tool_cls.__qualname__ == PackageGuardTool.__qualname__

    def test_tool_metadata(self):
        t = PackageGuardTool()
        assert t.name == "package_guard"
        assert "package" in t.description.lower()
        assert "name" in t.parameters
        assert "file" in t.parameters

    def test_execute_single_package(self):
        t = PackageGuardTool()
        with patch("lilith_tools.security.query_osv", return_value=[]):
            result = t.execute(
                name="requests", version="2.31.0", ecosystem="PyPI",
            )
        assert isinstance(result, ToolResult)
        assert result.success is True
        # UNKNOWN license by default → WARN verdict
        assert result.data["verdict"] in ("allow", "warn")
        assert result.data["allowed"] is True
        assert result.data["blocked"] is False

    def test_execute_single_package_blacklisted(self):
        t = PackageGuardTool()
        result = t.execute(name="reqests", version="1.0", ecosystem="PyPI")
        assert result.success is True
        assert result.data["verdict"] == "deny"
        assert result.data["blocked"] is True

    def test_execute_requires_name_or_file(self):
        t = PackageGuardTool()
        result = t.execute(name="", version="", ecosystem="PyPI")
        assert result.success is False
        assert "Provide either" in result.error

    def test_execute_unsupported_ecosystem(self):
        t = PackageGuardTool()
        result = t.execute(
            name="foo", version="1.0", ecosystem="Maven",
        )
        assert result.success is False
        assert "Unsupported ecosystem" in result.error

    def test_execute_manifest_mode(self, tmp_path):
        f = tmp_path / "requirements.txt"
        f.write_text("requests==2.31.0\nreqests==1.0\n")
        t = PackageGuardTool()
        with patch("lilith_tools.security.query_osv", return_value=[]):
            result = t.execute(file=str(f))
        assert result.success is True
        assert result.data["scanned"] == 2
        assert result.data["denied"] == 1
        assert result.data["worst_verdict"] == "deny"
        assert "report" in result.data

    def test_execute_manifest_missing_file(self, tmp_path):
        t = PackageGuardTool()
        # Tool returns success=True with a denied result inside data when
        # the manifest is unreadable (deny-by-default for unparsable input).
        result = t.execute(file=str(tmp_path / "requirements.txt"))
        assert result.success is True
        assert result.data["scanned"] == 1
        assert result.data["denied"] == 1
        assert result.data["worst_verdict"] == "deny"

    def test_execute_strict_mode(self):
        t = PackageGuardTool()
        fake_vuln = {
            "id": "CVE-2020-X",
            "summary": "h",
            "severity": [{"type": "CVSS_V3", "score": "8.0"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            result = t.execute(
                name="flask", version="0.1", ecosystem="PyPI",
                mode="strict",
            )
        assert result.success is True
        # strict → high CVE = deny
        assert result.data["verdict"] == "deny"

    def test_execute_permissive_mode(self):
        t = PackageGuardTool()
        fake_vuln = {
            "id": "CVE-2020-X",
            "summary": "h",
            "severity": [{"type": "CVSS_V3", "score": "8.0"}],
        }
        with patch("lilith_tools.security.query_osv", return_value=[fake_vuln]):
            result = t.execute(
                name="flask", version="0.1", ecosystem="PyPI",
                mode="permissive",
            )
        assert result.success is True
        # permissive → high CVE = warn
        assert result.data["verdict"] == "warn"


# ── Default seed sanity ──────────────────────────────────────────────────────


class TestSeedData:
    def test_default_blacklist_is_nonempty(self):
        assert len(DEFAULT_BLACKLIST) > 5
        # Sanity: contains known typosquats
        assert "reqests" in DEFAULT_BLACKLIST
        assert "djiango" in DEFAULT_BLACKLIST

    def test_default_blacklist_does_not_block_popular(self):
        for pkg in ("requests", "flask", "django", "numpy", "pandas"):
            assert pkg not in DEFAULT_BLACKLIST
