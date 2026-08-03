"""Tests for the security / CVE vulnerability scanner tool."""

import json
import pytest
from unittest.mock import MagicMock, patch

from lilith_tools.base import ToolResult
from lilith_tools.security import (
    OSV_QUERY_URL,
    SecurityScannerTool,
    parse_manifest,
    parse_package_json,
    parse_pyproject_toml,
    parse_requirements_txt,
    query_osv,
    summarize_vuln,
)


# ── requirements.txt parsing ─────────────────────────────────────────────────


class TestParseRequirementsTxt:
    def test_pinned_exact(self):
        deps = parse_requirements_txt("requests==2.20.0\nnumpy==1.21.0\n")
        assert deps == [("requests", "2.20.0"), ("numpy", "1.21.0")]

    def test_ge_bound(self):
        deps = parse_requirements_txt("flask>=2.0.0\n")
        assert deps == [("flask", "2.0.0")]

    def test_tilde_bound(self):
        deps = parse_requirements_txt("pytest~=7.4.0\n")
        assert deps == [("pytest", "7.4.0")]

    def test_comments_and_blanks_ignored(self):
        text = "# top comment\n\nrequests==2.31.0  # inline comment\n\n# more\n"
        deps = parse_requirements_txt(text)
        assert deps == [("requests", "2.31.0")]

    def test_extras_stripped(self):
        deps = parse_requirements_txt("dask[complete]==2023.1.0\n")
        assert deps == [("dask", "2023.1.0")]

    def test_env_marker_stripped(self):
        deps = parse_requirements_txt("foo==1.0.0; python_version<'3.10'\n")
        assert deps == [("foo", "1.0.0")]

    def test_unpinned_skipped(self):
        deps = parse_requirements_txt("requests\nfastapi\n")
        assert deps == []

    def test_flag_lines_skipped(self):
        deps = parse_requirements_txt("-r other.txt\n--index-url=x\nrequests==1.0\n")
        assert deps == [("requests", "1.0")]

    def test_empty(self):
        assert parse_requirements_txt("") == []


# ── pyproject.toml parsing ───────────────────────────────────────────────────


class TestParsePyprojectToml:
    def test_pep621_dependencies(self):
        text = """
[project]
name = "mypkg"
dependencies = ["requests==2.20.0", "numpy>=1.21.0"]
"""
        deps = parse_pyproject_toml(text)
        assert ("requests", "2.20.0") in deps
        assert ("numpy", "1.21.0") in deps

    def test_optional_dependencies(self):
        text = """
[project]
dependencies = ["click==8.1.0"]
[project.optional-dependencies]
dev = ["pytest==7.4.0", "ruff>=0.1.0"]
"""
        deps = parse_pyproject_toml(text)
        assert ("click", "8.1.0") in deps
        assert ("pytest", "7.4.0") in deps
        assert ("ruff", "0.1.0") in deps

    def test_poetry_dependencies(self):
        text = """
[tool.poetry.dependencies]
python = "^3.11"
requests = "2.20.0"
fastapi = {version = "0.100.0"}
"""
        deps = parse_pyproject_toml(text)
        # python should be excluded
        names = [d[0] for d in deps]
        assert "python" not in names
        assert ("requests", "2.20.0") in deps
        assert ("fastapi", "0.100.0") in deps

    def test_unversioned_pep621_skipped(self):
        text = '[project]\ndependencies = ["requests"]\n'
        assert parse_pyproject_toml(text) == []

    def test_empty(self):
        assert parse_pyproject_toml("") == []


# ── package.json parsing ─────────────────────────────────────────────────────


class TestParsePackageJson:
    def test_exact_pins(self):
        text = json.dumps(
            {"dependencies": {"express": "4.18.0", "lodash": "4.17.21"}}
        )
        deps = parse_package_json(text)
        assert deps == [("express", "4.18.0"), ("lodash", "4.17.21")]

    def test_caret_ranges_skipped(self):
        text = json.dumps({"dependencies": {"express": "^4.18.0"}})
        assert parse_package_json(text) == []

    def test_tilde_ranges_skipped(self):
        text = json.dumps({"dependencies": {"express": "~4.18.0"}})
        assert parse_package_json(text) == []

    def test_star_skipped(self):
        text = json.dumps({"dependencies": {"express": "*"}})
        assert parse_package_json(text) == []

    def test_dev_dependencies(self):
        text = json.dumps(
            {"devDependencies": {"jest": "29.7.0", "eslint": "8.50.0"}}
        )
        deps = parse_package_json(text)
        assert ("jest", "29.7.0") in deps
        assert ("eslint", "8.50.0") in deps

    def test_invalid_json(self):
        assert parse_package_json("not json") == []

    def test_v_prefix_stripped(self):
        text = json.dumps({"dependencies": {"pkg": "v1.2.3"}})
        assert parse_package_json(text) == [("pkg", "1.2.3")]


# ── parse_manifest dispatch ──────────────────────────────────────────────────


class TestParseManifest:
    def test_requirements_txt(self, tmp_path):
        f = tmp_path / "requirements.txt"
        f.write_text("requests==2.20.0\n")
        result = parse_manifest(str(f))
        assert result == [("requests", "2.20.0", "PyPI")]

    def test_pyproject_toml(self, tmp_path):
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\ndependencies = ["click==8.1.0"]\n')
        result = parse_manifest(str(f))
        assert result == [("click", "8.1.0", "PyPI")]

    def test_package_json(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(json.dumps({"dependencies": {"express": "4.18.0"}}))
        result = parse_manifest(str(f))
        assert result == [("express", "4.18.0", "npm")]

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            parse_manifest("does_not_exist_xyz.txt")

    def test_unsupported_manifest(self, tmp_path):
        f = tmp_path / "Cargo.toml"
        f.write_text("[dependencies]\n")
        with pytest.raises(ValueError):
            parse_manifest(str(f))


# ── OSV query + summarize ────────────────────────────────────────────────────


class TestQueryOsv:
    def _mock_response(self, vulns):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"vulns": vulns}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_query_returns_vulns(self):
        vulns = [{"id": "CVE-2023-1234", "summary": "bad bug"}]
        with patch("lilith_tools.security.urllib.request.urlopen", return_value=self._mock_response(vulns)) as mock_open:
            result = query_osv("requests", "PyPI", "2.20.0")
        assert result == vulns
        # verify the request was POSTed to the OSV endpoint
        args, _ = mock_open.call_args
        req = args[0]
        assert req.full_url == OSV_QUERY_URL
        assert req.get_method() == "POST"

    def test_query_no_vulns(self):
        with patch("lilith_tools.security.urllib.request.urlopen", return_value=self._mock_response([])):
            result = query_osv("clean-pkg", "PyPI", "1.0.0")
        assert result == []

    def test_query_missing_vulns_key(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("lilith_tools.security.urllib.request.urlopen", return_value=mock_resp):
            result = query_osv("pkg", "PyPI", "1.0.0")
        assert result == []


class TestSummarizeVuln:
    def test_full_summary(self):
        vuln = {
            "id": "GHSA-abc1-2def-3ghi",
            "aliases": ["CVE-2023-1234"],
            "summary": "RCE in foo package",
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L"}],
            "affected": [
                {
                    "package": {"name": "foo", "ecosystem": "PyPI"},
                    "ranges": [
                        {"events": [{"introduced": "0"}, {"fixed": "1.5.0"}]}
                    ],
                }
            ],
            "references": [
                {"type": "WEB", "url": "https://example.com/advisory"},
                {"type": "WEB", "url": "https://example.com/fix"},
            ],
        }
        s = summarize_vuln(vuln, "foo", "1.4.0")
        assert s["id"] == "GHSA-abc1-2def-3ghi"
        assert s["aliases"] == ["CVE-2023-1234"]
        assert s["package"] == "foo"
        assert s["version"] == "1.4.0"
        assert "1.5.0" in s["fixed_versions"]
        assert "RCE" in s["summary"]
        assert len(s["references"]) == 2

    def test_minimal_vuln(self):
        s = summarize_vuln({"id": "X-1"}, "bar", "0.1")
        assert s["id"] == "X-1"
        assert s["aliases"] == []
        assert s["fixed_versions"] == []
        assert s["severity"] == "UNKNOWN"
        assert s["references"] == []

    def test_multiple_fixed_versions_deduped(self):
        vuln = {
            "id": "Y-2",
            "affected": [
                {
                    "package": {"name": "pkg"},
                    "ranges": [{"events": [{"fixed": "2.0.0"}, {"fixed": "2.0.0"}]}],
                }
            ],
        }
        s = summarize_vuln(vuln, "pkg", "1.0")
        assert s["fixed_versions"] == ["2.0.0"]

    def test_different_package_affected_skipped(self):
        vuln = {
            "id": "Z-3",
            "affected": [
                {
                    "package": {"name": "other"},
                    "ranges": [{"events": [{"fixed": "9.9.9"}]}],
                }
            ],
        }
        s = summarize_vuln(vuln, "pkg", "1.0")
        assert s["fixed_versions"] == []


# ── SecurityScannerTool.execute ──────────────────────────────────────────────


class TestSecurityScannerTool:
    def test_name_and_description(self):
        assert SecurityScannerTool.name == "security_scan"
        assert "OSV" in SecurityScannerTool.description

    def test_single_package_success(self):
        vulns = [
            {
                "id": "CVE-2024-1",
                "summary": "xss",
                "affected": [
                    {
                        "package": {"name": "requests"},
                        "ranges": [{"events": [{"fixed": "2.31.0"}]}],
                    }
                ],
            }
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"vulns": vulns}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("lilith_tools.security.urllib.request.urlopen", return_value=mock_resp):
            tool = SecurityScannerTool()
            result = tool.execute(name="requests", ecosystem="PyPI", version="2.20.0")
        assert result.success is True
        assert result.data["package"] == "requests"
        assert result.data["vulnerability_count"] == 1
        assert result.data["vulnerabilities"][0]["id"] == "CVE-2024-1"

    def test_single_package_no_vulns(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"vulns": []}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("lilith_tools.security.urllib.request.urlopen", return_value=mock_resp):
            result = SecurityScannerTool().execute(name="clean", version="1.0.0")
        assert result.success is True
        assert result.data["vulnerability_count"] == 0
        assert result.data["vulnerabilities"] == []

    def test_single_package_missing_args(self):
        result = SecurityScannerTool().execute(name="onlyname")
        assert result.success is False
        assert "name" in result.error and "version" in result.error

    def test_single_package_bad_ecosystem(self):
        result = SecurityScannerTool().execute(name="x", version="1.0", ecosystem="cargo")
        assert result.success is False
        assert "ecosystem" in result.error

    def test_single_package_osv_error(self):
        with patch("lilith_tools.security.urllib.request.urlopen", side_effect=OSError("network down")):
            result = SecurityScannerTool().execute(name="x", version="1.0")
        assert result.success is False
        assert "network down" in result.error

    def test_file_mode_success(self, tmp_path):
        f = tmp_path / "requirements.txt"
        f.write_text("requests==2.20.0\nclean-pkg==1.0.0\n")

        def fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode())
            name = payload["package"]["name"]
            if name == "requests":
                body = {"vulns": [{"id": "CVE-X", "summary": "bad", "affected": [{"package": {"name": "requests"}, "ranges": [{"events": [{"fixed": "2.31.0"}]}]}]}]}
            else:
                body = {"vulns": []}
            mock = MagicMock()
            mock.read.return_value = json.dumps(body).encode()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("lilith_tools.security.urllib.request.urlopen", side_effect=fake_urlopen):
            result = SecurityScannerTool().execute(file=str(f))
        assert result.success is True
        assert result.data["scanned"] == 2
        assert len(result.data["vulnerabilities"]) == 1
        assert result.data["vulnerabilities"][0]["id"] == "CVE-X"
        assert result.data["error_count"] == 0

    def test_file_mode_missing_file(self):
        result = SecurityScannerTool().execute(file="nope.txt")
        assert result.success is False
        assert "not found" in result.error

    def test_file_mode_unsupported(self, tmp_path):
        f = tmp_path / "Cargo.toml"
        f.write_text("[deps]\n")
        result = SecurityScannerTool().execute(file=str(f))
        assert result.success is False
        assert "Unsupported" in result.error

    def test_file_mode_no_pinned_deps(self, tmp_path):
        f = tmp_path / "requirements.txt"
        f.write_text("# just comments\nrequests\n")
        result = SecurityScannerTool().execute(file=str(f))
        assert result.success is True
        assert result.data["scanned"] == 0

    def test_file_mode_partial_errors(self, tmp_path):
        f = tmp_path / "requirements.txt"
        f.write_text("a==1.0.0\nb==2.0.0\n")

        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("timeout for a")
            mock = MagicMock()
            mock.read.return_value = json.dumps({"vulns": []}).encode()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("lilith_tools.security.urllib.request.urlopen", side_effect=fake_urlopen):
            result = SecurityScannerTool().execute(file=str(f))
        assert result.success is True
        assert result.data["scanned"] == 2
        assert result.data["error_count"] == 1
        assert "a@1.0.0" in result.data["errors"][0]

    def test_registry_registered(self):
        from lilith_tools.registry import ToolRegistry
        # re-register to be resilient to other tests clearing the global registry
        ToolRegistry.register(SecurityScannerTool)
        assert ToolRegistry.get("security_scan") is SecurityScannerTool
