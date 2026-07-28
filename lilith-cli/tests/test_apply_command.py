"""Tests for the /apply slash command."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import (
    _check_patch_targets_in_repo,
    _resolve_repo_root,
    run_apply_command,
)


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


def _init_repo(path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        check=True,
        capture_output=True,
    )


# ── _resolve_repo_root ───────────────────────────────────────────────────────


def test_resolve_repo_root_inside_git(tmp_path, monkeypatch):
    """/apply detecta la raíz del repo incluso desde un subdirectorio."""
    _init_repo(tmp_path)
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    root = _resolve_repo_root()
    assert root is not None
    assert root.resolve() == tmp_path.resolve()


def test_resolve_repo_root_outside_git(tmp_path, monkeypatch):
    """/apply devuelve None fuera de un repo git (no crashea)."""
    monkeypatch.chdir(tmp_path)
    assert _resolve_repo_root() is None


# ── _check_patch_targets_in_repo ─────────────────────────────────────────────


def test_check_patch_targets_accepts_in_repo(tmp_path):
    """/apply acepta parches que apuntan a archivos dentro del repo."""
    patch = (
        "--- a/src/old.py\n"
        "+++ b/src/new.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    bad = _check_patch_targets_in_repo(patch, tmp_path)
    assert bad == []


def test_check_patch_targets_rejects_escape(tmp_path):
    """/apply rechaza parches que apuntan fuera del repo."""
    patch = (
        "--- a/x.py\n"
        "+++ b/../outside.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    bad = _check_patch_targets_in_repo(patch, tmp_path)
    assert bad == ["../outside.py"]


def test_check_patch_targets_accepts_devnull(tmp_path):
    """/apply permite líneas +++ /dev/null (creación/eliminación)."""
    patch = (
        "--- a/new_file.py\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-x\n"
    )
    bad = _check_patch_targets_in_repo(patch, tmp_path)
    assert bad == []


# ── run_apply_command: integración real con `git apply` ─────────────────────


@pytest.mark.asyncio
async def test_apply_happy_path(tmp_path, monkeypatch):
    """/apply aplica un diff válido sobre un archivo existente."""
    _init_repo(tmp_path)
    target = tmp_path / "hello.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    subprocess.run(["git", "add", str(target)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)
    diff_text = (
        "--- a/hello.txt\n"
        "+++ b/hello.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " one\n"
        "-two\n"
        "+dos\n"
        " three\n"
    )
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(diff_text, encoding="utf-8")

    session = DummySession()
    prints: list[str] = []

    def capture(text: str = "", **kwargs):
        if isinstance(text, str):
            prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_apply_command(session, f"{patch_file}")

    assert target.read_text(encoding="utf-8") == "one\ndos\nthree\n"
    assert any("Parche aplicado" in p for p in prints), prints


@pytest.mark.asyncio
async def test_apply_check_does_not_modify(tmp_path, monkeypatch):
    """/apply --check verifica sin escribir."""
    _init_repo(tmp_path)
    target = tmp_path / "foo.txt"
    target.write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", str(target)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)
    diff_text = (
        "--- a/foo.txt\n"
        "+++ b/foo.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-a\n"
        "+b\n"
    )
    patch_file = tmp_path / "p.patch"
    patch_file.write_text(diff_text, encoding="utf-8")

    session = DummySession()
    prints: list[str] = []

    def capture(text: str = "", **kwargs):
        if isinstance(text, str):
            prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_apply_command(session, f"{patch_file} --check")

    # El archivo NO fue modificado.
    assert target.read_text(encoding="utf-8") == "a\n"
    # Pero el comando reportó éxito de validación.
    assert any("válido" in p for p in prints), prints


@pytest.mark.asyncio
async def test_apply_rejects_path_outside_repo(tmp_path, monkeypatch):
    """/apply rechaza parches con targets fuera del repo (modo seguro)."""
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    # Parches que apuntan a /tmp/evil.py — _check_patch_targets_in_repo los caza.
    diff_text = (
        "--- a/x\n"
        "+++ b/../../../evil.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-a\n"
        "+b\n"
    )
    patch_file = tmp_path / "evil.patch"
    patch_file.write_text(diff_text, encoding="utf-8")

    session = DummySession()
    prints: list[str] = []

    def capture(text: str = "", **kwargs):
        if isinstance(text, str):
            prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_apply_command(session, f"{patch_file}")

    assert any("fuera del repositorio" in p for p in prints), prints


@pytest.mark.asyncio
async def test_apply_shows_help_when_no_args(tmp_path, monkeypatch):
    """/apply sin argumentos muestra la ayuda en español."""
    session = DummySession()
    prints: list[str] = []

    def capture(text: str = "", **kwargs):
        if isinstance(text, str):
            prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_apply_command(session, "")

    joined = "\n".join(prints)
    assert "/apply <archivo.diff>" in joined
    assert "--check" in joined
    assert "--reverse" in joined
    assert "--3way" in joined


@pytest.mark.asyncio
async def test_apply_rejects_unknown_flag(tmp_path, monkeypatch):
    """/apply reporta error en flags desconocidos y no ejecuta git."""
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    session = DummySession()
    prints: list[str] = []

    def capture(text: str = "", **kwargs):
        if isinstance(text, str):
            prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_apply_command(session, "fix.patch --bogus-flag")

    assert any("no reconocidos" in p for p in prints), prints