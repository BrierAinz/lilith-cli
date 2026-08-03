"""Tests for the destructive ``git init`` operation in
``GitOperationTool``.

``init`` lives in the destructive set: it creates a new repository and
flips ``HEAD`` in the current working dir, so it must be refused unless
the caller passes ``confirm=True``. Listing it under
``_DESTRUCTIVE_OPERATIONS`` also makes it appear in the description and
in the ``Operacion git no permitida`` error (when users mistype an op
that is not in the allow-list).
"""

from __future__ import annotations

import pytest

from lilith_tools.base import ToolResult
from lilith_tools.git_tools import (
    _DESTRUCTIVE_OPERATIONS,
    _SAFE_OPERATIONS,
    GitOperationTool,
)


@pytest.fixture
def tool() -> GitOperationTool:
    return GitOperationTool()


def test_init_is_listed_as_destructive():
    """'init' must show up in _DESTRUCTIVE_OPERATIONS so it requires confirm."""
    assert "init" in _DESTRUCTIVE_OPERATIONS
    assert "init" not in _SAFE_OPERATIONS


def test_init_rejected_without_confirm(tool):
    """Without ``confirm=True`` the tool refuses 'init' and returns a
    structured failure — it must not shell out to git."""
    res = tool.execute(op="init", confirm=False)
    assert isinstance(res, ToolResult)
    assert res.success is False
    assert "init" in (res.error or "")
    assert "confirm=True" in (res.error or "")


def test_init_allowed_with_confirm(tool, tmp_path, monkeypatch):
    """With ``confirm=True`` the tool invokes ``git init`` and surfaces
    whatever stdout/stderr git produced.

    We monkeypatch ``subprocess.run`` so we don't actually touch the host
    file system (and don't depend on git being installed on PATH).
    """

    fake = type(
        "FakeProc",
        (),
        {
            "stdout": "Initialized empty Git repository in .git/",
            "stderr": "",
            "returncode": 0,
        },
    )()

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["capture_output"] = kwargs.get("capture_output")
        return fake

    monkeypatch.setattr("lilith_tools.git_tools.subprocess.run", _fake_run)

    res = tool.execute(op="init", confirm=True)

    assert res.success is True, f"expected success, got error {res.error!r}"
    assert captured["cmd"][0] == "git"
    assert captured["cmd"][1] == "init"
    # Returned data must include stdout + stderr + returncode per the tool contract.
    assert res.data["returncode"] == 0
    assert "Initialized" in res.data["stdout"]


def test_init_failed_git_returns_structured_error(tool, monkeypatch):
    """When git itself returns non-zero, the result is success=False but
    ``data`` still carries stdout/stderr/command for the LLM to inspect."""

    fake = type(
        "FakeProc",
        (),
        {"stdout": "", "stderr": "fatal: not a directory", "returncode": 128},
    )()

    monkeypatch.setattr(
        "lilith_tools.git_tools.subprocess.run", lambda *a, **kw: fake
    )

    res = tool.execute(op="init", confirm=True, args="/nonexistent/path")

    assert res.success is False
    assert res.data["returncode"] == 128
    assert "fatal" in res.data["stderr"]


def test_init_error_message_lists_init_for_typo(tool):
    """A typo like ``initl`` should hit the not-allowed branch and the
    resulting error message must include ``init`` so the user sees the
    full allowlist (including the newly-added one)."""

    res = tool.execute(op="inici", confirm=True)
    assert res.success is False
    assert "init" in (res.error or "")


def test_init_description_mentions_init():
    """The tool description must list ``init`` so the LLM knows it exists
    and that it requires ``confirm=True``."""

    desc = GitOperationTool.description
    assert "init" in desc
    assert "confirm=True" in desc


class TestWorkdirAnchor:
    """git_operation debe correr anclado a la raíz de sesión, no al cwd
    del proceso (los sandboxes de otras tools hacen os.chdir y lo desplazan)."""

    def _init_repo(self, path):
        import subprocess

        subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
        (path / "a.txt").write_text("hola", encoding="utf-8")

    def test_default_usa_session_root(self, tool, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.setattr("lilith_tools.git_tools._SESSION_ROOT", tmp_path)
        # cwd del proceso desplazado a otro lado (simula el sandbox de coding)
        monkeypatch.chdir(tmp_path.parent)

        res = tool.execute(op="status", args="-s")
        assert res.success is True
        assert res.data["workdir"] == str(tmp_path)
        assert "a.txt" in res.data["stdout"]

    def test_workdir_explicito_gana(self, tool, tmp_path, monkeypatch):
        self._init_repo(tmp_path)
        monkeypatch.setattr("lilith_tools.git_tools._SESSION_ROOT", tmp_path.parent)

        res = tool.execute(op="status", args="-s", workdir=str(tmp_path))
        assert res.success is True
        assert res.data["workdir"] == str(tmp_path)
        assert "a.txt" in res.data["stdout"]

    def test_workdir_inexistente_falla_claro(self, tool):
        res = tool.execute(op="status", workdir="Z:/no/existe/xyz")
        assert res.success is False
        assert "workdir" in (res.error or "")
