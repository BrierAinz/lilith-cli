"""Detector de "cableado a medias": handlers despachados en repl.py deben
estar importados como nombres del módulo, si no se lanza NameError en
runtime apenas alguien escriba el slash.

Ticks anteriores documentaron este modo de falla: ``/apply`` se despachaba
SIN haber sido importado en repl.py — el NameError aparecía al tipearlo,
no al arrancar, con lo cual los tests que llamaban al handler directo
pasaban y el bug se colaba en producción.

``test_repl_wiring.py`` ya cubre el camino "command → handler" desde el
punto de vista del dispatch cuando el handler vive en un módulo dedicado.
Acá cubrimos el otro camino: cuando ``repl.py`` invoca un ``run_X_command``
inline, ese símbolo tiene que estar atado a un import a nivel de módulo.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPL = Path(__file__).resolve().parent.parent / "lilith_cli" / "repl.py"


def _invoked_handlers(tree: ast.Module) -> set[str]:
    """Nombres de la forma run_X_command invocados desde repl.py."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        if (
            isinstance(fn, ast.Name)
            and fn.id.startswith("run_")
            and fn.id.endswith("_command")
        ):
            out.add(fn.id)
    return out


def _bound_names(tree: ast.Module) -> set[str]:
    """Nombres atados a un import a nivel de módulo (no asignaciones)."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                out.add(n.asname or n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for n in node.names:
                out.add(n.asname or n.name)
    return out


def test_run_handlers_invoked_in_repl_are_imported() -> None:
    """Todo ``run_X_command`` invocado en repl.py debe estar importado.

    Si falta el import, el slash command se rompe en runtime con
    NameError — exactamente el modo de falla que este test blinda.
    """
    tree = ast.parse(REPL.read_text(encoding="utf-8"))
    invoked = _invoked_handlers(tree)
    bound = _bound_names(tree)

    assert invoked, (
        "No se encontraron invocaciones run_X_command — ¿se renombró la "
        "convención? Actualizá este test."
    )

    missing = sorted(invoked - bound)
    assert not missing, (
        "Handlers despachados en repl.py pero NO importados al módulo "
        f"(van a explotar con NameError en runtime): {missing}. "
        "Agregá el import correspondiente en repl.py."
    )
