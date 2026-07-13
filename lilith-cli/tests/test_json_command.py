"""Tests for /json slash command."""

from __future__ import annotations

import asyncio
import json


def _run(coro):
    return asyncio.run(coro)


def test_json_validate_object(fake_session, capsys):
    """/json with valid JSON object validates and pretty-prints."""
    from lilith_cli.extra_commands import run_json_command

    _run(run_json_command(fake_session, '{"name": "test", "value": 42}'))

    out = capsys.readouterr().out
    assert "V\u00e1lido" in out
    assert "dict" in out
    # Pretty-printed keys appear
    assert '"name"' in out
    assert '"value"' in out


def test_json_validate_array(fake_session, capsys):
    """/json with valid JSON array detects list type."""
    from lilith_cli.extra_commands import run_json_command

    _run(run_json_command(fake_session, '[1, 2, 3, "four"]'))

    out = capsys.readouterr().out
    assert "V\u00e1lido" in out
    assert "list" in out
    assert "4" in out  # length


def test_json_invalid(fake_session, capsys):
    """/json with invalid JSON shows clear error (no crash)."""
    from lilith_cli.extra_commands import run_json_command

    _run(run_json_command(fake_session, "{not valid json"))

    out = capsys.readouterr().out
    assert "inv\u00e1lido" in out.lower() or "invalid" in out.lower()


def test_json_no_args(fake_session, capsys):
    """/json with no args shows usage."""
    from lilith_cli.extra_commands import run_json_command

    _run(run_json_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_json_from_file(fake_session, capsys, tmp_path):
    """/json <file> reads and validates JSON from file."""
    from lilith_cli.extra_commands import run_json_command

    f = tmp_path / "config.json"
    data = {"server": "localhost", "port": 8080, "debug": True}
    f.write_text(json.dumps(data))

    _run(run_json_command(fake_session, str(f)))

    out = capsys.readouterr().out
    assert "V\u00e1lido" in out
    assert "archivo:" in out
    assert "config.json" in out
    assert '"server"' in out


def test_json_unicode_preserved(fake_session, capsys):
    """/json preserves unicode characters (no escape)."""
    from lilith_cli.extra_commands import run_json_command

    _run(run_json_command(fake_session, '{"mensaje": "Lilith en espa\u00f1ol"}'))

    out = capsys.readouterr().out
    # ensure_ascii=False should preserve the literal "ñ"
    assert "espa\u00f1ol" in out


def test_json_sorted_keys(fake_session, capsys):
    """/json output has keys sorted alphabetically."""
    from lilith_cli.extra_commands import run_json_command

    _run(run_json_command(fake_session, '{"z": 1, "a": 2, "m": 3}'))

    out = capsys.readouterr().out
    # Find positions of each key
    pos_a = out.find('"a"')
    pos_m = out.find('"m"')
    pos_z = out.find('"z"')
    assert pos_a < pos_m < pos_z