"""Todo subprocess con ``text=True`` debe declarar ``encoding``.

En Windows, ``text=True`` sin ``encoding`` decodifica la salida con el codec
del locale (cp1252), que lanza ``UnicodeDecodeError`` ante cualquier byte
UTF-8 que no mapee. Este repo escribe español con acentos y runas (᛭), así que
``git diff`` produce esa salida constantemente: ``/diff-unstaged`` crasheaba
con el working tree sucio de contenido normal del proyecto.

Se verifica sobre el AST en vez de ejecutar los comandos: la regla vale para
cualquier llamada nueva, no solo para las que hoy tienen test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PAQUETE = Path(__file__).resolve().parents[1] / "lilith_cli"
FUNCIONES_SUBPROCESS = {"run", "Popen", "check_output", "call", "check_call"}


def _llamadas_subprocess(arbol: ast.AST):
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        func = nodo.func
        # subprocess.run(...) / sp.Popen(...)
        if isinstance(func, ast.Attribute) and func.attr in FUNCIONES_SUBPROCESS:
            yield nodo
        # run(...) importado directo
        elif isinstance(func, ast.Name) and func.id in FUNCIONES_SUBPROCESS:
            yield nodo


def _kwargs(nodo: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in nodo.keywords if kw.arg}


def _es_texto(kwargs: dict[str, ast.expr]) -> bool:
    for clave in ("text", "universal_newlines"):
        valor = kwargs.get(clave)
        if isinstance(valor, ast.Constant) and valor.value is True:
            return True
    return False


ARCHIVOS = sorted(PAQUETE.rglob("*.py"))


def test_hay_archivos_para_revisar():
    """Si esto falla, el descubrimiento se rompió y el resto pasa en vacío."""
    assert len(ARCHIVOS) >= 5, ARCHIVOS


@pytest.mark.parametrize("ruta", ARCHIVOS, ids=lambda p: p.name)
def test_subprocess_en_modo_texto_declara_encoding(ruta: Path):
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    sin_encoding = [
        nodo.lineno
        for nodo in _llamadas_subprocess(arbol)
        if _es_texto(kw := _kwargs(nodo)) and "encoding" not in kw
    ]

    assert not sin_encoding, (
        f"{ruta.name}: subprocess en modo texto sin encoding explícito en las "
        f"líneas {sin_encoding}. En Windows decodifica con cp1252 y explota "
        f"con salida UTF-8; usar encoding='utf-8', errors='replace'."
    )
