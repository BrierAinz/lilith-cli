"""Tests for /changelog --list flag."""

from __future__ import annotations

import asyncio
from pathlib import Path


def _run(coro):
    return asyncio.run(coro)


def test_changelog_list_shows_versions(fake_session, tmp_path, monkeypatch, capsys):
    """/changelog --list shows only version numbers."""
    from lilith_cli import extra_commands as ec

    # Redirect CHANGELOG_PATH to a tmp file with multiple versions
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\r\n\r\n"
        "## [4.3.0] - 2026-07-10\r\n### Added\r\n- New /changelog command.\r\n\r\n"
        "## [4.2.0] - 2026-07-07\r\n### Changed\r\n- Improved truncation.\r\n\r\n"
        "## [4.1.0] - 2026-07-01\r\n### Added\r\n- Initial slash commands.\r\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.extra_commands.CHANGELOG_PATH", changelog)

    _run(ec.run_changelog_command(fake_session, "--list"))

    out = capsys.readouterr().out
    assert "Versiones disponibles" in out
    assert "v4.3.0" in out
    assert "v4.2.0" in out
    assert "v4.1.0" in out
    # NO content lines (only version headers)
    assert "Initial slash commands" not in out
    assert "Improved truncation" not in out


def test_changelog_list_alias(fake_session, tmp_path, monkeypatch, capsys):
    """/changelog list also works (alias for --list)."""
    from lilith_cli import extra_commands as ec

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\r\n\r\n## [1.0.0]\r\n- Initial.\r\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.extra_commands.CHANGELOG_PATH", changelog)

    _run(ec.run_changelog_command(fake_session, "list"))

    out = capsys.readouterr().out
    assert "Versiones disponibles" in out
    assert "v1.0.0" in out


def test_changelog_list_empty_changelog(fake_session, tmp_path, monkeypatch, capsys):
    """/changelog --list on empty changelog shows friendly message."""
    from lilith_cli import extra_commands as ec

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\r\n", encoding="utf-8")
    monkeypatch.setattr("lilith_cli.extra_commands.CHANGELOG_PATH", changelog)

    _run(ec.run_changelog_command(fake_session, "--list"))

    out = capsys.readouterr().out
    assert "No hay entradas" in out or "empty" in out.lower()