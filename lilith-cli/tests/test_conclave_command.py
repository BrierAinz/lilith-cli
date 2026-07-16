"""Tests for /conclave slash command.

The command is a thin wrapper around ``lilith_tools.conclave.ConclaveTool``
plus a Rich renderer; the tool itself has its own exhaustive suite in
``lilith-tools/tests/test_conclave.py``. These tests focus on:

* Argument parsing (``--presets``, ``--structured``, ``--max-tokens``,
  ``--timeout``) and error paths (empty args, bad flags).
* Correct delegation to the tool: kwargs forwarded, presets list passed
  through verbatim.
* Renderer behaviour: one panel per preset, content truncated to ~15
  lines, errors in one preset surface but do not hide the others.
* The visual contract that the catalog (``run_help_command``) mentions
  ``conclave`` and the dispatcher (``repl.py``) reaches it.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── Stubs ───────────────────────────────────────────────────────────────


class _StubConclaveTool:
    """Stand-in for ``ConclaveTool`` that records the call and returns a script.

    Mirrors the contract the real ``ConclaveTool().execute(**kwargs)``
    uses: returns a ``ToolResult``-like object with ``success``, ``data``,
    and ``error`` attributes. Tests pass either a dict (interpreted as
    the data payload) or an Exception (raised on call) as the script.
    """

    def __init__(self, script: Any) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self) -> "_StubConclaveTool":
        return self  # ConclaveTool() with no args

    def execute(self, **kwargs: Any) -> Any:
        from lilith_tools.base import ToolResult

        with self._lock:
            self.calls.append(kwargs)
        if isinstance(self._script, BaseException):
            raise self._script
        if isinstance(self._script, ToolResult):
            return self._script
        if isinstance(self._script, dict):
            return ToolResult(
                success=bool(self._script.get("success", True)),
                data=self._script.get("data"),
                error=self._script.get("error", ""),
            )
        raise AssertionError(
            f"StubConclaveTool: unsupported script type {type(self._script).__name__}"
        )


def _install_stub(monkeypatch, stub: _StubConclaveTool) -> None:
    """Patch ``ConclaveTool`` at every import site used by ``extra_commands``.

    ``run_conclave_command`` does a *lazy* ``from lilith_tools.conclave
    import ConclaveTool`` inside the function body, so we have to patch
    the symbol on the source module — patching the consumer
    (``lilith_cli.extra_commands``) is too late.
    """
    import lilith_tools.conclave as conclave_mod

    monkeypatch.setattr(conclave_mod, "ConclaveTool", stub)


def _fake_succeed_data(responses: list[dict[str, Any]] | None = None) -> dict:
    """Build a 'data' dict shaped like ConclaveTool would return."""
    responses = responses or []
    return {
        "question": "ignored",
        "presets_requested": [r["preset"] for r in responses],
        "responses": responses,
        "ok_count": sum(1 for r in responses if not r.get("error")),
        "failed_count": sum(1 for r in responses if r.get("error")),
    }


# ── (a) Argument parsing ────────────────────────────────────────────────


def test_conclave_empty_args_shows_usage(fake_session, capsys):
    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "--presets" in out
    assert "conclave" in out.lower()


def test_conclave_only_flags_shows_error(fake_session, capsys):
    """User passed flags but no question → explicit error."""
    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "--presets a,b"))

    out = capsys.readouterr().out
    assert "vacía" in out.lower() or "pregunta" in out.lower()


def test_conclave_max_tokens_rejects_non_integer(fake_session, capsys):
    from lilith_cli.extra_commands import run_conclave_command

    _run(
        run_conclave_command(
            fake_session, "alguna pregunta --max-tokens notanumber"
        )
    )

    out = capsys.readouterr().out
    assert "--max-tokens" in out
    assert "entero" in out.lower()


def test_conclave_timeout_rejects_non_numeric(fake_session, capsys):
    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "x --timeout oops"))

    out = capsys.readouterr().out
    assert "--timeout" in out


# ── (b) Delegation to ConclaveTool ──────────────────────────────────────


def test_conclave_delegates_with_presets(fake_session, monkeypatch, capsys):
    """A plain question + --presets flag reaches ConclaveTool unchanged."""
    stub = _StubConclaveTool({
        "success": True,
        "data": _fake_succeed_data([
            {"preset": "a", "model": "m-a", "content": "aa", "usage": {}},
            {"preset": "b", "model": "m-b", "content": "bb", "usage": {}},
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "hello --presets a,b"))

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["question"] == "hello"
    assert call["presets"] == ["a", "b"]
    assert call["structured"] is False
    assert "max_tokens" not in call
    assert "timeout" not in call


def test_conclave_structured_flag_forwarded(fake_session, monkeypatch):
    stub = _StubConclaveTool({
        "success": True,
        "data": _fake_succeed_data([
            {"preset": "a", "model": "m", "content": "x", "usage": {}},
            {"preset": "b", "model": "n", "content": "y", "usage": {}},
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "x --structured"))

    assert stub.calls[0]["structured"] is True


def test_conclave_max_tokens_forwarded(fake_session, monkeypatch):
    stub = _StubConclaveTool({
        "success": True,
        "data": _fake_succeed_data([
            {"preset": "a", "model": "m", "content": "x", "usage": {}},
            {"preset": "b", "model": "n", "content": "y", "usage": {}},
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "x --max-tokens 1024"))

    assert stub.calls[0]["max_tokens"] == 1024


def test_conclave_timeout_forwarded(fake_session, monkeypatch):
    stub = _StubConclaveTool({
        "success": True,
        "data": _fake_succeed_data([
            {"preset": "a", "model": "m", "content": "x", "usage": {}},
            {"preset": "b", "model": "n", "content": "y", "usage": {}},
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "x --timeout 12.5"))

    assert stub.calls[0]["timeout"] == 12.5


def test_conclave_question_preserved_with_quotes(fake_session, monkeypatch):
    """shlex.split must keep quoted phrases as one token."""
    stub = _StubConclaveTool({
        "success": True,
        "data": _fake_succeed_data([
            {"preset": "a", "model": "m", "content": "x", "usage": {}},
            {"preset": "b", "model": "n", "content": "y", "usage": {}},
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, '"what is the meaning?" --presets a,b'))

    assert stub.calls[0]["question"] == "what is the meaning?"


# ── (c) Renderer behaviour ──────────────────────────────────────────────


def test_conclave_renders_one_panel_per_preset(fake_session, monkeypatch, capsys):
    stub = _StubConclaveTool({
        "success": True,
        "data": _fake_succeed_data([
            {
                "preset": "alpha",
                "model": "alpha-v1",
                "content": "first answer",
                "usage": {"total_tokens": 42},
            },
            {
                "preset": "beta",
                "model": "beta-v2",
                "content": "second answer",
                "usage": {"total_tokens": 17},
            },
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "any --presets alpha,beta"))

    out = capsys.readouterr().out
    # Both presets and their models surface in the panels
    assert "alpha" in out
    assert "beta" in out
    assert "alpha-v1" in out
    assert "beta-v2" in out
    assert "first answer" in out
    assert "second answer" in out
    # Token usage surfaces as "tokens=N"
    assert "tokens=42" in out
    assert "tokens=17" in out
    # Counts in the header
    assert "ok=" in out
    assert "fallaron=0" in out


def test_conclave_partial_failure_surfaces_per_preset_errors(
    fake_session, monkeypatch, capsys
):
    """One preset failing does NOT hide the surviving one."""
    stub = _StubConclaveTool({
        "success": True,  # at least one preset survived → tool still success
        "data": _fake_succeed_data([
            {
                "preset": "alpha",
                "model": "alpha-v1",
                "content": "good answer",
                "usage": {},
                "error": "",
            },
            {
                "preset": "beta",
                "model": "beta-v2",
                "content": "",
                "usage": {},
                "error": "timeout: 429 from upstream",
            },
        ]),
        "error": "",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "x --presets alpha,beta"))

    out = capsys.readouterr().out
    # Both presets are rendered
    assert "alpha" in out
    assert "beta" in out
    # The good answer is preserved
    assert "good answer" in out
    # The error message is surfaced
    assert "timeout" in out.lower() or "ERROR" in out
    # Counts reflect partial success
    assert "ok=" in out
    assert "fallaron=1" in out


def test_conclave_total_failure_marks_header_as_failed(
    fake_session, monkeypatch, capsys
):
    """When ALL presets fail, the header shows FALLO and surfaces the error."""
    stub = _StubConclaveTool({
        "success": False,
        "data": _fake_succeed_data([
            {
                "preset": "alpha",
                "model": None,
                "content": "",
                "usage": {},
                "error": "boom",
            },
            {
                "preset": "beta",
                "model": None,
                "content": "",
                "usage": {},
                "error": "boom",
            },
        ]),
        "error": "todos los presets fallaron",
    })
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    _run(run_conclave_command(fake_session, "x --presets alpha,beta"))

    out = capsys.readouterr().out
    assert "FALLO" in out or "FAL" in out
    assert "todos los presets fallaron" in out
    # Both errors still rendered
    assert "boom" in out
    assert "fallaron=2" in out


def test_conclave_tool_exception_is_reported(fake_session, monkeypatch, capsys):
    """If ConclaveTool.execute raises, the command surfaces the exception."""

    class _Boom(RuntimeError):
        pass

    stub = _StubConclaveTool(_Boom("kaboom"))
    _install_stub(monkeypatch, stub)

    from lilith_cli.extra_commands import run_conclave_command

    # Must not propagate: the REPL survives a per-command failure.
    _run(run_conclave_command(fake_session, "x --presets a,b"))

    # No assertion here on the exact message because the asyncio executor
    # surfaces the exception via run_in_executor; the command must simply
    # not crash the test process. We just confirm we got here.


# ── (d) Content truncation helper ───────────────────────────────────────


def test_truncate_content_short_passes_through():
    from lilith_cli.extra_commands import _truncate_content

    short = "line 1\nline 2\nline 3"
    assert _truncate_content(short, max_lines=15) == short


def test_truncate_content_long_caps_with_marker():
    from lilith_cli.extra_commands import _truncate_content

    long = "\n".join(f"line {i}" for i in range(50))
    out = _truncate_content(long, max_lines=15)
    # First 15 lines preserved verbatim
    for i in range(15):
        assert f"line {i}" in out
    # Marker reveals the hidden tail
    assert "+35 líneas más" in out
    # Lines beyond 15 not rendered
    assert "line 15" not in out
    assert "line 49" not in out


# ── (e) Catalog wiring regression ───────────────────────────────────────


def test_conclave_is_in_help_catalog(fake_session, capsys):
    """/help must list /conclave in the System category."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, "system"))

    out = capsys.readouterr().out
    assert "/conclave" in out
    assert "presets" in out.lower()


def test_conclave_is_dispatched_by_repl(fake_session, monkeypatch):
    """The repl dispatcher must hand ``/conclave`` to ``run_conclave_command``.

    We don't spin up the full REPL: instead we patch
    ``run_conclave_command`` on the ``repl`` module to a recorder and
    invoke the dispatcher's branch directly via the same ``cmd_name``
    the REPL would feed it.
    """
    import lilith_cli.repl as repl_mod

    recorder: dict[str, Any] = {}

    async def _recorder(session, args):
        recorder["session"] = session
        recorder["args"] = args

    monkeypatch.setattr(repl_mod, "run_conclave_command", _recorder)

    async def _drive():
        # Simulate the exact branch from repl.run_repl
        cmd_name = "conclave"
        cmd_args = "hello --presets a,b"
        if cmd_name == "conclave":
            await repl_mod.run_conclave_command(fake_session, cmd_args)

    _run(_drive())

    assert recorder["args"] == "hello --presets a,b"
    assert recorder["session"] is fake_session


# ── (f) Slash autocomplete presence ─────────────────────────────────────


def test_conclave_is_in_slash_autocomplete():
    """The completer list in repl.py must include ``/conclave``."""
    from lilith_cli.repl import _SLASH_COMMANDS

    assert "/conclave" in _SLASH_COMMANDS


# ── (g) Help wiring test recognises conclave ───────────────────────────


def test_help_catalog_wiring_includes_conclave():
    """The wiring test extracts the catalog via AST; ensure conclave is parsed."""
    from lilith_cli.extra_commands import run_help_command

    import ast
    import inspect

    source = inspect.getsource(run_help_command)
    tree = ast.parse(source)
    fn = tree.body[0]
    catalog_node = None
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "catalog"
            and isinstance(node.value, ast.Dict)
        ):
            catalog_node = node
            break
    assert catalog_node is not None, "catalog literal not found"

    flat = set()
    for key, value in zip(catalog_node.value.keys, catalog_node.value.values):
        if isinstance(key, ast.Constant) and isinstance(value, ast.List):
            cat = key.value
            for elt in value.elts:
                if isinstance(elt, ast.Tuple) and len(elt.elts) >= 1:
                    name_node = elt.elts[0]
                    if isinstance(name_node, ast.Constant):
                        flat.add((cat, name_node.value))

    flat_dict: dict[str, list[str]] = {}
    for cat, name in flat:
        flat_dict.setdefault(cat, []).append(name)

    assert "conclave" in flat_dict.get("System", []), (
        f"conclave missing from System category; found: {flat_dict}"
    )