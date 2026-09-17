"""El comando /transcript: leer una sesion guardada.

Las conversaciones se guardaban desde siempre y NADA las leia: 422 ficheros en
~/.yggdrasil/conversations y ningun comando que los abriera para revisarlos.
Hacia falta justo el dia en que se apagaron los paneles en vivo: quitarle la
ventana al proceso solo es razonable si queda como mirarlo despues.
"""

from __future__ import annotations

import json

import pytest

from lilith_cli.transcript_commands import _formatear_hora, run_transcript_command


def _sesion(ruta, nombre, mensajes, marca="20260915_190741_598108"):
    f = ruta / f"{nombre}.json"
    f.write_text(json.dumps({
        "timestamp": marca, "model": "router", "provider": "fabric",
        "messages": mensajes,
    }, ensure_ascii=False), encoding="utf-8")
    return f


@pytest.fixture
def conversaciones(tmp_path, monkeypatch):
    f1 = _sesion(tmp_path, "conv_20260915_190741_598108_aaa", [
        {"role": "user", "content": "como activo tu modo admin"},
        {"role": "assistant", "content": "No tengo un modo admin.",
         "tool_calls": [{"function": {"name": "file_read", "arguments": '{"path": "x"}'}}]},
        {"role": "tool", "content": "contenido del fichero"},
    ])
    entradas = [{"file": f1, "name": f1.stem, "timestamp": "20260915_190741_598108",
                 "model": "router", "provider": "fabric", "message_count": 3,
                 "preview": "como activo tu modo admin"}]
    import lilith_cli.repl as repl
    monkeypatch.setattr(repl, "_list_saved_conversations", lambda: entradas)
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    return entradas


@pytest.mark.asyncio
async def test_sin_sesiones_no_revienta(tmp_path, monkeypatch, capsys):
    import lilith_cli.repl as repl
    monkeypatch.setattr(repl, "_list_saved_conversations", lambda: [])
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path)
    await run_transcript_command(None, "")
    assert "No hay sesiones guardadas" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_lista_las_sesiones(conversaciones, capsys):
    await run_transcript_command(None, "")
    salida = capsys.readouterr().out
    assert "Turnos" in salida
    assert "modo admin" in salida


@pytest.mark.asyncio
async def test_abre_por_indice_y_oculta_herramientas(conversaciones, capsys):
    await run_transcript_command(None, "1")
    salida = capsys.readouterr().out
    assert "como activo tu modo admin" in salida
    assert "No tengo un modo admin" in salida
    # Por omision el resultado de la herramienta no se muestra: es el proceso.
    assert "contenido del fichero" not in salida
    assert "Resultados de herramienta ocultos" in salida


@pytest.mark.asyncio
async def test_con_tools_si_muestra_las_herramientas(conversaciones, capsys):
    await run_transcript_command(None, "1 tools")
    salida = capsys.readouterr().out
    assert "contenido del fichero" in salida
    assert "file_read" in salida


@pytest.mark.asyncio
async def test_abre_por_nombre(conversaciones, capsys):
    await run_transcript_command(None, "190741")
    assert "modo admin" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_indice_fuera_de_rango_avisa(conversaciones, capsys):
    await run_transcript_command(None, "99")
    assert "Fuera de rango" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_nombre_inexistente_avisa(conversaciones, capsys):
    await run_transcript_command(None, "no-existe-esto")
    assert "No encuentro" in capsys.readouterr().out


def test_formatear_hora_con_las_dos_formas():
    """Los ficheros guardan 20260915_194917, no ISO. Eso dejaba la columna en crudo."""
    assert _formatear_hora("20260915_194917_691086") == "15 Sep 19:49"
    assert _formatear_hora("2026-09-15T19:49:17") == "15 Sep 19:49"
    assert _formatear_hora("") == "sin fecha"
    assert _formatear_hora(None) == "sin fecha"
    assert _formatear_hora("basura-no-fecha") == "basura-no-fecha"
