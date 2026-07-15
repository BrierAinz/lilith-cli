"""Tests for the new ``--yes`` and ``--max-iterations`` flags on the
``yggdrasil prompt`` command.

These flags mutate the loaded ``YggdrasilConfig`` before the agent session
is created, so we assert them by spying on ``load_config`` (which the
``prompt`` command calls inside the cyclopts ``@app.command`` handler)
and capturing the ``AgentSession`` kwargs.

The actual LLM run is suppressed by replacing ``AgentSession`` with a
fake that records the config it received.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.config import YggdrasilConfig
from lilith_cli.main import app


@pytest.fixture
def captured():
    return {}


def _make_session_factory(captured):
    class _FakeSession:
        def __init__(self, cfg):
            captured["cw"] = cfg.confirm_write
            captured["mi"] = cfg.max_iterations
            captured["provider"] = cfg.provider

    return _FakeSession


def _patch_prompt(monkeypatch, captured):
    """Patch everything ``prompt()`` needs at call time.

    ``run_oneshot`` is imported lazily inside ``prompt()`` via
    ``from .repl import run_oneshot`` — that local rebinding *doesn't*
    touch the ``main`` module namespace, so monkeypatching
    ``lilith_cli.main.run_oneshot`` is a no-op (the import would
    AttributeError before the local rebind). Instead we set the
    sentinel attributes on the namespaces we own so the local imports
    succeed and our stubs take effect.
    """

    def _load(config_path=None):
        cfg = YggdrasilConfig(provider="local", model="local-model")
        cfg.confirm_write = True
        cfg.max_iterations = 10
        captured["cfg"] = cfg
        return cfg

    main_mod = __import__("lilith_cli.main", fromlist=["x"])
    monkeypatch.setattr(main_mod, "load_config", _load)
    # Pre-create the attribute so the lazy ``from .repl import run_oneshot``
    # sees our stub (it imports onto the local function namespace, not the module,
    # so we need a different strategy: replace the symbol in ``lilith_cli.repl``).
    repl_mod = __import__("lilith_cli.repl", fromlist=["x"])
    async def _noop_oneshot(*a, **kw):
        return None
    monkeypatch.setattr(repl_mod, "run_oneshot", _noop_oneshot)
    # Same trick for AgentSession — imported lazily.
    agent_mod = __import__("lilith_cli.agent", fromlist=["x"])
    monkeypatch.setattr(agent_mod, "AgentSession", _make_session_factory(captured))


def _invoke(argv):
    """Invoke ``app`` swallowing cyclopts' benign post-handler ``SystemExit(0)``.

    The cyclopts dispatcher runs ``sys.exit(returncode)`` after every command,
    which makes it hostile to direct in-process unit testing. We re-raise
    non-zero exits so the validation tests still assert ``SystemExit(2)``.
    """
    try:
        app(argv)
    except SystemExit as exc:
        if exc.code in (None, 0):
            return
        raise


def test_yes_flag_disables_confirm_write(monkeypatch, captured):
    _patch_prompt(monkeypatch, captured)
    _invoke(["prompt", "hola", "--yes"])
    assert captured["cw"] is False, (
        f"--yes must turn off confirm_write on the session's config; got {captured['cw']!r}"
    )


def test_max_iterations_flag_overrides_config(monkeypatch, captured):
    _patch_prompt(monkeypatch, captured)
    _invoke(["prompt", "hola", "--max-iterations", "5"])
    assert captured["mi"] == 5, (
        f"--max-iterations must set cfg.max_iterations=5; got {captured['mi']!r}"
    )


def test_yes_and_max_iterations_combine(monkeypatch, captured):
    _patch_prompt(monkeypatch, captured)
    _invoke(["prompt", "hola", "--yes", "--max-iterations", "20"])
    assert captured["cw"] is False
    assert captured["mi"] == 20


def test_max_iterations_below_one_rejected(monkeypatch, captured):
    _patch_prompt(monkeypatch, captured)
    with pytest.raises(SystemExit) as exc_info:
        app(["prompt", "hola", "--max-iterations", "0"])
    assert exc_info.value.code == 2
    assert "cw" not in captured, "should not have built session with invalid max_iterations"
