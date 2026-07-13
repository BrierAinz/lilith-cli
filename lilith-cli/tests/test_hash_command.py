"""Tests for /hash slash command."""

from __future__ import annotations

import asyncio
import hashlib


def _run(coro):
    return asyncio.run(coro)


def test_hash_md5_text(fake_session, capsys):
    """/hash md5 <text> returns correct md5 digest."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, "md5 hello"))

    out = capsys.readouterr().out
    expected = hashlib.md5(b"hello").hexdigest()
    assert expected in out
    assert "md5" in out


def test_hash_sha256_text(fake_session, capsys):
    """/hash sha256 <text> returns correct sha256 digest."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, "sha256 Lilith"))

    out = capsys.readouterr().out
    expected = hashlib.sha256(b"Lilith").hexdigest()
    assert expected in out
    assert "sha256" in out


def test_hash_sha512_text(fake_session, capsys):
    """/hash sha512 <text> returns correct sha512 digest."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, "sha512 test"))

    # Strip whitespace for long-digest wrap safety
    out = capsys.readouterr().out.replace("\n", "").replace(" ", "")
    expected = hashlib.sha512(b"test").hexdigest()
    assert expected in out


def test_hash_sha1_text(fake_session, capsys):
    """/hash sha1 <text> returns correct sha1 digest."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, "sha1 Lilith"))

    out = capsys.readouterr().out
    expected = hashlib.sha1(b"Lilith").hexdigest()
    assert expected in out


def test_hash_file(fake_session, capsys, tmp_path):
    """/hash <algo> <file> returns digest of file contents."""
    from lilith_cli.extra_commands import run_hash_command

    f = tmp_path / "data.txt"
    f.write_text("hello")
    _run(run_hash_command(fake_session, f"md5 {f}"))

    out = capsys.readouterr().out
    expected = hashlib.md5(b"hello").hexdigest()
    assert expected in out
    assert "archivo" in out


def test_hash_unsupported_algo(fake_session, capsys):
    """/hash with unsupported algorithm shows error."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, "whirlpool foo"))

    out = capsys.readouterr().out
    assert "no soportado" in out.lower() or "supported" in out.lower()


def test_hash_no_args(fake_session, capsys):
    """/hash with no args shows usage."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_hash_algo_only(fake_session, capsys):
    """/hash <algo> with no target shows usage."""
    from lilith_cli.extra_commands import run_hash_command

    _run(run_hash_command(fake_session, "md5"))

    out = capsys.readouterr().out
    assert "Uso:" in out