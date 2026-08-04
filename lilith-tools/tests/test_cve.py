"""Tests for lilith_tools.cve."""
import pytest

from lilith_tools.cve import CVEDatabase, CVEEntry


@pytest.fixture
def db() -> CVEDatabase:
    return CVEDatabase()


# ── Basic lookups ──────────────────────────────────────────────


def test_db_count(db: CVEDatabase):
    # Seed catalog has at least 8 CVEs
    assert db.count() >= 8


def test_by_id_known(db: CVEDatabase):
    cve = db.by_id("CVE-2021-44228")
    assert cve is not None
    assert cve.title.startswith("Log4Shell")


def test_by_id_case_insensitive(db: CVEDatabase):
    cve = db.by_id("cve-2021-44228")
    assert cve is not None
    assert cve.cve_id == "CVE-2021-44228"


def test_by_id_unknown(db: CVEDatabase):
    assert db.by_id("CVE-9999-99999") is None


def test_by_severity(db: CVEDatabase):
    critical = db.by_severity("critical")
    assert len(critical) >= 1
    for c in critical:
        assert c.severity == "critical"


# ── Search ─────────────────────────────────────────────────────


def test_search_keyword(db: CVEDatabase):
    results = db.search("log4j")
    assert len(results) >= 1
    assert any("Log4j" in c.title for c in results)


def test_search_no_match(db: CVEDatabase):
    assert db.search("totally-bogus-query-xyz") == []


def test_search_by_title(db: CVEDatabase):
    results = db.search("Heartbleed")
    assert len(results) == 1
    assert "Heartbleed" in results[0].title


# ── Dependency matching ───────────────────────────────────────


def test_match_dependency_unsafe_version(db: CVEDatabase):
    # log4j-core<2.15.0 is vulnerable; an old version should match
    matches = db.match_dependency("log4j-core<2.10.0")
    assert len(matches) >= 1
    assert any(c.cve_id == "CVE-2021-44228" for c in matches)


def test_match_dependency_safe_version(db: CVEDatabase):
    # log4j-core 2.17.0 is patched; should not match
    matches = db.match_dependency("log4j-core==2.17.0")
    log4j_matches = [c for c in matches if c.cve_id == "CVE-2021-44228"]
    assert log4j_matches == []


def test_match_dependency_unconstrained(db: CVEDatabase):
    # No version specified → match if name aligns with any cve
    matches = db.match_dependency("log4j-core")
    log4j_matches = [c for c in matches if c.cve_id == "CVE-2021-44228"]
    assert len(log4j_matches) >= 1


def test_match_dependency_unrelated_package(db: CVEDatabase):
    # Unrelated package should not match anything
    matches = db.match_dependency("my-internal-tool==1.0")
    assert matches == []


# ── Stats ──────────────────────────────────────────────────────


def test_stats(db: CVEDatabase):
    s = db.stats()
    assert s["total"] >= 8
    assert "critical" in s
    assert s["critical"] >= 1


def test_all_returns_list(db: CVEDatabase):
    items = db.all()
    assert isinstance(items, list)
    assert len(items) >= 8


# ── CVEEntry dataclass ─────────────────────────────────────────


def test_cve_entry_to_dict():
    e = CVEEntry(
        cve_id="CVE-TEST-0001",
        title="Test",
        severity="low",
        cvss_score=2.0,
        description="x",
    )
    d = e.to_dict()
    assert d["cve_id"] == "CVE-TEST-0001"
    assert d["cvss_score"] == 2.0


def test_custom_cve_list():
    custom = [
        CVEEntry(cve_id="CVE-CUSTOM-1", title="X", severity="low", cvss_score=1.0, description="d"),
    ]
    db = CVEDatabase(custom)
    assert db.count() == 1
    assert db.by_id("CVE-CUSTOM-1") is not None
