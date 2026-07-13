"""Tests for the /diff-staged slash command."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_diff_staged_command


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


@pytest.mark.asyncio
async def test_diff_staged_no_changes(tmp_path, monkeypatch):
    """/diff-staged en un repo sin cambios preparados muestra mensaje adecuado."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    session = DummySession()
    prints = []

    def capture(text: str = "", **kwargs):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_diff_staged_command(session, "")

    assert any("No hay cambios preparados" in str(p) for p in prints)


@pytest.mark.asyncio
async def test_diff_staged_stats_shows_file_only(tmp_path, monkeypatch):
    """/diff-staged stats muestra estadísticas de archivos preparados."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    test_file = tmp_path / "stats.txt"
    test_file.write_text("linea\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True, capture_output=True)

    session = DummySession()
    prints = []

    def capture(text: str = "", **kwargs):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_diff_staged_command(session, "stats")

    output = "\n".join(str(p) for p in prints)
    assert "stats.txt" in output


@pytest.mark.asyncio
async def test_diff_staged_specific_file(tmp_path, monkeypatch):
    """/diff-staged <archivo> muestra diff de un archivo preparado."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    test_file = tmp_path / "tracked.txt"
    test_file.write_text("contenido original\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    test_file.write_text("contenido modificado\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True, capture_output=True)

    session = DummySession()
    prints = []

    def capture(text: str = "", **kwargs):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_diff_staged_command(session, "tracked.txt")

    output = "\n".join(str(p) for p in prints)
    assert "contenido original" in output or "contenido modificado" in output or "---" in output
