"""Tests for the machine-readable ``lilith prompt`` output modes.

Covers the ``--quiet`` / ``--output-format`` surface added so another AI
can consume ``lilith prompt`` as a sub-agent:

- ``--quiet --output-format text``: stdout carries ONLY the final
  response — no ANSI, banners, separators, thinking panels, tool cards
  or timings; diagnostics go to stderr; errors produce non-zero exit.
- ``--output-format json``: stdout is a single parseable JSON document
  with schema_version / success / response / usage / duration_ms /
  error; internal reasoning is never included.
- Default (no flags) keeps routing to the Rich ``repl.run_oneshot``
  path (compatibilidad total).

All tests are offline: the agent session is a fake exposing the same
``process_message_stream`` async-generator interface as
``AgentSession``.
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from lilith_cli.config import YggdrasilConfig
from lilith_cli.machine_output import SCHEMA_VERSION, run_oneshot_machine
from lilith_cli.main import app

# ── Fakes ───────────────────────────────────────────────────────────


class _FakeSession:
    """Minimal stand-in for ``AgentSession`` — same stream interface."""

    def __init__(self, events: list[dict] | None = None, exc: Exception | None = None):
        self._events = events or []
        self._exc = exc
        self.received: list[str] = []

    async def process_message_stream(self, text, *, cancel_event=None):
        self.received.append(text)
        if self._exc is not None:
            raise self._exc
        for event in self._events:
            yield event


def _run(coro):
    return asyncio.run(coro)


# ── run_oneshot_machine: text mode ──────────────────────────────────


def test_text_mode_stdout_is_only_the_response():
    session = _FakeSession(
        [
            {"type": "reasoning", "content": "pensando..."},
            {"type": "text", "content": "Hola "},
            {"type": "text", "content": "mundo."},
            {
                "type": "done",
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "hola", output_format="text", out=out, err=err))

    assert code == 0
    assert out.getvalue() == "Hola mundo.\n"
    assert err.getvalue() == ""


def test_text_mode_strips_ansi_from_response():
    session = _FakeSession(
        [
            {"type": "text", "content": "\x1b[31mrojo\x1b[0m limpio"},
            {"type": "done", "usage": {}},
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="text", out=out, err=err))

    assert code == 0
    assert out.getvalue() == "rojo limpio\n"
    assert "\x1b" not in out.getvalue()


def test_text_mode_error_goes_to_stderr_with_nonzero_exit():
    session = _FakeSession(exc=ConnectionError("provider caído"))
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="text", out=out, err=err))

    assert code == 1
    assert out.getvalue() == "", "stdout must stay empty on error"
    assert "ConnectionError" in err.getvalue()
    assert "provider caído" in err.getvalue()


def test_text_mode_cancelled_is_error():
    session = _FakeSession([{"type": "cancelled"}])
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="text", out=out, err=err))

    assert code == 1
    assert out.getvalue() == ""
    assert "cancelled" in err.getvalue()


def test_tool_errors_are_stderr_diagnostics_not_stdout():
    session = _FakeSession(
        [
            {"type": "tool_call", "name": "coding", "arguments": {}},
            {"type": "tool_result", "name": "coding", "content": "", "error": "boom"},
            {"type": "text", "content": "respuesta final"},
            {"type": "done", "usage": {}},
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="text", out=out, err=err))

    assert code == 0, "the model recovered with a final answer"
    assert out.getvalue() == "respuesta final\n"
    assert "tool error" in err.getvalue()
    assert "boom" in err.getvalue()


def test_text_mode_preamble_before_tool_call_is_discarded():
    """El preámbulo interno previo a una tool_call no es parte de la respuesta.

    machine_output limpia ``text_parts`` al llegar un ``tool_call``, así que
    un texto tentativo anterior a las herramientas nunca llega a stdout:
    solo el texto posterior al último ``tool_result``.
    """
    session = _FakeSession(
        [
            {"type": "text", "content": "PREAMBULO_INTERNO"},
            {"type": "tool_call", "name": "coding", "arguments": {}},
            {"type": "tool_result", "name": "coding", "content": "resultado intermedio"},
            {"type": "text", "content": "RESPUESTA_FINAL"},
            {"type": "done", "usage": {}},
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="text", out=out, err=err))

    assert code == 0
    assert out.getvalue() == "RESPUESTA_FINAL\n"
    assert "PREAMBULO_INTERNO" not in out.getvalue()


# ── run_oneshot_machine: json mode ──────────────────────────────────


def test_json_mode_emits_single_parseable_document():
    session = _FakeSession(
        [
            {"type": "reasoning", "content": "razonamiento interno"},
            {"type": "text", "content": "la respuesta"},
            {
                "type": "done",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "hola", output_format="json", out=out, err=err))

    assert code == 0
    raw = out.getvalue()
    # Exactly one JSON document (single line + newline).
    assert raw.count("\n") == 1
    doc = json.loads(raw)
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["success"] is True
    assert doc["response"] == "la respuesta"
    assert doc["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    assert isinstance(doc["duration_ms"], int)
    assert doc["duration_ms"] >= 0
    assert doc["error"] is None
    # Internal reasoning must never leak into the JSON document.
    assert "razonamiento" not in raw
    assert "reasoning" not in doc
    assert err.getvalue() == ""


def test_json_mode_error_document_and_nonzero_exit():
    session = _FakeSession(exc=RuntimeError("kaput"))
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="json", out=out, err=err))

    assert code == 1
    doc = json.loads(out.getvalue())
    assert doc["success"] is False
    assert doc["response"] is None
    assert "kaput" in doc["error"]
    assert doc["usage"] is None
    assert isinstance(doc["duration_ms"], int)


def test_json_mode_cancelled_document():
    session = _FakeSession([{"type": "cancelled"}])
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="json", out=out, err=err))

    assert code == 1
    doc = json.loads(out.getvalue())
    assert doc["success"] is False
    assert doc["error"] == "cancelled"


def test_json_mode_usage_zero_filled_when_missing():
    session = _FakeSession([{"type": "text", "content": "ok"}, {"type": "done"}])
    out, err = io.StringIO(), io.StringIO()
    code = _run(run_oneshot_machine(session, "x", output_format="json", out=out, err=err))

    assert code == 0
    doc = json.loads(out.getvalue())
    assert doc["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_invalid_output_format_rejected():
    session = _FakeSession([])
    with pytest.raises(ValueError, match="output_format"):
        _run(run_oneshot_machine(session, "x", output_format="yaml"))


# ── CLI routing (prompt command) ────────────────────────────────────


def _patch_prompt_cli(monkeypatch, captured, events=None, exc=None):
    """Patch config + AgentSession so ``prompt`` runs fully offline."""

    def _load(config_path=None):
        cfg = YggdrasilConfig(provider="local", model="local-model")
        captured["cfg"] = cfg
        return cfg

    main_mod = __import__("lilith_cli.main", fromlist=["x"])
    monkeypatch.setattr(main_mod, "load_config", _load)

    agent_mod = __import__("lilith_cli.agent", fromlist=["x"])
    monkeypatch.setattr(
        agent_mod, "AgentSession", lambda cfg: _FakeSession(events=events, exc=exc)
    )

    repl_mod = __import__("lilith_cli.repl", fromlist=["x"])

    async def _rich_oneshot(*a, **kw):
        captured["rich_path"] = True

    monkeypatch.setattr(repl_mod, "run_oneshot", _rich_oneshot)


def _invoke(argv):
    """Invoke the cyclopts app; return the SystemExit code (0 default)."""
    try:
        app(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def test_default_prompt_still_uses_rich_path(monkeypatch, capsys):
    """Compatibilidad total: sin flags, ``prompt`` rutea a repl.run_oneshot."""
    captured: dict = {}
    _patch_prompt_cli(monkeypatch, captured)
    code = _invoke(["prompt", "hola"])
    assert code == 0
    assert captured.get("rich_path") is True


def test_quiet_text_flag_routes_to_machine_mode(monkeypatch, capsys):
    captured: dict = {}
    events = [
        {"type": "text", "content": "salida limpia"},
        {"type": "done", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    ]
    _patch_prompt_cli(monkeypatch, captured, events=events)
    code = _invoke(["prompt", "hola", "--quiet"])
    assert code == 0
    out = capsys.readouterr().out
    assert out == "salida limpia\n"
    assert not captured.get("rich_path"), "machine mode must not touch the Rich path"


def test_json_flag_implies_quiet(monkeypatch, capsys):
    captured: dict = {}
    events = [{"type": "text", "content": "resp"}, {"type": "done", "usage": {}}]
    _patch_prompt_cli(monkeypatch, captured, events=events)
    code = _invoke(["prompt", "hola", "--output-format", "json"])
    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["success"] is True
    assert doc["response"] == "resp"


def test_cli_error_produces_nonzero_exit(monkeypatch, capsys):
    captured: dict = {}
    _patch_prompt_cli(monkeypatch, captured, exc=ConnectionError("sin red"))
    code = _invoke(["prompt", "hola", "--quiet"])
    assert code == 1
    streams = capsys.readouterr()
    assert streams.out == ""
    assert "sin red" in streams.err


def test_cli_invalid_output_format_rejected_with_exit_2(monkeypatch, capsys):
    """--output-format inválido: stdout vacío, error plano en stderr, exit 2."""
    captured: dict = {}
    _patch_prompt_cli(monkeypatch, captured)
    code = _invoke(["prompt", "hola", "--output-format", "yaml"])
    assert code == 2
    assert "rich_path" not in captured
    streams = capsys.readouterr()
    assert streams.out == "", "stdout must stay empty on invalid --output-format"
    assert "--output-format" in streams.err
    assert "yaml" in streams.err
    # Plain text, no Rich markup rendered or ANSI escapes on stderr.
    assert "\x1b" not in streams.err
    assert "[error]" not in streams.err


def test_cli_stdout_has_no_ansi_sequences(monkeypatch, capsys):
    captured: dict = {}
    events = [
        {"type": "text", "content": "\x1b[1;32mverde\x1b[0m"},
        {"type": "done", "usage": {}},
    ]
    _patch_prompt_cli(monkeypatch, captured, events=events)
    code = _invoke(["prompt", "hola", "--quiet"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert out == "verde\n"
