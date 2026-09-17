"""Safety and behavior tests for the interactive operator-choice tool."""

from __future__ import annotations

import io
import sys

import lilith_tools.ask_operator as ask_operator
from lilith_tools.ask_operator import AskOperatorTool
from lilith_tools.delegate import _AGENTIC_TOOL_NAMES


OPTIONS = [
    {"label": "Usar gratis", "detail": "Se mantienen los límites actuales."},
    {"label": "Pagar cuota", "detail": "Se habilita capacidad adicional."},
]


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_non_tty_fails_without_reading(monkeypatch):
    class _NoRead(io.StringIO):
        def isatty(self) -> bool:
            return False

        def readline(self):
            raise AssertionError("no debe leer stdin sin terminal")

    monkeypatch.setattr(sys, "stdin", _NoRead())
    monkeypatch.setattr(sys, "stdout", _NoRead())
    result = AskOperatorTool().execute(question="¿Qué hacemos?", options=OPTIONS)
    assert not result.success
    assert "terminal" in result.error.lower()
    assert "prosa" in result.error.lower()


def test_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr(sys, "stdout", _TTY())
    monkeypatch.setattr(ask_operator, "_readline_with_timeout", lambda timeout, stream: None)
    result = AskOperatorTool().execute(question="¿Qué hacemos?", options=OPTIONS, timeout=0.01)
    assert not result.success
    assert "agotado" in result.error.lower()


def test_validation_is_clear():
    tool = AskOperatorTool()
    assert "2 y 5" in tool.execute(question="¿Qué hacemos?", options=[]).error
    assert "label" in tool.execute(question="¿Qué hacemos?", options=[{"label": "", "detail": "x"}, OPTIONS[1]]).error
    assert "detail" in tool.execute(question="¿Qué hacemos?", options=[{"label": "Uno", "detail": ""}, OPTIONS[1]]).error


def test_single_selection(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TTY("2\n"))
    monkeypatch.setattr(sys, "stdout", _TTY())
    result = AskOperatorTool().execute(question="¿Qué hacemos?", options=OPTIONS)
    assert result.success
    assert result.data["indices"] == [2]
    assert result.data["free_text"] is None


def test_multi_selection_and_other(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TTY("1,3\nexplicar el impacto\n"))
    monkeypatch.setattr(sys, "stdout", _TTY())
    result = AskOperatorTool().execute(question="¿Qué hacemos?", options=OPTIONS, multi=True)
    assert result.success
    assert result.data["indices"] == [1, 3]
    assert result.data["free_text"] == "explicar el impacto"


def test_subagents_cannot_call_operator_prompt():
    assert "ask_operator" not in _AGENTIC_TOOL_NAMES
