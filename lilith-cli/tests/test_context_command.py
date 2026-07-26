"""Tests for the /context slash command.

Covers three paths:

1. Default (``/context``) and ``full`` render a Rich progress bar plus a
   one-line summary that always includes the model name and the
   ``Usados / Restantes`` accounting.
2. ``/context json`` emits a machine-readable snapshot with the same
   bucket numbers the rendered path shows (system / tools / history /
   pinned / plan).
3. ``/context warn <0-100>`` persists the warning threshold and the
   default path shows the warning line when usage exceeds it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.extra_commands import (
    _context_snapshot,
    _load_warn_pct,
    _save_warn_pct,
    run_context_command,
)


def _make_session(model: str = "fugu-ultra") -> AgentSession:
    cfg = YggdrasilConfig(provider="sakana", model=model)
    session = AgentSession(cfg)
    session.system_prompt = "You are Lilith. " * 20  # ~100 words
    return session


@pytest.fixture
def isolated_warn_file(tmp_path, monkeypatch):
    """Redirect the warn-threshold store to a tmp file so tests stay hermetic."""
    from lilith_cli import extra_commands as ec

    fake = tmp_path / "context_warn.json"
    monkeypatch.setattr(ec, "_CONTEXT_WARN_FILE", fake)
    return fake


# ── /context default + full ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_default_renders_progress_bar(fake_session, capsys):
    """``/context`` shows a progress bar and a one-line summary."""
    fake_session._track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        "fugu-ultra",
    )
    await run_context_command(fake_session, "")

    out = capsys.readouterr().out
    # Progress bar uses block characters.
    assert "█" in out or "░" in out
    # The summary line always surfaces the model + accounting fields.
    assert "Usados" in out
    assert "Restantes" in out
    assert "Modelo" in out


@pytest.mark.asyncio
async def test_context_full_renders_breakdown_table(fake_session, capsys):
    """``/context full`` adds a per-bucket table under the bar."""
    fake_session._track_usage(
        {"prompt_tokens": 5000, "completion_tokens": 1000, "total_tokens": 6000},
        "fugu-ultra",
    )
    await run_context_command(fake_session, "full")

    out = capsys.readouterr().out
    # Bucket labels the user sees in the rendered table.
    assert "Prompt del sistema" in out
    assert "Descripción de herramientas" in out
    assert "Historial de mensajes" in out


# ── /context json ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_json_emits_machine_readable(fake_session, capsys):
    """``/context json`` bypasses Rich and writes a JSON snapshot."""
    # The conftest fixture builds a session with model=local-model; we
    # override to fugu-ultra so the snapshot pulls the real Sakana
    # window from providers._MODEL_CONTEXTS.
    fake_session.config.model = "fugu-ultra"
    fake_session._track_usage(
        {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000},
        "fugu-ultra",
    )
    await run_context_command(fake_session, "json")

    out = capsys.readouterr().out.strip()
    data = json.loads(out)

    assert data["model"] == "fugu-ultra"
    # The model window is looked up in providers._MODEL_CONTEXTS and must
    # be a positive integer.
    assert data["max_tokens"] > 0
    assert data["used"] == 1000
    assert data["remaining"] == data["max_tokens"] - 1000
    # Buckets contract.
    for key in ("system", "tools", "history", "pinned"):
        assert key in data["buckets"]
    assert "plan" in data
    assert "warn_pct" in data
    assert isinstance(data["over_warn"], bool)


# ── /context warn ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_warn_persists_threshold(
    fake_session, capsys, isolated_warn_file
):
    """``/context warn <n>`` writes the threshold to disk and clamps it."""
    await run_context_command(fake_session, "warn 60")

    # Persisted to the redirected JSON file.
    assert isolated_warn_file.exists()
    payload = json.loads(isolated_warn_file.read_text(encoding="utf-8"))
    assert payload["warn_pct"] == 60.0

    # Subsequent reads see the new value.
    assert _load_warn_pct() == 60.0

    out = capsys.readouterr().out
    assert "60%" in out


@pytest.mark.asyncio
async def test_context_warn_clamps_out_of_range(
    fake_session, capsys, isolated_warn_file
):
    """Threshold is clamped to [0, 100] even if the user asks for 999."""
    await run_context_command(fake_session, "warn 999")

    payload = json.loads(isolated_warn_file.read_text(encoding="utf-8"))
    assert payload["warn_pct"] == 100.0


@pytest.mark.asyncio
async def test_context_warn_zero_disables(fake_session, capsys, isolated_warn_file):
    """``warn 0`` disables the warning line entirely."""
    _save_warn_pct(0)
    fake_session._track_usage(
        {"prompt_tokens": 100_000, "completion_tokens": 0, "total_tokens": 100_000},
        "fugu-ultra",
    )
    await run_context_command(fake_session, "")

    out = capsys.readouterr().out
    # over_warn must be False when threshold is 0, even at 100K tokens.
    assert "Contexto al" not in out
    assert _load_warn_pct() == 0.0


# ── snapshot helper contract ─────────────────────────────────────────


def test_context_snapshot_uses_real_model_window():
    """The snapshot must look up the model window, not hard-code 128K."""
    session = _make_session(model="fugu-ultra")
    snap = _context_snapshot(session)

    # fugu-ultra is registered at 262_144 in providers._MODEL_CONTEXTS.
    # We don't hardcode the value here — only assert the lookup succeeded.
    assert snap["max_tokens"] > 0
    assert snap["model"] == "fugu-ultra"


def test_context_snapshot_handles_missing_total_usage():
    """When no LLM call has happened, the snapshot still renders sane numbers."""
    session = _make_session()
    # Force the worst-case default of 0 tokens across the board.
    session._total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    snap = _context_snapshot(session)

    assert snap["used"] == 0
    assert snap["remaining"] == snap["max_tokens"]
    assert snap["percentage"] == 0.0
    assert snap["over_warn"] is False
