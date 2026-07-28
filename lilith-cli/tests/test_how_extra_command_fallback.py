"""Coverage for the ``/how`` fallback over non-BaseCommand slash commands.

Before the fallback landed, ``/how timer``, ``/how quote``, ``/how epoch``
and ``/how random`` all reported "Comando desconocido" — even though
``/timer``, ``/quote``, ``/epoch`` and ``/random`` are fully wired and
dispatched through ``repl.py``. The same blind spot applied to every
other ``run_X_command`` coroutine in ``extra_commands.py`` that lacks a
matching ``BaseCommand`` subclass in ``commands.py``.

These tests exercise:

* the resolver surface for the four historically-orphaned commands
* alias extraction from ``repl.py`` (e.g. ``reverse`` → ``rev``)
* rendering of the fallback panel
* graceful handling of unknown command names
* the no-args usage hint

They are deliberately AST-aware so they survive the 422 KB monolith
without importing it eagerly.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Import the module under test fresh so the module-level cache starts
# empty even if another test already populated it.
how_module = importlib.import_module("lilith_cli.how_command")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_extra_index():
    """Clear the cached fallback index between tests."""
    saved = getattr(how_module, "_EXTRA_INDEX", None)
    setattr(how_module, "_EXTRA_INDEX", None)
    yield
    setattr(how_module, "_EXTRA_INDEX", saved)


def test_how_resolves_orphaned_timer_command(fake_session, capsys):
    """``/how timer`` should now succeed for the historically-orphaned command."""
    _run(how_module.run_how_command(fake_session, "timer"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    assert "/timer" in out
    # Docstring mention of stopwatch / cronómetro is unique enough to assert on.
    assert "cronómetro" in out.lower() or "timer" in out.lower()
    # Se afirma que muestra un origen dentro del paquete, no un archivo
    # concreto: partir el monolito mueve estos comandos de modulo (p. ej. a
    # utility_commands.py) y el test no deberia romperse por eso.
    assert "lilith_cli/" in out


def test_how_resolves_orphaned_quote_command(fake_session, capsys):
    _run(how_module.run_how_command(fake_session, "quote"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    assert "/quote" in out
    # Se afirma que muestra un origen dentro del paquete, no un archivo
    # concreto: partir el monolito mueve estos comandos de modulo (p. ej. a
    # utility_commands.py) y el test no deberia romperse por eso.
    assert "lilith_cli/" in out


def test_how_resolves_orphaned_epoch_command(fake_session, capsys):
    _run(how_module.run_how_command(fake_session, "epoch"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    assert "/epoch" in out
    # Se afirma que muestra un origen dentro del paquete, no un archivo
    # concreto: partir el monolito mueve estos comandos de modulo (p. ej. a
    # utility_commands.py) y el test no deberia romperse por eso.
    assert "lilith_cli/" in out


def test_how_resolves_orphaned_random_command(fake_session, capsys):
    _run(how_module.run_how_command(fake_session, "random"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    assert "/random" in out
    # Se afirma que muestra un origen dentro del paquete, no un archivo
    # concreto: partir el monolito mueve estos comandos de modulo (p. ej. a
    # utility_commands.py) y el test no deberia romperse por eso.
    assert "lilith_cli/" in out


def test_how_fallback_picks_up_repl_alias(fake_session, capsys):
    """Aliases parsed from ``repl.py`` (e.g. ``rev`` for ``reverse``) are surfaced."""
    _run(how_module.run_how_command(fake_session, "rev"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    # The panel prints both the canonical name and any aliases it found.
    assert "/reverse" in out
    assert "/rev" in out


def test_how_fallback_for_known_base_command_still_uses_main_panel(fake_session, capsys):
    """BaseCommand commands keep their original cyan-bordered panel."""
    _run(how_module.run_how_command(fake_session, "help"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    assert "/help" in out
    # BaseCommand panel highlights "Origen" — extra commands do too, but
    # in a different border style and with the "Tipo" footer line.
    assert "Origen" in out
    assert "Tipo" not in out  # fallback panel adds a "Tipo" line; main panel does not


def test_how_fallback_unknown_command_still_errors(fake_session, capsys):
    """An entirely unknown name still produces the friendly error path."""
    _run(how_module.run_how_command(fake_session, "definitely-not-a-command"))
    out = capsys.readouterr().out
    assert "Comando desconocido" in out
    assert "/definitely-not-a-command" in out


def test_how_fallback_accepts_leading_slash(fake_session, capsys):
    """``/how /timer`` behaves like ``/how timer`` (mirror of the BaseCommand path)."""
    _run(how_module.run_how_command(fake_session, "/timer"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    assert "/timer" in out


def test_how_extra_index_is_cached_across_calls(fake_session, capsys):
    """Calling ``/how`` twice should not re-parse the 422 KB monolith."""
    _run(how_module.run_how_command(fake_session, "timer"))
    first_index = getattr(how_module, "_EXTRA_INDEX", None)
    assert first_index is not None
    _run(how_module.run_how_command(fake_session, "quote"))
    assert getattr(how_module, "_EXTRA_INDEX", None) is first_index, (
        "el índice debería estar memoizado en la primera llamada"
    )


def test_how_extra_index_includes_standalone_command_modules():
    """``batch``, ``pipeline`` and ``workflow`` live in their own modules."""
    index = how_module._resolve_extra_command("batch")
    assert index is not None
    assert index["origin"].endswith("batch_command.py")
    assert "Batch" in index["doc"] or "batch" in index["doc"].lower()


def test_how_fallback_panel_renders_docstring_body(fake_session, capsys):
    """The fallback panel exposes the run_X_command docstring verbatim."""
    _run(how_module.run_how_command(fake_session, "env"))
    out = capsys.readouterr().out
    assert "Comando desconocido" not in out
    # /env's docstring starts with "Ejecuta /env [name|info|..." — check
    # that we surface the meaningful first line, not the raw AST dump.
    assert "/env" in out
    assert "Resumen" in out
    assert "Documentación" in out