"""Tests for /capture --first N and --last N limiters."""

from __future__ import annotations

from lilith_cli.extra_commands import _capture_parse_args


def test_capture_no_args_first_last_both_none():
    """By default, both first_n and last_n are None (no limit)."""
    result = _capture_parse_args("")
    _1, _2, _3, _4, _5, _6, first_n, last_n = result
    assert first_n is None
    assert last_n is None


def test_capture_first_flag_with_positive_int():
    """/capture --first 5 sets first_n to 5."""
    result = _capture_parse_args("--first 5")
    _1, _2, _3, _4, _5, _6, first_n, last_n = result
    assert first_n == 5
    assert last_n is None


def test_capture_last_flag_with_positive_int():
    """/capture --last 3 sets last_n to 3."""
    result = _capture_parse_args("--last 3")
    _1, _2, _3, _4, _5, _6, first_n, last_n = result
    assert first_n is None
    assert last_n == 3


def test_capture_first_without_value_errors():
    """/capture --first (no value) returns None so the caller errors out."""
    result = _capture_parse_args("--first")
    assert result is None


def test_capture_first_with_non_int_errors():
    """/capture --first abc returns None with an error."""
    result = _capture_parse_args("--first abc")
    assert result is None


def test_capture_first_with_zero_errors():
    """/capture --first 0 errors (must be positive)."""
    result = _capture_parse_args("--first 0")
    assert result is None


def test_capture_last_without_value_errors():
    """/capture --last (no value) errors."""
    result = _capture_parse_args("--last")
    assert result is None


def test_capture_last_with_negative_errors():
    """/capture --last -1 errors (must be positive)."""
    result = _capture_parse_args("--last -1")
    assert result is None


def test_capture_first_combined_with_other_flags():
    """--first can combine with --tags, --exclude-system, --output."""
    result = _capture_parse_args(
        "--tags work --exclude-system --first 10 --output /tmp/out.md"
    )
    _1, _2, _3, _4, tags, _5, first_n, _6 = result
    assert tags == ["work"]
    assert first_n == 10


def test_capture_first_consumes_next_token():
    """/capture --first 10 mi-sesion puts 10 in first_n and 'mi-sesion'
    in the positional name (the flag doesn't swallow the next token)."""
    result = _capture_parse_args("--first 10 mi-sesion")
    name, _1, _2, _3, _4, _5, first_n, _6 = result
    assert first_n == 10
    assert name == "mi-sesion"
