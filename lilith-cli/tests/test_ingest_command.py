"""Tests herméticos para ``/ingest`` (``run_ingest_command``).

La red NO entra en la suite: ``MimirIngestUrlTool`` se mockea siempre, y el
camino de reindex también. El handler hace imports perezosos (``from
lilith_tools.crawl_tools import MimirIngestUrlTool`` y ``from
lilith_cli.ops_knowledge import _run_mimir_main, load_mimir_cli``); los
parcheamos a nivel de módulo en ``sys.modules`` antes de invocar para que
los ``import`` internos del handler resuelvan al mock.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _ensure_imports():
    """Fuerza los imports perezosos que el handler usa.

    El handler hace ``from lilith_tools.crawl_tools import MimirIngestUrlTool``
    y ``from lilith_cli.ops_knowledge import _run_mimir_main, load_mimir_cli``
    adentro del ``async def``. Para que ``monkeypatch.setattr(module, ...)``
    pueda interceptarlos, los módulos tienen que estar cargados en
    ``sys.modules`` ANTES del handler — los ``from X import Y`` resuelven
    contra ``sys.modules``, no contra el módulo "en abstracto".
    """
    import importlib

    importlib.import_module("lilith_tools.crawl_tools")
    importlib.import_module("lilith_cli.ops_knowledge")
    yield


def _install_tool_mock(monkeypatch, *, execute):
    """Sustituye ``MimirIngestUrlTool`` en ``lilith_tools.crawl_tools``."""
    tool_module = sys.modules["lilith_tools.crawl_tools"]
    fake_cls = MagicMock()
    fake_instance = MagicMock()
    fake_instance.execute = execute
    fake_cls.return_value = fake_instance
    monkeypatch.setattr(tool_module, "MimirIngestUrlTool", fake_cls)
    return fake_cls, fake_instance


def _install_ops_knowledge_mock(monkeypatch, *, rc=None, mimir=None, raise_load=False):
    """Stub de ``lilith_cli.ops_knowledge`` con los símbolos que el handler importa."""
    ops = sys.modules["lilith_cli.ops_knowledge"]
    fake_run = MagicMock(return_value=rc)
    fake_load = MagicMock(side_effect=RuntimeError("boom") if raise_load else None)
    fake_load.return_value = mimir or MagicMock()
    monkeypatch.setattr(ops, "_run_mimir_main", fake_run)
    monkeypatch.setattr(ops, "load_mimir_cli", fake_load)
    return fake_run, fake_load


def _make_result(success, *, data=None, error=""):
    """Construye un ``ToolResult`` real para que ``.success/.data/.error`` existan."""
    from lilith_tools.base import ToolResult

    return ToolResult(success=success, data=data, error=error)


# ── Sin args / args inválidos ────────────────────────────────────────


def test_no_args_does_not_call_tool(fake_session, monkeypatch):
    """``/ingest`` sin args imprime uso y NO invoca el tool."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock()
    _install_tool_mock(monkeypatch, execute=execute)

    _run(run_ingest_command(fake_session, ""))

    execute.assert_not_called()


def test_non_url_arg_does_not_call_tool(fake_session, monkeypatch, capsys):
    """``/ingest foo`` no es URL → muestra uso, no llama al tool."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock()
    _install_tool_mock(monkeypatch, execute=execute)

    _run(run_ingest_command(fake_session, "foo bar baz"))

    execute.assert_not_called()
    # El mensaje de uso menciona "/ingest".
    captured = capsys.readouterr()
    assert "/ingest" in (captured.out + captured.err)


# ── Llamada correcta al tool ─────────────────────────────────────────


def test_valid_url_calls_tool_with_defaults(fake_session, monkeypatch, capsys):
    """URL válida → el tool se llama con ``url`` y los flags por defecto."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock(return_value=_make_result(
        True,
        data={
            "ruta": "/tmp/docs/externo/foo.md",
            "slug": "foo",
            "caracteres": 1234,
            "url": "https://example.com/foo",
            "nota": "Reindexá para que entre en las búsquedas.",
        },
    ))
    _install_tool_mock(monkeypatch, execute=execute)

    _run(run_ingest_command(fake_session, "https://example.com/foo"))

    execute.assert_called_once_with(
        url="https://example.com/foo",
        nombre="",
        sobrescribir=False,
    )
    # Muestra la ruta y los caracteres.
    out = capsys.readouterr().out
    assert "foo.md" in out
    assert "1234" in out


def test_nombre_and_sobrescribir_flags_passed(fake_session, monkeypatch):
    """``--nombre`` y ``--sobrescribir`` viajan al tool como kwargs."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock(return_value=_make_result(
        True,
        data={"ruta": "/tmp/x.md", "slug": "mi-doc", "caracteres": 5, "url": "u", "nota": ""},
    ))
    _install_tool_mock(monkeypatch, execute=execute)

    _run(
        run_ingest_command(
            fake_session,
            "https://example.com --nombre mi-doc --sobrescribir",
        )
    )

    execute.assert_called_once_with(
        url="https://example.com",
        nombre="mi-doc",
        sobrescribir=True,
    )


# ── Éxito sin reindex: nota mostrada ────────────────────────────────


def test_success_without_reindex_shows_nota(fake_session, monkeypatch, capsys):
    """Sin ``--reindex``, se imprime la nota del tool (recordatorio de reindex)."""
    from lilith_cli.ingest_command import run_ingest_command

    nota = "Reindexá para que entre en las búsquedas: `lilith ask --index <consulta>`"
    execute = MagicMock(return_value=_make_result(
        True,
        data={"ruta": "/tmp/x.md", "slug": "x", "caracteres": 7, "url": "u", "nota": nota},
    ))
    _install_tool_mock(monkeypatch, execute=execute)

    _run(run_ingest_command(fake_session, "https://example.com"))

    out = capsys.readouterr().out
    assert "lilith ask --index" in out


# ── Error del tool ──────────────────────────────────────────────────


def test_tool_error_rendered_via_render_error(fake_session, monkeypatch, capsys):
    """Si ``result.success`` es False, el mensaje del tool se ve como error."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock(return_value=_make_result(
        False, data=None, error="ya existe foo.md. Pasá sobrescribir=true para reemplazarlo.",
    ))
    _install_tool_mock(monkeypatch, execute=execute)

    _run(run_ingest_command(fake_session, "https://example.com"))

    out = capsys.readouterr().out + capsys.readouterr().err
    assert "ya existe foo.md" in out
    # El handler NO debe intentar reindexar en el camino de error.
    execute.assert_called_once()


# ── --reindex: invoca / NO invoca ───────────────────────────────────


def test_reindex_flag_invokes_ops_knowledge(fake_session, monkeypatch, capsys):
    """``--reindex`` ejecuta ``_run_mimir_main`` con ``index`` después del tool."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock(return_value=_make_result(
        True,
        data={"ruta": "/tmp/x.md", "slug": "x", "caracteres": 7, "url": "u", "nota": ""},
    ))
    _install_tool_mock(monkeypatch, execute=execute)
    fake_run, _ = _install_ops_knowledge_mock(monkeypatch, rc=0, mimir=MagicMock())

    _run(run_ingest_command(fake_session, "https://example.com --reindex"))

    # El tool se llamó una vez…
    execute.assert_called_once()
    # …y _run_mimir_main también, con un argv que arranca con "index".
    fake_run.assert_called_once()
    argv = fake_run.call_args.args[1]
    assert argv[0] == "index"
    assert "--root" in argv


def test_no_reindex_flag_does_not_invoke_ops_knowledge(fake_session, monkeypatch):
    """Sin ``--reindex``, NO se importa ni llama nada de ``ops_knowledge``."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock(return_value=_make_result(
        True,
        data={"ruta": "/tmp/x.md", "slug": "x", "caracteres": 7, "url": "u", "nota": ""},
    ))
    _install_tool_mock(monkeypatch, execute=execute)
    fake_run, fake_load = _install_ops_knowledge_mock(monkeypatch, rc=0)

    _run(run_ingest_command(fake_session, "https://example.com"))

    fake_run.assert_not_called()
    fake_load.assert_not_called()


def test_reindex_failure_still_says_doc_was_saved(fake_session, monkeypatch, capsys):
    """Si el reindex falla, el handler avisa que el doc YA quedó en disco."""
    from lilith_cli.ingest_command import run_ingest_command

    execute = MagicMock(return_value=_make_result(
        True,
        data={"ruta": "/tmp/x.md", "slug": "x", "caracteres": 7, "url": "u", "nota": ""},
    ))
    _install_tool_mock(monkeypatch, execute=execute)
    _install_ops_knowledge_mock(monkeypatch, rc=2, mimir=MagicMock())

    _run(run_ingest_command(fake_session, "https://example.com --reindex"))

    out = capsys.readouterr().out + capsys.readouterr().err
    assert "guardado" in out.lower() or "ya qued" in out.lower()


# ── No-op tests no pasan sin el comando ─────────────────────────────


def test_command_module_is_wired_in_repl() -> None:
    """El handler está realmente agregado al REPL (no solo existe el archivo)."""
    import lilith_cli.repl as repl_module

    assert hasattr(repl_module, "run_ingest_command"), (
        "run_ingest_command no está importado en repl.py"
    )
    assert "/ingest" in repl_module._SLASH_COMMANDS, (
        "/ingest no figura en _SLASH_COMMANDS"
    )
    src = open(repl_module.__file__, encoding="utf-8").read()
    assert 'cmd_name == "ingest"' in src, (
        "el dispatch de /ingest no está cableado en repl.py"
    )