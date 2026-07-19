"""Tests for ResumeCommand and the _list_saved_conversations /
_load_conversation helpers it depends on.

Item 17 of the audit (deleg_d9685cd6): there was no test_resume_command.py
even though /resume is one of the discoverable slash commands. The
previous surface tests only verified the constructor name.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lilith_cli.repl import _list_saved_conversations, _load_conversation


# ── helpers ──────────────────────────────────────────────────────────


def _write_conv(dirpath: Path, name: str, messages: list[dict], *, model: str = "test") -> Path:
    """Drop a conversation file matching the conv_*.json glob."""
    fpath = dirpath / f"conv_{name}.json"
    fpath.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-18T12:00:00+00:00",
                "model": model,
                "provider": "test",
                "messages": messages,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fpath


# ── _list_saved_conversations ────────────────────────────────────────


def test_list_conversations_empty_when_dir_missing(monkeypatch, tmp_path):
    """If ~/.yggdrasil/conversations/ doesn't exist, return [] cleanly."""
    # Point _CONVERSATIONS_DIR at a non-existent subdir.
    fake = tmp_path / "does-not-exist"
    with patch("lilith_cli.repl._CONVERSATIONS_DIR", fake):
        assert _list_saved_conversations() == []


def test_list_conversations_returns_sorted_metadata(monkeypatch, tmp_path):
    _write_conv(tmp_path, "first", [{"role": "user", "content": "hola"}])
    _write_conv(tmp_path, "second", [{"role": "user", "content": "chau"}], model="gpt-x")

    with patch("lilith_cli.repl._CONVERSATIONS_DIR", tmp_path):
        items = _list_saved_conversations()

    assert len(items) == 2
    # Sorted by glob reverse (alphabetical desc), so "second" first.
    assert items[0]["name"] == "conv_second"
    assert items[0]["model"] == "gpt-x"
    assert items[0]["preview"] == "chau"
    assert items[1]["preview"] == "hola"


def test_list_conversations_surfaces_corruption(monkeypatch, tmp_path, capsys):
    """A broken conv_*.json must NOT be silently dropped: it must print a
    yellow warning with the file name and the underlying exception so
    the user can rename/delete it (audit item 5)."""
    good = _write_conv(tmp_path, "good", [{"role": "user", "content": "ok"}])
    bad = tmp_path / "conv_broken.json"
    bad.write_text("this is { not json", encoding="utf-8")

    with patch("lilith_cli.repl._CONVERSATIONS_DIR", tmp_path):
        items = _list_saved_conversations()

    assert len(items) == 1
    assert items[0]["file"] == good
    out = capsys.readouterr().out
    assert "conv_broken.json" in out
    assert "No pude leer" in out


def test_list_conversations_skips_non_conv_files(monkeypatch, tmp_path):
    """Only conv_*.json files are picked up; arbitrary .json files are ignored."""
    _write_conv(tmp_path, "real", [{"role": "user", "content": "x"}])
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")

    with patch("lilith_cli.repl._CONVERSATIONS_DIR", tmp_path):
        items = _list_saved_conversations()

    assert [i["name"] for i in items] == ["conv_real"]


# ── _load_conversation ───────────────────────────────────────────────


def test_load_conversation_returns_data(tmp_path):
    fpath = _write_conv(tmp_path, "x", [{"role": "user", "content": "hi"}])
    data = _load_conversation(fpath)
    assert isinstance(data, dict)
    assert data["model"] == "test"


def test_load_conversation_missing_file(tmp_path, capsys):
    result = _load_conversation(tmp_path / "nope.json")
    assert result is None
    out = capsys.readouterr().out
    assert "no encontrado" in out.lower()
    assert "nope.json" in out


def test_load_conversation_bad_json_includes_line_col(tmp_path, capsys):
    """JSONDecodeError surfaces line/column to help the user fix or delete
    the file (audit item 6)."""
    fpath = tmp_path / "conv_bad.json"
    fpath.write_text('{"messages": [\n  {"role": "user"\n}', encoding="utf-8")
    assert _load_conversation(fpath) is None
    out = capsys.readouterr().out
    assert "conv_bad.json" in out
    assert "JSON" in out or "json" in out
    assert "/resume" in out  # hint to recover


# ── ResumeCommand surface ────────────────────────────────────────────


def test_resume_command_metadata():
    from lilith_cli.commands import ResumeCommand

    class _Cfg:
        model = "t"
        provider = "t"

    class _S:
        config = _Cfg()

    cmd = ResumeCommand(_S())
    assert cmd.name == "resume"
    assert "load" in cmd.aliases
    assert cmd.description  # non-empty
