"""El recorrido podado y, sobre todo, que un recorrido truncado lo DIGA.

Contexto que justifica estos tests (2026-09-15): `search_across_files` y
`grep_files` recorrian con `Path.rglob` sin excluir nada. Lilith busco
"telegram", la busqueda caduco, devolvio cero y ella concluyo que no habia un
bot de Telegram implementado. El bot existia y estaba corriendo.

El test que de verdad importa aqui es `test_un_resultado_truncado_lo_declara`:
mientras eso se cumpla, un cero nunca volvera a poder confundirse con una
ausencia.
"""

from __future__ import annotations

import json

from lilith_tools.file_walk import (
    EXCLUDED_DIRS,
    WalkReport,
    looks_binary,
    walk_text_files,
)


def _arbol(raiz):
    """Un arbol con lo que hay que saltar y lo que hay que encontrar."""
    (raiz / "bueno.py").write_text("hola telegram\n", encoding="utf-8")
    (raiz / "sub").mkdir()
    (raiz / "sub" / "otro.py").write_text("otra cosa\n", encoding="utf-8")
    for basura in ("node_modules", ".venv", "__pycache__"):
        d = raiz / basura
        d.mkdir()
        (d / "ruido.py").write_text("telegram por todas partes\n", encoding="utf-8")
    (raiz / "imagen.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (raiz / "datos.bin").write_bytes(b"cabecera\x00con nulo")
    return raiz


def test_poda_los_directorios_generados(tmp_path):
    raiz = _arbol(tmp_path)
    rep = WalkReport()
    encontrados = {p.name for p in walk_text_files(raiz, "*.py", report=rep)}
    assert encontrados == {"bueno.py", "otro.py"}
    assert rep.pruned_dirs >= 3
    assert "ruido.py" not in encontrados


def test_apply_exclusions_false_vuelve_a_entrar(tmp_path):
    """A veces se busca justo dentro de dist o de site-packages."""
    raiz = _arbol(tmp_path)
    rep = WalkReport()
    encontrados = [p.name for p in walk_text_files(raiz, "*.py", report=rep,
                                                   apply_exclusions=False)]
    assert encontrados.count("ruido.py") == 3
    assert rep.pruned_dirs == 0


def test_salta_binarios_por_extension_y_por_byte_nulo(tmp_path):
    raiz = _arbol(tmp_path)
    rep = WalkReport()
    nombres = {p.name for p in walk_text_files(raiz, "*", report=rep)}
    assert "imagen.png" not in nombres
    assert "datos.bin" not in nombres
    assert rep.skipped_binary >= 2
    assert looks_binary(raiz / "datos.bin") is True
    assert looks_binary(raiz / "bueno.py") is False


def test_un_resultado_truncado_lo_declara(tmp_path):
    """El test que existe por la conclusion falsa. No lo debilites.

    Un recorrido que se corta y no lo dice convierte "no llegue a mirar" en
    "no existe", y quien lo lea no tiene forma de saberlo.
    """
    raiz = _arbol(tmp_path)
    rep = WalkReport()
    list(walk_text_files(raiz, "*.py", report=rep, max_files=1))
    assert rep.truncated is True
    assert rep.reason
    aviso = rep.warning()
    assert aviso and "INCOMPLETA" in aviso
    assert "NO prueba" in aviso
    d = rep.as_dict()
    assert d["truncated"] is True
    assert "files_scanned" in d and "warning" in d


def test_un_recorrido_completo_no_avisa_de_nada(tmp_path):
    raiz = _arbol(tmp_path)
    rep = WalkReport()
    list(walk_text_files(raiz, "*.py", report=rep))
    assert rep.truncated is False
    assert rep.warning() is None
    assert "warning" not in rep.as_dict()


def test_las_exclusiones_incluyen_los_sospechosos_habituales():
    for d in ("node_modules", ".venv", ".git", "__pycache__", "site-packages", "dist"):
        assert d in EXCLUDED_DIRS


def test_render_acepta_la_forma_vieja_y_la_nueva():
    """La regresion que Ratatoskr avisó y que habria pasado en SILENCIO.

    grep_files devolvia una lista y ahora devuelve un dict. Los consumidores de
    render.py comprobaban isinstance(data, list) y startswith("["), asi que con
    un dict dejarian de resumir y de pintar la tabla sin lanzar nada.
    """
    from lilith_cli.render import _grep_matches, render_tool_result, summarize_tool_result

    viejo = [{"file": "a.py", "line_number": 3, "line_text": "x"}]
    nuevo = {"matches": viejo, "count": 1, "files_scanned": 9, "truncated": True,
             "warning": "BUSQUEDA INCOMPLETA: se alcanzo el tope."}

    assert _grep_matches(viejo) == (viejo, None)
    assert _grep_matches(nuevo)[0] == viejo
    assert _grep_matches(nuevo)[1]
    assert _grep_matches("basura") == ([], None)

    assert "1 coincidencia" in summarize_tool_result("grep_files", json.dumps(viejo), viejo)
    resumen_nuevo = summarize_tool_result("grep_files", json.dumps(nuevo), nuevo)
    assert "INCOMPLETA" in resumen_nuevo

    for forma in (viejo, nuevo):
        tabla = render_tool_result("grep_files", json.dumps(forma, ensure_ascii=False))
        assert tabla.row_count == 1
    assert render_tool_result("grep_files", json.dumps(nuevo, ensure_ascii=False)).caption


# ── walk_all_files: el nucleo, para quien necesita ver los binarios ──────────


def test_walk_all_files_poda_pero_ve_los_binarios(tmp_path):
    """La distincion que justifica que existan dos funciones.

    Un vigilante de ficheros tiene que notar que un .png o un .zip cambiaron,
    asi que aplicarle el filtro de texto de las busquedas seria un error
    silencioso: dejaria de avisar de cambios reales.
    """
    from lilith_tools.file_walk import walk_all_files

    raiz = _arbol(tmp_path)
    nombres = {p.name for p in walk_all_files(raiz)}
    assert "imagen.png" in nombres
    assert "datos.bin" in nombres
    assert "bueno.py" in nombres
    assert "ruido.py" not in nombres


def test_walk_all_files_acepta_otra_lista_de_exclusiones(tmp_path):
    from lilith_tools.file_walk import walk_all_files

    raiz = _arbol(tmp_path)
    solo_git = frozenset({".git"})
    nombres = [p.name for p in walk_all_files(raiz, "*.py", exclude_dirs=solo_git)]
    assert nombres.count("ruido.py") == 3, "con otra lista, node_modules deja de excluirse"


def test_el_snapshot_del_vigilante_no_baja_a_node_modules(tmp_path):
    """_build_snapshot se reconstruye CADA SEGUNDO desde _run().

    Con rglob eso era stat() sobre todo el arbol una vez por segundo. Si este
    test se rompe, alguien volvio a poner un recorrido sin poda en un bucle de
    un segundo.
    """
    from lilith_tools.watcher import _PollingObserver

    (tmp_path / "codigo.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "imagen.png").write_bytes(b"\x89PNG" + b"\x00" * 16)
    basura = tmp_path / "node_modules"
    basura.mkdir()
    for i in range(20):
        (basura / f"p{i}.js").write_text("ruido\n", encoding="utf-8")

    obs = _PollingObserver.__new__(_PollingObserver)
    obs.entry = type("E", (), {"paths": [str(tmp_path)], "_add_event": lambda *a, **k: None})()
    snap = _PollingObserver._build_snapshot(obs)

    assert len(snap) == 2, f"deberia ver 2 ficheros, vio {len(snap)}"
    assert any("imagen.png" in k for k in snap), "el vigilante debe ver binarios"
    assert not any("node_modules" in k for k in snap)
