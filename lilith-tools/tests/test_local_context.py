"""Tests for local_context tools (iwomm-mcp style)."""
from __future__ import annotations

import os
import subprocess

import pytest

from lilith_tools import local_context
from lilith_tools.local_context import (
    LocalDiskUsageTool,
    LocalDockerPsTool,
    LocalEnvTool,
    LocalGitLogTool,
    LocalGitStatusTool,
    LocalPortsTool,
    LocalProcessesTool,
    LocalPythonInfoTool,
)


def test_run_timeout_names_the_command(monkeypatch):
    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["docker", "ps"], timeout=10.0)

    monkeypatch.setattr(local_context.subprocess, "run", _timeout)

    rc, stdout, stderr = local_context._run(["docker", "ps"], timeout=10.0)

    assert (rc, stdout) == (-2, "")
    assert stderr == "docker timed out after 10.0s"


# ── LocalPythonInfoTool ────────────────────────────────────────────────────


class TestLocalPythonInfo:
    def test_returns_basic_info(self):
        tool = LocalPythonInfoTool()
        result = tool.execute()
        assert result.success
        assert "executable" in result.data
        assert "version" in result.data
        assert "platform" in result.data
        assert "is_venv" in result.data
        assert isinstance(result.data["is_venv"], bool)


# ── LocalDiskUsageTool ─────────────────────────────────────────────────────


class TestLocalDiskUsage:
    def test_disk_usage_cwd(self):
        tool = LocalDiskUsageTool()
        result = tool.execute()
        assert result.success
        assert "total_bytes" in result.data
        assert "free_bytes" in result.data
        assert result.data["total_bytes"] > 0

    def test_disk_usage_specific_path(self):
        tool = LocalDiskUsageTool()
        result = tool.execute(path=os.getcwd())
        assert result.success
        assert result.data["path"] == os.getcwd()


# ── LocalEnvTool ───────────────────────────────────────────────────────────


class TestLocalEnv:
    def test_read_specific_var(self, monkeypatch):
        monkeypatch.setenv("LILITH_TEST_VAR", "hello_world")
        tool = LocalEnvTool()
        result = tool.execute(name="LILITH_TEST_VAR")
        assert result.success
        assert result.data["name"] == "LILITH_TEST_VAR"
        assert result.data["value"] == "hello_world"

    def test_missing_var(self):
        tool = LocalEnvTool()
        result = tool.execute(name="LILITH_NONEXISTENT_XYZ_VAR")
        assert not result.success
        assert "not set" in result.error

    def test_secret_var_is_masked(self, monkeypatch):
        monkeypatch.setenv("LILITH_FAKE_API_KEY", "secret12345")
        tool = LocalEnvTool()
        result = tool.execute(name="LILITH_FAKE_API_KEY")
        assert result.success
        assert result.data["value"] == "***MASKED***"

    def test_list_with_prefix(self, monkeypatch):
        monkeypatch.setenv("LILITH_TEST_FOO", "1")
        monkeypatch.setenv("LILITH_TEST_BAR", "2")
        tool = LocalEnvTool()
        result = tool.execute(prefix="LILITH_TEST_")
        assert result.success
        assert result.data["count"] >= 2
        assert "LILITH_TEST_FOO" in result.data["values"]
        assert "LILITH_TEST_BAR" in result.data["values"]


# ── LocalGitStatusTool ─────────────────────────────────────────────────────


class TestLocalGitStatus:
    def test_git_status_in_repo(self, tmp_path):
        # Initialize a git repo in tmp_path
        subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True, capture_output=True)
        # Create a file
        (tmp_path / "a.txt").write_text("hello")
        tool = LocalGitStatusTool()
        result = tool.execute(path=str(tmp_path))
        assert result.success
        assert result.data["is_repo"] is True
        assert result.data["dirty_count"] >= 1

    def test_git_status_nonexistent_path(self):
        tool = LocalGitStatusTool()
        result = tool.execute(path="/nonexistent/path/that/does/not/exist")
        # git status in non-repo returns error
        assert not result.success


# ── LocalGitLogTool ────────────────────────────────────────────────────────


class TestLocalGitLog:
    def test_git_log_in_repo(self, tmp_path):
        subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "test commit"], cwd=str(tmp_path), check=True, capture_output=True)
        tool = LocalGitLogTool()
        result = tool.execute(path=str(tmp_path), limit=5)
        assert result.success
        assert result.data["count"] >= 1
        assert result.data["commits"][0]["subject"] == "test commit"

    def test_git_log_nonexistent_path(self):
        tool = LocalGitLogTool()
        result = tool.execute(path="/nonexistent/path/xyz")
        assert not result.success


# ── LocalProcessesTool ─────────────────────────────────────────────────────


class TestLocalProcesses:
    def test_list_processes(self):
        tool = LocalProcessesTool()
        result = tool.execute(limit=10)
        assert result.success
        assert result.data["count"] >= 0
        assert isinstance(result.data["processes"], list)

    def test_filter_processes(self):
        tool = LocalProcessesTool()
        result = tool.execute(filter="python", limit=50)
        assert result.success
        # All returned should match filter
        for p in result.data["processes"]:
            assert "python" in p["name"].lower()

    def test_filter_applies_before_limit(self):
        """El filtro debe mirar todos los procesos, no solo los primeros ``limit``.

        Antes se recortaba a ``limit`` y recién después se filtraba, así que
        buscar un nombre concreto devolvía 0 si no caía entre los primeros
        procesos que enumeraba el sistema. El intérprete que corre este test
        siempre existe, así que un límite chico no debería esconderlo.
        """
        tool = LocalProcessesTool()
        result = tool.execute(filter="python", limit=3)
        assert result.success
        assert result.data["count"] >= 1
        assert len(result.data["processes"]) <= 3

    def test_limit_is_respected_without_filter(self):
        tool = LocalProcessesTool()
        result = tool.execute(limit=4)
        assert result.success
        assert len(result.data["processes"]) <= 4
        assert result.data["count"] == len(result.data["processes"])


# ── LocalPortsTool ─────────────────────────────────────────────────────────


class TestLocalPorts:
    def test_list_ports(self):
        tool = LocalPortsTool()
        result = tool.execute()
        # Either succeeds with ports or fails gracefully on systems without netstat
        if result.success:
            assert isinstance(result.data["ports"], list)
        else:
            # Acceptable on minimal systems
            assert result.error != ""

    def test_list_ports_ignores_wildcard_addresses(self, monkeypatch):
        def fake_run(cmd, timeout=5.0, cwd=None):
            return 0, """State Recv-Q Send-Q Local Address:Port Peer Address:Port
LISTEN 0 4096 *:* *:*
LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*
LISTEN 0 4096 [::]:5432 [::]:*
""", ""

        monkeypatch.setattr(local_context, "_run", fake_run)
        result = LocalPortsTool().execute()
        assert result.success
        assert result.data["ports"] == ["5432", "8000"]


# ── LocalDockerPsTool ──────────────────────────────────────────────────────


class TestLocalDockerPs:
    def test_docker_ps_handles_missing_docker(self, monkeypatch):
        monkeypatch.setattr(local_context.shutil, "which", lambda _cmd: None)
        tool = LocalDockerPsTool()
        result = tool.execute()
        assert not result.success
        assert result.error == "docker not installed"

    def test_docker_ps_reports_daemon_timeout(self, monkeypatch):
        monkeypatch.setattr(local_context.shutil, "which", lambda _cmd: "docker")
        monkeypatch.setattr(
            local_context,
            "_run",
            lambda _cmd, timeout: (-2, "", f"docker timed out after {timeout}s"),
        )

        result = LocalDockerPsTool().execute()

        assert not result.success
        assert result.error == "docker timed out after 10.0s"
