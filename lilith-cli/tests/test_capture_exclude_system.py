"""Tests for /capture --exclude-system argument parsing."""

from __future__ import annotations

from lilith_cli.extra_commands import _capture_parse_args


def test_capture_no_args_exclude_system_false():
    """By default, exclude_system is False (backward compat)."""
    result = _capture_parse_args("")
    name, path, tools, usage, tags, exclude_system = result
    assert exclude_system is False


def test_capture_exclude_system_flag():
    """/capture --exclude-system flips the flag to True."""
    result = _capture_parse_args("--exclude-system")
    _, _, _, _, _, exclude_system = result
    assert exclude_system is True


def test_capture_exclude_system_with_name():
    """/capture mi-sesion --exclude-system works alongside a positional name."""
    result = _capture_parse_args("mi-sesion --exclude-system")
    name, _, _, _, _, exclude_system = result
    assert name == "mi-sesion"
    assert exclude_system is True


def test_capture_exclude_system_combined_with_other_flags():
    """--exclude-system composes with --tags and --include-tools without conflict."""
    result = _capture_parse_args(
        "--tags work,urgent --exclude-system --include-tools"
    )
    _, _, tools, _, tags, exclude_system = result
    assert tags == ["work", "urgent"]
    assert tools is True
    assert exclude_system is True


def test_capture_exclude_system_does_not_require_value():
    """--exclude-system is a flag, not a key=value, so it should never
    consume the next token as its value."""
    result = _capture_parse_args("--exclude-system mi-sesion")
    name, _, _, _, _, exclude_system = result
    # The 'mi-sesion' must be captured as the positional name (the
    # --exclude-system flag doesn't consume it as a value).
    assert name == "mi-sesion"
    assert exclude_system is True
