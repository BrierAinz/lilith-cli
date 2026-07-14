"""Tests for the /test slash command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.config import CONFIG_DIR
from lilith_cli.extra_commands import (
    _DEFAULT_TEST_SUITE,
    _PYTEST_SUMMARY_RE,
    _parse_pytest_summary,
    _render_test_summary,
    _render_test_usage,
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


def _capture_prints(monkeypatch=None):
    """Return a (prints list, restore helper) pair."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(text)

    patcher = patch("lilith_cli.extra_commands.console.print", side_effect=capture)
    patcher.start()
    return prints, patcher.stop


# ── /test last (no history) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_command_last_failed_with_no_history(isolated_last_failed):
    """/test last avisa cuando no hay tests fallidos previos."""
    prints, stop = _capture_prints()
    try:
        session = DummySession()
        await run_test_command(session, "last")
    finally:
        stop()

    output = "".join(str(p) for p in prints)
    assert "No hay tests fallidos previos" in output


# ── /test --help ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_command_help_flag():
    """/test --help imprime la ayuda en español."""
    prints, stop = _capture_prints()
    try:
        await run_test_command(DummySession(), "--help")
    finally:
        stop()

    output = "".join(str(p) for p in prints)
    assert "/test" in output
    assert "Uso" in output
    assert "-k" in output
    assert "last" in output


# ── /test (no args) uses default suite and patches subprocess ─────────────


@pytest.mark.asyncio
async def test_test_command_no_args_runs_default_suite():
    """/test sin args llama al runner con la suite por defecto."""
    fake_summary = {
        "passed": 5,
        "failed": 0,
        "error": 0,
        "duration": 1.23,
        "last_failure": None,
        "returncode": 0,
        "command": ["python", "-m", "pytest", _DEFAULT_TEST_SUITE],
    }
    prints, stop = _capture_prints()
    try:
        with patch(
            "lilith_cli.extra_commands._run_pytest_subprocess",
            return_value=fake_summary,
        ) as run_mock:
            await run_test_command(DummySession(), "")
    finally:
        stop()

    run_mock.assert_called_once()
    args, kwargs = run_mock.call_args
    # first positional arg = target
    assert args[0] == _DEFAULT_TEST_SUITE
    assert kwargs.get("keyword") in (None, "")
    output = "".join(str(p) for p in prints)
    assert "5 passed" in output
    assert "1.23s" in output


# ── /test <path> forwards target ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_command_with_target_path():
    """/test <ruta> pasa la ruta al runner."""
    fake_summary = {
        "passed": 1,
        "failed": 0,
        "error": 0,
        "duration": 0.10,
        "last_failure": None,
        "returncode": 0,
        "command": [],
    }
    prints, stop = _capture_prints()
    try:
        with patch(
            "lilith_cli.extra_commands._run_pytest_subprocess",
            return_value=fake_summary,
        ) as run_mock:
            await run_test_command(DummySession(), "lilith-stack/lilith-cli/tests/test_plan.py")
    finally:
        stop()

    args, kwargs = run_mock.call_args
    assert args[0] == "lilith-stack/lilith-cli/tests/test_plan.py"


# ── /test -k <expr> ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_command_with_keyword_flag():
    """/test -k expr filtra por nombre."""
    fake_summary = {
        "passed": 2,
        "failed": 0,
        "error": 0,
        "duration": 0.5,
        "last_failure": None,
        "returncode": 0,
        "command": [],
    }
    prints, stop = _capture_prints()
    try:
        with patch(
            "lilith_cli.extra_commands._run_pytest_subprocess",
            return_value=fake_summary,
        ) as run_mock:
            await run_test_command(DummySession(), "-k hello")
    finally:
        stop()

    args, kwargs = run_mock.call_args
    assert kwargs.get("keyword") == "hello"
    # No path tokens → default target
    assert args[0] == _DEFAULT_TEST_SUITE


@pytest.mark.asyncio
async def test_test_command_with_k_equals():
    """/test -k=expr (sin espacio) también se acepta."""
    fake_summary = {
        "passed": 1,
        "failed": 0,
        "error": 0,
        "duration": 0.1,
        "last_failure": None,
        "returncode": 0,
        "command": [],
    }
    with patch(
        "lilith_cli.extra_commands._run_pytest_subprocess",
        return_value=fake_summary,
    ) as run_mock:
        await run_test_command(DummySession(), "-k=hello")

    args, kwargs = run_mock.call_args
    assert kwargs.get("keyword") == "hello"


@pytest.mark.asyncio
async def test_test_command_combined_path_and_keyword():
    """/test <ruta> -k expr combina ambos."""
    fake_summary = {
        "passed": 1,
        "failed": 0,
        "error": 0,
        "duration": 0.1,
        "last_failure": None,
        "returncode": 0,
        "command": [],
    }
    with patch(
        "lilith_cli.extra_commands._run_pytest_subprocess",
        return_value=fake_summary,
    ) as run_mock:
        await run_test_command(
            DummySession(),
            "lilith-stack/lilith-cli/tests/ -k smoke",
        )

    args, kwargs = run_mock.call_args
    assert args[0] == "lilith-stack/lilith-cli/tests/"
    assert kwargs.get("keyword") == "smoke"


# ── /test with pytest failure shows last FAILED ───────────────────────────


@pytest.mark.asyncio
async def test_test_command_shows_last_failure():
    """/test muestra la última línea FAILED cuando hay fallos."""
    fake_summary = {
        "passed": 1,
        "failed": 2,
        "error": 0,
        "duration": 0.5,
        "last_failure": "FAILED tests/test_foo.py::test_broken",
        "returncode": 1,
        "command": [],
    }
    prints, stop = _capture_prints()
    try:
        with patch(
            "lilith_cli.extra_commands._run_pytest_subprocess",
            return_value=fake_summary,
        ):
            await run_test_command(DummySession(), "")
    finally:
        stop()

    output = "".join(str(p) for p in prints)
    assert "1 passed" in output
    assert "2 failed" in output
    assert "FAILED tests/test_foo.py::test_broken" in output
    assert "exit=1" in output


# ── /test with subprocess error renders tip ───────────────────────────────


@pytest.mark.asyncio
async def test_test_command_subprocess_error_renders_tip():
    """/test imprime el error y un tip si el runner devolvió un error."""
    fake_summary = {
        "passed": 0,
        "failed": 0,
        "error": 0,
        "duration": 0.0,
        "last_failure": None,
        "returncode": -1,
        "command": [],
        "error": "pytest no disponible",
    }
    prints, stop = _capture_prints()
    try:
        with patch(
            "lilith_cli.extra_commands._run_pytest_subprocess",
            return_value=fake_summary,
        ):
            await run_test_command(DummySession(), "")
    finally:
        stop()

    output = "".join(str(p) for p in prints)
    assert "pytest no disponible" in output
    assert ".venv" in output  # tip mentions .venv


# ── _parse_pytest_summary helpers ─────────────────────────────────────────


def test_parse_pytest_summary_canonical_line():
    summary = _parse_pytest_summary("===== 475 passed in 28.13s =====")
    assert summary["passed"] == 475
    assert summary["failed"] == 0
    assert summary["duration"] == pytest.approx(28.13)


def test_parse_pytest_summary_segmented_line():
    text = "3 failed, 1 passed in 0.5s"
    summary = _parse_pytest_summary(text)
    assert summary["passed"] == 1
    assert summary["failed"] == 3


def test_parse_pytest_summary_captures_last_failure():
    text = (
        "FAILED tests/test_a.py::test_x - assert False\n"
        "FAILED tests/test_b.py::test_y - assert True\n"
        "===== 1 failed, 4 passed in 0.3s ====="
    )
    summary = _parse_pytest_summary(text)
    assert summary["failed"] == 1
    assert summary["passed"] == 4
    assert summary["last_failure"] is not None
    assert "test_b.py" in summary["last_failure"]


def test_parse_pytest_summary_empty_text():
    summary = _parse_pytest_summary("")
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["last_failure"] is None


def test_render_test_summary_includes_counts_and_duration():
    summary = {"passed": 3, "failed": 0, "error": 0, "duration": 1.5, "last_failure": None}
    line = _render_test_summary(summary, 0)
    assert "3 passed" in line
    assert "1.50s" in line
    assert "exit=" not in line  # returncode 0 → no exit label


def test_render_test_summary_includes_exit_code_when_nonzero():
    summary = {"passed": 0, "failed": 1, "error": 0, "duration": 0.2, "last_failure": None}
    line = _render_test_summary(summary, 2)
    assert "1 failed" in line
    assert "exit=2" in line


def test_render_test_usage_contains_help():
    _render_test_usage()  # only checks no exception; smoke test