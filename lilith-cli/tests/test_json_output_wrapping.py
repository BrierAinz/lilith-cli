"""El JSON legible por maquina no se emite con ``console.print`` pelado.

Rich envuelve al ancho de la consola, y al hacerlo mete un ``\n`` DENTRO del
JSON: la salida deja de parsear con "Invalid control character". Ademas
interpreta los corchetes como markup y colorea numeros y strings con
secuencias ANSI. Nada de eso se nota en la maquina de desarrollo, donde la
consola suele quedar mas ancha que el payload; en CI, con 80 columnas, los
dos tests de ``/now --json`` reventaron.

La regla es usar ``render.print_json``, que desactiva ``soft_wrap``,
``markup`` y ``highlight``. Se verifica sobre el AST en vez de ejecutar cada
comando: vale para cualquier salida ``--json`` nueva, no solo para las que
hoy tienen test. Cuando se escribio, habia tres sitios afectados ademas de
``/now``: ``/stats --json``, ``/map --json`` y el payload estructurado de
``delegate``.

Un ``json.dumps`` anidado dentro de otra llamada (por ejemplo
``console.print(Panel(json.dumps(...)))``) NO cuenta: eso es decoracion para
un humano, no salida para parsear.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PAQUETE = Path(__file__).resolve().parents[1] / "lilith_cli"

# render.py es donde vive print_json, y ahi la llamada cruda es la correcta.
EXENTOS = {"render.py"}


def _es_dumps(nodo: ast.expr) -> bool:
    """True si *nodo* es una llamada a ``json.dumps`` / ``dumps``."""
    if not isinstance(nodo, ast.Call):
        return False
    func = nodo.func
    if isinstance(func, ast.Attribute):
        return func.attr == "dumps"
    return isinstance(func, ast.Name) and func.id == "dumps"


def _es_console_print(nodo: ast.Call) -> bool:
    func = nodo.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "print"
        and isinstance(func.value, ast.Name)
        and func.value.id == "console"
    )


def _infracciones(arbol: ast.AST) -> list[int]:
    """Lineas donde se imprime un dumps directamente por la consola."""
    fuera = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call) or not _es_console_print(nodo):
            continue
        # Solo los argumentos DIRECTOS: un dumps envuelto en Panel(...) u
        # otra llamada es presentacion, no salida para parsear.
        if any(_es_dumps(arg) for arg in nodo.args):
            fuera.append(nodo.lineno)
    return fuera


@pytest.mark.parametrize(
    "ruta",
    sorted(PAQUETE.rglob("*.py")),
    ids=lambda p: str(p.relative_to(PAQUETE)),
)
def test_json_no_se_imprime_por_consola_pelada(ruta: Path) -> None:
    if ruta.name in EXENTOS:
        pytest.skip("render.py define print_json; ahi la llamada cruda es correcta")

    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    lineas = _infracciones(arbol)

    assert not lineas, (
        f"{ruta.relative_to(PAQUETE)}: console.print(json.dumps(...)) en las lineas "
        f"{lineas}. Rich envuelve al ancho de la consola y parte el JSON, que deja "
        f"de parsear. Usar render.print_json en su lugar."
    )


def test_print_json_sobrevive_una_consola_angosta(capsys, monkeypatch) -> None:
    """El helper emite JSON parseable aunque el payload exceda el ancho.

    Cubre a los cuatro comandos que lo usan de una sola vez. Se fija un ancho
    chico para que la condicion sea determinista en cualquier maquina, en vez
    de depender del tamano de terminal del runner.
    """
    import json

    from lilith_cli.render import console, print_json

    monkeypatch.setattr(console, "width", 40)

    payload = {
        "ruta": "/home/runner/work/reino-asgard/lilith-stack/lilith-cli/main.py",
        "simbolos": ["alfa", "beta", "gamma"],
        "cantidad": 3,
    }
    print_json(payload, indent=None)

    out = capsys.readouterr().out.strip()
    assert "\n" not in out, "Rich envolvio el JSON en varias lineas"
    assert "\x1b[" not in out, "Rich metio secuencias ANSI en la salida"
    assert json.loads(out) == payload
