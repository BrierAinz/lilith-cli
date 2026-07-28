"""El autocompletado debe cubrir todo lo que ``/help`` documenta.

``test_help_catalog_completeness`` cierra una mitad de la brecha: que todo
comando despachado esté documentado. Falta la otra mitad, que es la que ve
el usuario: un comando puede estar documentado en ``/help`` y aun así no
figurar en ``_SLASH_COMMANDS``, con lo cual el Tab nunca lo sugiere. El
usuario lo lee en la ayuda, lo escribe a mano y funciona — pero no hay forma
de descubrirlo tipeando.

Al 2026-07-27 eran 15 comandos en esa situación: /base64, /calc, /config,
/diff, /init, /save, /system, /undo, /ygg, /commands, /quickstart, /bifrost
y los alias /preview, /start, /tool.

Los alias cortos (``/c``, ``/u``, ``/cp``…) quedan deliberadamente fuera del
autocompletado para no saturar el menú, y por eso este test mira el catálogo
—lo que se documenta— y no el conjunto de nombres resolubles.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPL = REPO_ROOT / "lilith_cli" / "repl.py"
EXTRA_COMMANDS = REPO_ROOT / "lilith_cli" / "extra_commands.py"


def _catalog_names() -> set[str]:
    """Nombres declarados en el catálogo de ``run_help_command``.

    Se lee por AST y no importando el módulo: ``extra_commands`` arrastra
    todo el paquete, y acá sólo interesa el dict-literal.
    """
    tree = ast.parse(EXTRA_COMMANDS.read_text(encoding="utf-8"))
    run_help = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "run_help_command"
    )
    catalog_node = next(
        n
        for n in ast.walk(run_help)
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "catalog"
        and isinstance(n.value, ast.Dict)
    )
    out: set[str] = set()
    for value_node in catalog_node.value.values:
        if not isinstance(value_node, ast.List):
            continue
        for elt in value_node.elts:
            if not (isinstance(elt, ast.Tuple) and elt.elts):
                continue
            first = elt.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(first.value)
    return out


def _slash_commands() -> set[str]:
    """Nombres (sin la barra) listados en ``_SLASH_COMMANDS`` de repl.py."""
    src = REPL.read_text(encoding="utf-8")
    block = re.search(r"_SLASH_COMMANDS\s*=\s*\[(.*?)\n\]", src, re.S)
    assert block, "no se encontró _SLASH_COMMANDS — ¿cambió la convención?"
    return set(re.findall(r'"/([^"]+)"', block.group(1)))


def test_autocomplete_covers_help_catalog() -> None:
    """Todo comando documentado en /help debe poder autocompletarse."""
    catalog = _catalog_names()
    listed = _slash_commands()
    assert catalog, "el catálogo de /help quedó vacío — revisá el parser"

    missing = sorted(catalog - listed)
    assert not missing, (
        "comandos documentados en /help pero ausentes de _SLASH_COMMANDS: "
        f"{missing}. El usuario los ve en la ayuda y el Tab no se los "
        "completa. Agregalos a _SLASH_COMMANDS en repl.py."
    )


def test_slash_commands_has_no_duplicates() -> None:
    """Una entrada repetida ensucia el menú de sugerencias."""
    src = REPL.read_text(encoding="utf-8")
    block = re.search(r"_SLASH_COMMANDS\s*=\s*\[(.*?)\n\]", src, re.S)
    assert block
    todos = re.findall(r'"/([^"]+)"', block.group(1))
    repetidos = sorted({n for n in todos if todos.count(n) > 1})
    assert not repetidos, f"entradas duplicadas en _SLASH_COMMANDS: {repetidos}"
