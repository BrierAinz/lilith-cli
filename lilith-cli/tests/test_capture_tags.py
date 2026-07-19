"""Tests for /capture --tags argument parsing."""

from __future__ import annotations

from lilith_cli.extra_commands import _capture_parse_args


def test_capture_no_args():
    result = _capture_parse_args("")
    assert result == (None, None, False, True, [], False, None, None)


def test_capture_name_only():
    """A positional name goes into the name slot; tags stays empty."""
    result = _capture_parse_args("mi-sesion")
    assert result == ("mi-sesion", None, False, True, [], False, None, None)


def test_capture_tags_space_separated():
    """/capture --tags foo bar baz parses three tags."""
    result = _capture_parse_args("--tags work urgent")
    name, path, tools, usage, tags, _, first_n, last_n = result
    assert name is None
    assert path is None
    assert tools is False
    assert usage is True
    assert tags == ["work", "urgent"]


def test_capture_tags_with_hash_prefix_stripped():
    """Tags written with leading # get the hash stripped for the
    transcript tag line."""
    result = _capture_parse_args("--tags #work #urgent")
    _, _, _, _, tags, _, first_n, last_n = result
    assert tags == ["work", "urgent"]


def test_capture_tags_equals_form_comma_separated():
    """/capture --tags=work,urgent works without spaces after the =."""
    result = _capture_parse_args("--tags=work,urgent,review")
    _, _, _, _, tags, _, first_n, last_n = result
    assert tags == ["work", "urgent", "review"]


def test_capture_tags_combined_with_other_flags():
    """Tags can be combined with --include-tools and --no-usage."""
    result = _capture_parse_args("mi-sesion --tags work --include-tools --no-usage")
    name, path, tools, usage, tags, _, first_n, last_n = result
    assert name == "mi-sesion"
    assert tools is True
    assert usage is False
    assert tags == ["work"]


def test_capture_tags_with_output_path():
    """/capture --output C:/path --tags foo doesn't confuse the parser."""
    result = _capture_parse_args("--output C:/transcripts/foo.md --tags review")
    name, path, tools, usage, tags, _, first_n, last_n = result
    assert path == "C:/transcripts/foo.md"
    assert tags == ["review"]


def test_capture_empty_tags_errors():
    """/capture --tags (no value) returns None so the caller errors out."""
    result = _capture_parse_args("--tags")
    assert result is None


def test_capture_empty_tags_equals_errors():
    """/capture --tags= (empty value) returns None."""
    result = _capture_parse_args("--tags=")
    assert result is None


def test_capture_tags_filters_empty_strings():
    """/capture --tags ,,,work,,,urgent filters out the empty pieces."""
    result = _capture_parse_args("--tags ,,, work ,,, urgent ,,,")
    _, _, _, _, tags, _, first_n, last_n = result
    assert tags == ["work", "urgent"]


def test_capture_unknown_flag_errors():
    """/capture --foo errors (forward compat: explicit unknown flag = error)."""
    result = _capture_parse_args("--foo bar")
    assert result is None
