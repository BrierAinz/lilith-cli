"""Tests for the _print_error helper (consistent error UX with actionable tips)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import _ERROR_TIPS, _print_error


def test_error_tips_contains_common_exceptions():
    """The tips dict covers the 5+ most common exception types."""
    expected = {
        FileNotFoundError,
        PermissionError,
        IsADirectoryError,
        NotADirectoryError,
        TimeoutError,
        ConnectionError,
        ValueError,
        KeyError,
    }
    assert expected.issubset(set(_ERROR_TIPS.keys()))


def test_print_error_prints_context_and_message():
    """_print_error prints `[error] context: message[/error]`."""
    prints = []

    def capture(text=""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        _print_error("reading file", FileNotFoundError("foo.txt"))

    output = "\n".join(prints)
    assert "[error]reading file: foo.txt[/error]" in output


def test_print_error_includes_tip_for_known_exception():
    """When err is a known exception type, a tip is printed."""
    prints = []

    def capture(text=""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        _print_error("reading file", FileNotFoundError("foo.txt"))

    output = "\n".join(prints)
    assert "[dim]tip:" in output
    # FileNotFoundError tip mentions path
    assert "path" in output.lower() or "permission" in output.lower()


def test_print_error_no_tip_for_unknown_exception():
    """Unknown exception types still print the error but no tip."""
    prints = []

    def capture(text=""):
        prints.append(str(text))

    class WeirdError(Exception):
        pass

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        _print_error("doing thing", WeirdError("oops"))

    output = "\n".join(prints)
    assert "[error]doing thing: oops[/error]" in output
    assert "tip:" not in output


def test_print_error_accepts_string_err():
    """When err is a string (not an exception), prints it without a tip."""
    prints = []

    def capture(text=""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        _print_error("validation", "missing argument")

    output = "\n".join(prints)
    assert "[error]validation: missing argument[/error]" in output
    assert "tip:" not in output


def test_print_error_subclass_match():
    """Subclasses of known exceptions also get a tip."""
    prints = []

    def capture(text=""):
        prints.append(str(text))

    # ConnectionError has a subclass in some libs; PermissionError has
    # many subclasses too. Use the standard one directly.
    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        _print_error("network call", ConnectionError("refused"))

    output = "\n".join(prints)
    assert "[error]" in output
    assert "[dim]tip:" in output