"""Focused tests for FeedbackCommand.

Audit items 15-16 (deleg_d9685cd6): FeedbackCommand had no test_feedback_command.py
even though /feedback is the main user-feedback loop. The existing
test_commands.py only smoke-tested the constructor.

These tests cover the rating parse, comment handling, list/stats/clear
subcommands, and the persistence contract (entries written to the
JSON file with the right shape).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lilith_cli.commands import FeedbackCommand


class _Cfg:
    model = "t"
    provider = "t"


class _Sess:
    config = _Cfg()


# ── /feedback (no args) and bad rating ───────────────────────────────


@pytest.mark.asyncio
async def test_feedback_no_args_shows_usage(capsys):
    """/feedback with no args prints the usage hint, no error."""
    cmd = FeedbackCommand(_Sess())
    assert cmd.name == "feedback"
    assert "fb" in cmd.aliases

    await cmd.execute("")

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "list" in out
    assert "stats" in out
    assert "clear" in out


@pytest.mark.asyncio
async def test_feedback_non_numeric_rating_errors(capsys):
    """/feedback awesome respuesta must reject 'awesome' as rating."""
    cmd = FeedbackCommand(_Sess())
    await cmd.execute("awesome respuesta")

    out = capsys.readouterr().out
    assert "puntuación" in out.lower() or "entero" in out.lower()


@pytest.mark.asyncio
async def test_feedback_out_of_range_rating_errors(capsys):
    """Rating must be 1-5 inclusive; 0 and 6 both error."""
    cmd = FeedbackCommand(_Sess())
    await cmd.execute("0 genial")
    out = capsys.readouterr().out
    assert "1 y 5" in out

    await cmd.execute("6 pésimo")
    out = capsys.readouterr().out
    assert "1 y 5" in out


# ── /feedback <rating> [comment] persistence ─────────────────────────


@pytest.mark.asyncio
async def test_feedback_submission_persists_to_disk(tmp_path, monkeypatch):
    """/feedback 4 comentario must append an entry to feedback.json with
    the expected shape (id, rating, comment, created timestamp)."""
    feedback_file = tmp_path / "feedback.json"
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    cmd = FeedbackCommand(_Sess())
    await cmd.execute("4 muy buena respuesta")

    assert feedback_file.exists()
    data = json.loads(feedback_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    entry = data[0]
    assert entry["rating"] == 4
    assert entry["comment"] == "muy buena respuesta"
    assert entry["id"] == 1
    assert "created" in entry and "T" in entry["created"]


@pytest.mark.asyncio
async def test_feedback_submission_without_comment(tmp_path, monkeypatch, capsys):
    """/feedback 3 (no comment) must persist an entry with empty comment."""
    feedback_file = tmp_path / "feedback.json"
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    await FeedbackCommand(_Sess()).execute("3")

    data = json.loads(feedback_file.read_text(encoding="utf-8"))
    assert data[0]["rating"] == 3
    assert data[0]["comment"] == ""


@pytest.mark.asyncio
async def test_feedback_submission_increments_id(tmp_path, monkeypatch):
    """Each new entry's id is len(entries)+1, so a second /feedback gets id 2."""
    feedback_file = tmp_path / "feedback.json"
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    cmd = FeedbackCommand(_Sess())
    await cmd.execute("5 excelente")
    await cmd.execute("2 meh")

    data = json.loads(feedback_file.read_text(encoding="utf-8"))
    assert [e["id"] for e in data] == [1, 2]
    assert [e["rating"] for e in data] == [5, 2]


# ── /feedback list / stats / clear ──────────────────────────────────


@pytest.mark.asyncio
async def test_feedback_list_empty(tmp_path, monkeypatch, capsys):
    """/feedback list with no stored entries prints a dim 'no feedback' message."""
    feedback_file = tmp_path / "feedback.json"  # does not exist
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    await FeedbackCommand(_Sess()).execute("list")

    out = capsys.readouterr().out
    assert "No hay feedback" in out


@pytest.mark.asyncio
async def test_feedback_stats_empty(tmp_path, monkeypatch, capsys):
    """/feedback stats with no stored entries prints a dim 'no feedback' message
    instead of trying to compute min/max on an empty list."""
    feedback_file = tmp_path / "feedback.json"
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    await FeedbackCommand(_Sess()).execute("stats")

    out = capsys.readouterr().out
    assert "No hay feedback" in out


@pytest.mark.asyncio
async def test_feedback_stats_with_entries(tmp_path, monkeypatch, capsys):
    """/feedback stats computes average/min/max from stored entries."""
    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text(
        json.dumps(
            [
                {"id": 1, "rating": 5, "comment": "a", "created": "2026-07-18T10:00:00+00:00"},
                {"id": 2, "rating": 3, "comment": "b", "created": "2026-07-18T11:00:00+00:00"},
                {"id": 3, "rating": 4, "comment": "c", "created": "2026-07-18T12:00:00+00:00"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    await FeedbackCommand(_Sess()).execute("stats")

    out = capsys.readouterr().out
    assert "Total de entradas: 3" in out
    assert "4.00" in out   # avg of 5,3,4
    assert "3" in out      # min
    assert "5" in out      # max


@pytest.mark.asyncio
async def test_feedback_clear_empties_file(tmp_path, monkeypatch):
    """/feedback clear rewrites feedback.json as an empty list."""
    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text(
        json.dumps([{"id": 1, "rating": 5, "comment": "x", "created": "now"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    await FeedbackCommand(_Sess()).execute("clear")

    data = json.loads(feedback_file.read_text(encoding="utf-8"))
    assert data == []


@pytest.mark.asyncio
async def test_feedback_clear_when_empty_prints_dim_message(
    tmp_path, monkeypatch, capsys
):
    """/feedback clear with no stored entries says so instead of doing nothing silently."""
    feedback_file = tmp_path / "feedback.json"
    monkeypatch.setattr("lilith_cli.commands._FEEDBACK_PATH", feedback_file)

    await FeedbackCommand(_Sess()).execute("clear")

    out = capsys.readouterr().out
    assert "No hay feedback" in out
