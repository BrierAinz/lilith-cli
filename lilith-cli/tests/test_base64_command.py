"""Tests for /base64 slash command."""

from __future__ import annotations

import asyncio
import base64


def _run(coro):
    return asyncio.run(coro)


def test_base64_encode_text(fake_session, capsys):
    """/base64 encode <text> returns base64-encoded string."""
    from lilith_cli.extra_commands import run_base64_command

    _run(run_base64_command(fake_session, "encode hello"))

    out = capsys.readouterr().out
    expected = base64.b64encode(b"hello").decode("ascii")
    assert expected in out
    assert "encoded:" in out


def test_base64_decode_text(fake_session, capsys):
    """/base64 decode <base64> returns decoded UTF-8 text."""
    from lilith_cli.extra_commands import run_base64_command

    encoded = base64.b64encode(b"Lilith").decode("ascii")
    _run(run_base64_command(fake_session, f"decode {encoded}"))

    out = capsys.readouterr().out
    assert "Lilith" in out
    assert "decoded:" in out


def test_base64_encode_decode_roundtrip(fake_session, capsys):
    """Roundtrip: encode then decode returns original."""
    from lilith_cli.extra_commands import run_base64_command

    original = "Hermes CLI rocks!"

    _run(run_base64_command(fake_session, f"encode {original}"))
    out1 = capsys.readouterr().out
    # Extract encoded from output
    encoded_line = [line for line in out1.split("\n") if "encoded:" in line][0]
    encoded = encoded_line.split("encoded:")[-1].strip()

    # Decode it
    _run(run_base64_command(fake_session, f"decode {encoded}"))
    out2 = capsys.readouterr().out
    assert original in out2


def test_base64_decode_invalid(fake_session, capsys):
    """/base64 decode with invalid base64 shows error (no crash)."""
    from lilith_cli.extra_commands import run_base64_command

    _run(run_base64_command(fake_session, "decode !!!not-valid-base64!!!"))

    out = capsys.readouterr().out
    assert "inv\u00e1lido" in out.lower() or "invalid" in out.lower() or "error" in out.lower()


def test_base64_no_args(fake_session, capsys):
    """/base64 with no args shows usage."""
    from lilith_cli.extra_commands import run_base64_command

    _run(run_base64_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_base64_invalid_op(fake_session, capsys):
    """/base64 with invalid op shows usage."""
    from lilith_cli.extra_commands import run_base64_command

    _run(run_base64_command(fake_session, "encrypt hello"))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_base64_op_only(fake_session, capsys):
    """/base64 encode with no target shows usage."""
    from lilith_cli.extra_commands import run_base64_command

    _run(run_base64_command(fake_session, "encode"))

    out = capsys.readouterr().out
    assert "Uso:" in out