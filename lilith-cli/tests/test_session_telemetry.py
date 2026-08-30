"""Tests for the session telemetry boundary and compatibility seam."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.providers import estimate_cost
from lilith_cli.session_telemetry import SessionTelemetry, get_session_telemetry


def test_track_usage_accumulates_totals_and_model_cost() -> None:
    telemetry = SessionTelemetry()

    telemetry.track_usage(
        {"prompt_tokens": 1000, "completion_tokens": 250},
        "gpt-4o",
    )
    telemetry.track_usage(
        {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
        "gpt-4o",
    )

    assert telemetry.total_usage == {
        "prompt_tokens": 1500,
        "completion_tokens": 350,
        "total_tokens": 1850,
    }
    model_usage = telemetry.per_model_usage["gpt-4o"]
    assert {
        key: model_usage[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    } == {
        "prompt_tokens": 1500,
        "completion_tokens": 350,
        "total_tokens": 1850,
    }
    assert model_usage["cost"] == pytest.approx(estimate_cost("gpt-4o", 1500, 350))


def test_public_views_do_not_expose_mutable_state() -> None:
    telemetry = SessionTelemetry()
    telemetry.record_tool_call(
        {"name": "file_read", "arguments": {"path": "README.md"}}
    )

    total = telemetry.total_usage
    calls = telemetry.tool_calls
    total["total_tokens"] = 999
    calls[0]["arguments"]["path"] = "changed.txt"

    assert telemetry.total_usage["total_tokens"] == 0
    assert telemetry.tool_calls[0]["arguments"]["path"] == "README.md"


def test_merge_usage_combines_persisted_totals() -> None:
    telemetry = SessionTelemetry()
    telemetry.track_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "gpt-4o",
    )

    telemetry.merge_usage(
        {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
        {
            "gpt-4o": {
                "prompt_tokens": 20,
                "completion_tokens": 7,
                "total_tokens": 27,
                "cost": 0.5,
            }
        },
    )

    assert telemetry.total_usage["total_tokens"] == 42
    assert telemetry.per_model_usage["gpt-4o"]["total_tokens"] == 42
    assert telemetry.per_model_usage["gpt-4o"]["cost"] == pytest.approx(
        estimate_cost("gpt-4o", 10, 5) + 0.5
    )


def test_snapshot_restore_round_trip() -> None:
    source = SessionTelemetry()
    source.merge_usage({"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})
    source.record_tool_call({"name": "file_read", "duration": 0.25})
    source.record_command({"name": "metrics", "args": "tools"})
    source.record_file_edit({"path": "a.py", "tool": "file_edit"})

    restored = SessionTelemetry()
    restored.restore(source.snapshot())

    assert restored.snapshot() == source.snapshot()


def test_agent_legacy_fields_delegate_to_telemetry_when_built_with_new() -> None:
    session = AgentSession.__new__(AgentSession)

    session._total_usage = {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
    }
    session._tool_call_history = [{"name": "shell"}]

    assert session.total_usage["total_tokens"] == 3
    assert session.telemetry.tool_calls == [{"name": "shell"}]

    del session._tool_call_history
    assert not hasattr(session, "_tool_call_history")
    assert session.telemetry.status()["tools"] is False

    session._tool_call_history = [{"name": "file_read"}]
    assert session.telemetry.status()["tools"] is True


def test_legacy_session_adapter_preserves_existing_embedders() -> None:
    session = SimpleNamespace(
        _total_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        _per_model_usage={},
        _tool_call_history=[],
        _command_history=[],
        _file_edit_history=[],
    )
    telemetry = get_session_telemetry(session)

    telemetry.record_command({"name": "status", "args": ""})
    telemetry.merge_usage({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})

    assert session._command_history == [{"name": "status", "args": ""}]
    assert telemetry.total_usage["total_tokens"] == 7
    assert telemetry.status() == {"tools": True, "commands": True, "files": True}


def test_get_session_telemetry_accepts_public_runtime_implementations() -> None:
    telemetry = SessionTelemetry()
    session = SimpleNamespace(telemetry=telemetry)

    assert get_session_telemetry(session) is telemetry
