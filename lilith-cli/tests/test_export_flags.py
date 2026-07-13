"""Tests for /export --format and --output flags."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


def _run(coro):
    return asyncio.run(coro)


def test_export_default_json(fake_session, tmp_path, monkeypatch, capsys):
    """/export (no args) writes JSON to conversations dir."""
    from lilith_cli import extra_commands as ec

    # Redirect CONFIG_DIR so we don't pollute the real one
    monkeypatch.setattr(ec, "CONFIG_DIR", tmp_path)

    fake_session.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    fake_session.config.model = "test-model"
    fake_session.config.provider = "test-provider"

    _run(ec.run_export_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "exportada" in out
    # Default name is timestamp
    conversations_dir = tmp_path / "conversations"
    files = list(conversations_dir.glob("*.json"))
    assert len(files) == 1
    # Validate JSON structure
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["model"] == "test-model"
    assert len(data["messages"]) == 2


def test_export_format_md(fake_session, tmp_path, monkeypatch, capsys):
    """/export --format md writes Markdown."""
    from lilith_cli import extra_commands as ec

    monkeypatch.setattr(ec, "CONFIG_DIR", tmp_path)

    fake_session.history = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "response"},
    ]

    _run(ec.run_export_command(fake_session, "--format md"))

    out = capsys.readouterr().out
    conversations_dir = tmp_path / "conversations"
    files = list(conversations_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    # Markdown structure
    assert "# Conversaci\u00f3n" in content
    assert "## user" in content
    assert "## assistant" in content
    assert "first message" in content


def test_export_custom_output_path(fake_session, tmp_path, capsys):
    """/export --output <path> writes to specified path."""
    from lilith_cli import extra_commands as ec

    fake_session.history = [{"role": "user", "content": "x"}]
    custom_path = tmp_path / "my_export.json"

    _run(ec.run_export_command(fake_session, f"--output {custom_path}"))

    out = capsys.readouterr().out
    assert "exportada" in out
    assert custom_path.exists()
    data = json.loads(custom_path.read_text(encoding="utf-8"))
    assert len(data["messages"]) == 1


def test_export_output_with_format_md(fake_session, tmp_path, capsys):
    """/export --output file.md --format md combines both flags."""
    from lilith_cli import extra_commands as ec

    fake_session.history = [{"role": "user", "content": "y"}]
    custom_path = tmp_path / "custom.md"

    _run(ec.run_export_command(fake_session, f"--format md --output {custom_path}"))

    assert custom_path.exists()
    content = custom_path.read_text(encoding="utf-8")
    assert "## user" in content


def test_export_named_file(fake_session, tmp_path, monkeypatch, capsys):
    """/export <name> uses given name."""
    from lilith_cli import extra_commands as ec

    monkeypatch.setattr(ec, "CONFIG_DIR", tmp_path)

    fake_session.history = [{"role": "user", "content": "z"}]

    _run(ec.run_export_command(fake_session, "my_session"))

    conversations_dir = tmp_path / "conversations"
    files = list(conversations_dir.glob("my_session.json"))
    assert len(files) == 1


def test_export_creates_parent_dirs(fake_session, tmp_path, capsys):
    """/export --output creates parent dirs if they don't exist."""
    from lilith_cli import extra_commands as ec

    nested = tmp_path / "deep" / "nested" / "export.json"
    fake_session.history = [{"role": "user", "content": "x"}]

    _run(ec.run_export_command(fake_session, f"--output {nested}"))

    assert nested.exists()


def test_export_invalid_format(fake_session, capsys):
    """/export --format invalid shows usage error."""
    from lilith_cli import extra_commands as ec

    fake_session.history = []

    _run(ec.run_export_command(fake_session, "--format yaml"))

    # argparse rejects invalid choice, prints to stderr and calls SystemExit
    out = capsys.readouterr().out
    err = capsys.readouterr().err
    # Either error message or no success message
    assert "exportada" not in out or "invalid" in err.lower()