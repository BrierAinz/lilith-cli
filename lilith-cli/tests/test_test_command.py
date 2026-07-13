"""Tests for the /test slash command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.config import CONFIG_DIR
from lilith_cli.extra_commands import (
    _set_test_last_failed_path,
    run_test_command,
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


@pytest.fixture
def isolated_last_failed(tmp_path):
    """Redirect last-failed storage to a temporary directory."""
    path = tmp_path / "test_last_failed.json"
    _set_test_last_failed_path(path)
    yield path
    _set_test_last_failed_path(CONFIG_DIR / "test_last_failed.json")


@pytest.mark.asyncio
async def test_test_command_runs_all_tests_in_cwd(tmp_path, monkeypatch):
    """/test sin argumentos ejecuta pytest en el directorio actual."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "test_dummy.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_test_command(session, "")

    output = "".join(str(p) for p in prints)
    assert "test_dummy.py" in output or "1 passed" in output


@pytest.mark.asyncio
async def test_test_command_last_failed_with_no_history(isolated_last_failed):
    """/test last avisa cuando no hay tests fallidos previos."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_test_command(session, "last")

    output = "".join(str(p) for p in prints)
    assert "No hay tests fallidos previos" in output
