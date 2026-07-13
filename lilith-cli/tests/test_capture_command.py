"""Tests for the /capture slash command."""

from __future__ import annotations

from pathlib import Path

import pytest

from lilith_cli.extra_commands import run_capture_command


class _DummyConfig:
    """Minimal config stand-in for /capture tests."""

    def __init__(self) -> None:
        self.model = "test-model"
        self.provider = "test-provider"


class _DummySession:
    """Minimal session stand-in for /capture tests."""

    def __init__(self, history: list | None = None) -> None:
        self.config = _DummyConfig()
        self.history = list(history) if history is not None else []
        self._tool_call_history: list[dict] = []
        self._total_usage = {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }

    @property
    def total_usage(self):
        return dict(self._total_usage)


@pytest.mark.asyncio
async def test_capture_basic(tmp_path: Path) -> None:
    """/capture should write a human-readable Markdown transcript."""
    out_path = tmp_path / "out.md"
    session = _DummySession(
        history=[
            {"role": "user", "content": "Hola Lilith"},
            {"role": "assistant", "content": "Hola, cazador."},
        ]
    )

    await run_capture_command(session, f"--output {out_path}")

    content = out_path.read_text(encoding="utf-8")
    assert content.startswith("# Lilith transcript")
    assert "## 👤 Usuario" in content
    assert "## 🤖 Lilith" in content
    assert "Hola Lilith" in content
    assert "Hola, cazador." in content
    assert "test-model" in content
    assert "test-provider" in content
    assert "Tokens:" in content


@pytest.mark.asyncio
async def test_capture_include_tools(tmp_path: Path) -> None:
    """/capture --include-tools should include tool call summaries."""
    out_path = tmp_path / "x.md"
    session = _DummySession(
        history=[
            {"role": "user", "content": "Leé README.md"},
            {"role": "assistant", "content": "Listo."},
        ]
    )
    session._tool_call_history = [
        {"name": "file_read", "duration": 0.123, "arguments": {"path": "README.md"}},
        {"name": "run_tests", "duration": 1.5, "arguments": {"cmd": "pytest -q"}},
    ]

    await run_capture_command(session, f"--output {out_path} --include-tools")

    content = out_path.read_text(encoding="utf-8")
    assert "## 🔧 Herramientas llamadas" in content
    assert "file_read" in content
    assert "run_tests" in content
    assert "—" in content
