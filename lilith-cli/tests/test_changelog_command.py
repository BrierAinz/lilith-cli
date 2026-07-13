"""Tests for /changelog slash command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import (
    CHANGELOG_PATH,
    _parse_changelog_entries,
    run_changelog_command,
)


class DummySession:
    def __init__(self):
        self.config = SimpleNamespace(
            model="test",
            provider="test",
            providers={},
            api_key="",
            system_prompt="",
        )
        self.memory = None
        self.history = []
        self.provider = None


@pytest.fixture
def changelog(tmp_path: Path, monkeypatch):
    """Create a temporary CHANGELOG.md and point the command at it."""
    original = CHANGELOG_PATH
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(
        "# Changelog\n\n"
        "## [4.3.0] - 2026-07-10\n\n"
        "### Added\n- New /changelog command.\n\n"
        "## [4.2.0] - 2026-07-07\n\n"
        "### Changed\n- Improved truncation.\n- Better stream handling.\n\n"
        "## [4.1.0] - 2026-07-01\n\n"
        "### Added\n- Initial slash commands.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.extra_commands.CHANGELOG_PATH", changelog_file)
    return changelog_file


@pytest.mark.asyncio
async def test_changelog_default_shows_latest_entries(changelog):
    """/changelog sin argumentos muestra las entradas más recientes."""
    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_changelog_command(session, "")

    output = "\n".join(prints)
    assert "v4.3.0" in output
    assert "New /changelog command" in output
    assert "v4.2.0" in output
    assert "v4.1.0" in output


@pytest.mark.asyncio
async def test_changelog_specific_version(changelog):
    """/changelog <versión> muestra solo los cambios de esa versión."""
    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_changelog_command(session, "4.2.0")

    output = "\n".join(prints)
    assert "v4.2.0" in output
    assert "Improved truncation" in output
    assert "Better stream handling" in output
    assert "v4.3.0" not in output
    assert "v4.1.0" not in output


@pytest.mark.asyncio
async def test_changelog_unknown_version(changelog):
    """/changelog con una versión inexistente muestra un aviso y lista las disponibles."""
    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.render_error", side_effect=capture):
        await run_changelog_command(session, "9.9.9")

    output = "\n".join(prints)
    assert "No se encontró la versión" in output
    # New: now lists available versions
    assert "Disponibles:" in output
    assert "4.3.0" in output
    assert "4.2.0" in output


def test_parse_changelog_entries_groups_by_version():
    """El parser agrupa el markdown por encabezados de versión."""
    content = (
        "# Changelog\n\n"
        "## [1.0.0]\n### Added\n- Feature A\n\n"
        "## [1.1.0]\n### Fixed\n- Bugfix B\n"
    )
    entries = _parse_changelog_entries(content)

    assert len(entries) == 2
    assert entries[0]["version"] == "1.0.0"
    assert any("Feature A" in line for line in entries[0]["lines"])
    assert entries[1]["version"] == "1.1.0"
    assert any("Bugfix B" in line for line in entries[1]["lines"])
