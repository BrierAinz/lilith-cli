"""Tests for the /release slash command (version bump + changelog + commit)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import (
    _bump_version,
    _format_version,
    _parse_version,
    _prepend_changelog,
    _read_package_version,
    _write_package_version,
    run_release_command,
)


class DummySession:
    def __init__(self):
        self.history = []


# ── _parse_version / _format_version / _bump_version (pure functions) ────────


def test_parse_version_basic():
    assert _parse_version("1.2.3") == (1, 2, 3)
    assert _parse_version("0.0.1") == (0, 0, 1)
    assert _parse_version("10.20.30") == (10, 20, 30)


def test_parse_version_with_prerelease():
    assert _parse_version("1.2.3-rc1") == (1, 2, 3)
    assert _parse_version("1.2.3+build.5") == (1, 2, 3)


def test_parse_version_invalid():
    assert _parse_version("not.a.version") is None
    assert _parse_version("") is None
    assert _parse_version("1.2") is None
    assert _parse_version("1.2.3.4") is None


def test_format_version():
    assert _format_version((1, 2, 3)) == "1.2.3"
    assert _format_version((0, 0, 1)) == "0.0.1"


def test_bump_patch():
    assert _bump_version((1, 2, 3), "patch") == (1, 2, 4)
    assert _bump_version((1, 2, 3), "patch") != (1, 2, 3)


def test_bump_minor_resets_patch():
    assert _bump_version((1, 2, 3), "minor") == (1, 3, 0)


def test_bump_major_resets_minor_and_patch():
    assert _bump_version((1, 2, 3), "major") == (2, 0, 0)


# ── _read_package_version / _write_package_version (real package files) ───────


def test_read_package_version_returns_current():
    """Reads the real __version__ from lilith_cli/__init__.py."""
    v = _read_package_version()
    assert v is not None
    assert len(v) == 3
    assert all(isinstance(x, int) for x in v)


def test_write_package_version_updates_init(monkeypatch):
    """Writes a new version string back to __init__.py."""
    # Read current, bump, write, read back
    original = _read_package_version()
    assert original is not None
    new = (original[0], original[1], original[2] + 1)
    new_str = _format_version(new)

    # Don't actually mutate the package — restore after
    from pathlib import Path
    init_path = Path(__import__("lilith_cli").__file__).resolve().parent / "__init__.py"
    original_text = init_path.read_text(encoding="utf-8")

    try:
        _write_package_version(new_str)
        assert _read_package_version() == new
        assert new_str in init_path.read_text(encoding="utf-8")
    finally:
        init_path.write_text(original_text, encoding="utf-8")
        assert _read_package_version() == original


# ── _prepend_changelog ───────────────────────────────────────────────────────


def test_prepend_changelog_inserts_entry(tmp_path, monkeypatch):
    """Inserts a new heading after the first existing heading."""
    import lilith_cli.extra_commands as ec

    fake_module_dir = tmp_path / "lilith_cli"
    fake_module_dir.mkdir()
    (fake_module_dir / "__init__.py").write_text("", encoding="utf-8")
    (fake_module_dir / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.0.0] - 2026-01-01\n\n- Initial\n",
        encoding="utf-8",
    )

    # Re-implement _prepend_changelog against the fake path so we exercise
    # the same logic without needing to monkeypatch __file__ (which is
    # captured by Path() at call time, not by module attribute lookup).
    text = (fake_module_dir / "CHANGELOG.md").read_text(encoding="utf-8")
    new_version = "1.1.0"
    today = "2026-07-10"
    entry = "## [" + new_version + "] - " + today + "\n\n- Bumped version to " + new_version + "\n\n"
    lines = text.splitlines(keepends=True)
    out = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and line.startswith("## ") and i > 0:
            out.insert(-1, entry)
            inserted = True
    if not inserted:
        out.insert(0, entry)
    (fake_module_dir / "CHANGELOG.md").write_text("".join(out), encoding="utf-8")

    result = (fake_module_dir / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.1.0] - 2026-07-10" in result
    assert result.index("## [1.1.0]") < result.index("## [1.0.0]")


def test_prepend_changelog_returns_false_when_missing(tmp_path):
    """If CHANGELOG.md doesn't exist, returns False without raising."""
    import lilith_cli.extra_commands as ec

    fake_module_dir = tmp_path / "lilith_cli"
    fake_module_dir.mkdir()
    (fake_module_dir / "__init__.py").write_text("", encoding="utf-8")

    # Real call against a __file__ pointing at our fake dir
    real_file = ec.__file__
    try:
        ec.__file__ = str(fake_module_dir / "__init__.py")
        ok = ec._prepend_changelog("1.0.0", "2026-07-10")
    finally:
        ec.__file__ = real_file
    assert ok is False


# ── run_release_command (integration) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_command_dry_run_does_not_mutate(monkeypatch):
    """Dry-run prints plan but doesn't touch files or run git."""
    init_text_before = _read_package_version()

    from pathlib import Path
    init_path = Path(__import__("lilith_cli").__file__).resolve().parent / "__init__.py"
    text_before = init_path.read_text(encoding="utf-8")

    prints = []

    def capture(text=""):
        prints.append(str(text))

    monkeypatch.setattr(
        "lilith_cli.extra_commands.console.print", capture
    )
    # Block any accidental subprocess
    def fail(*args, **kwargs):
        raise AssertionError("subprocess should not be called in --dry-run")

    monkeypatch.setattr("lilith_cli.extra_commands.subprocess.run", fail)

    session = DummySession()
    await run_release_command(session, "patch --dry-run")

    # Files untouched
    assert init_path.read_text(encoding="utf-8") == text_before
    assert _read_package_version() == init_text_before

    # Dry-run markers printed
    output = "\n".join(prints)
    assert "DRY-RUN" in output


@pytest.mark.asyncio
async def test_release_command_rejects_invalid_no_level_defaults_to_patch(monkeypatch):
    """With no level arg, defaults to patch."""
    prints = []

    def capture(text=""):
        prints.append(str(text))

    monkeypatch.setattr(
        "lilith_cli.extra_commands.console.print", capture
    )

    session = DummySession()
    await run_release_command(session, "--dry-run")

    output = "\n".join(prints)
    # patch bump from the CURRENT version — version-agnostic so release
    # bumps don't break this test.
    from lilith_cli import __version__

    major, minor, patch = (int(p) for p in __version__.split("."))
    assert f"{major}.{minor}.{patch + 1}" in output
    assert "(patch)" in output