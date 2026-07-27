"""Tanda 17 — cerrar la brecha entre el REPL y el catalogo de ``/help``.

Históricamente, comandos que se dispatchan desde ``repl.py`` (es decir, los
que el usuario realmente puede invocar) no aparecen en el dict-literal del
catalogo dentro de ``run_help_command``. Esto los hace invisibles para el
usuario: ``/help`` no los lista, la finalización por tab los omite del menú
de sugerencias agrupadas, y la documentacion de discovery queda incompleta.

Esta tanda enumera los comandos que ya están *despachados* en el REPL
(``cmd_name == "..."``) pero faltan del catalogo, y exige que cada uno
figure en al menos una categoria. Asi evitamos que un comando nuevo se
despache sin documentarse.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
REPL = REPO_ROOT / "lilith_cli" / "repl.py"
EXTRA_COMMANDS = REPO_ROOT / "lilith_cli" / "extra_commands.py"


def _dispatched_slash_commands() -> set[str]:
    """Devuelve el set de ``cmd_name`` que el REPL dispatcha explicitamente."""
    src = REPL.read_text(encoding="utf-8")
    return set(re.findall(r'cmd_name == "([a-z][a-z0-9\-_]+)"', src))


def _catalog_names() -> set[str]:
    """Devuelve los nombres de comando declarados en el catalogo de ``/help``."""
    tree = ast.parse(EXTRA_COMMANDS.read_text(encoding="utf-8"))
    run_help = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "run_help_command"
    )
    catalog_node = next(
        n for n in ast.walk(run_help)
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "catalog"
        and isinstance(n.value, ast.Dict)
    )
    out: set[str] = set()
    assert isinstance(catalog_node.value, ast.Dict)
    for value_node in catalog_node.value.values:
        assert isinstance(value_node, ast.List)
        for elt in value_node.elts:
            assert isinstance(elt, ast.Tuple) and len(elt.elts) >= 1
            first = elt.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str)
            out.add(first.value)
    return out


# Comandos que el REPL dispatcha y que ya figuraban en el catalogo al
# cierre de la tanda 16. El test verifica que ninguno desaparezca.
KNOWN_PRESENT = {
    "agent", "alias", "auto", "base64", "bench", "bookmark", "calc", "capture",
    "cd", "changelog", "cls", "compare", "compact", "conclave", "config",
    "continue", "copy", "deps", "doctor", "editor", "env", "epoch", "explain",
    "export", "feedback", "file", "fork", "format", "git", "goal", "hash",
    "help", "history", "hooks", "init", "json", "json-mode", "learn", "lines",
    "lint", "lint-fix", "log", "macro", "metrics", "model", "model-info",
    "multi-file", "now", "pin", "plan", "profile", "pwd", "qr",
    "recap", "recent", "redo", "release", "replay", "review",
    "search", "secret", "snippet", "status", "stream", "summary", "template",
    "test", "theme", "timer", "tip", "tokens", "todos", "tour", "tree",
    "undo", "usage", "uuid", "voice", "watch", "whereami", "ygg",
}


def test_dispatched_commands_are_in_catalog() -> None:
    """Cada ``cmd_name`` despachado debe figurar en el catalogo de ``/help``.

    Si un comando nuevo se agrega a ``repl.py`` sin documentarlo en
    ``run_help_command.catalog``, este test falla y obliga a actualizar
    el catalogo (que es el contrato visible para el usuario).
    """
    dispatched = _dispatched_slash_commands()
    catalog = _catalog_names()
    missing = sorted(dispatched - catalog)
    assert not missing, (
        f"Comandos despachados en repl.py pero ausentes del catalogo /help: "
        f"{missing}. Agrega cada uno a la categoria correspondiente dentro "
        f"de run_help_command.catalog en extra_commands.py."
    )


def test_known_present_commands_still_listed() -> None:
    """Asegura que los comandos que ya estaban documentados no desaparezcan."""
    catalog = _catalog_names()
    missing = sorted(KNOWN_PRESENT - catalog)
    assert not missing, (
        f"Estos comandos solian estar en el catalogo y ahora faltan: {missing}. "
        f"Si los retiras intencionalmente, actualiza KNOWN_PRESENT en el test."
    )


@pytest.mark.parametrize(
    "cmd",
    ["batch", "completion", "context", "how", "map", "note", "pipeline",
     "quote", "random", "workflow"],
)
def test_recently_dispatched_command_in_catalog(cmd: str) -> None:
    """Pin de comandos despachados que omitimos del catalogo en tandas previas.

    Cada uno de estos ya tiene ``run_X_command`` (verificado por smoke test)
    y un branch de dispatch en ``repl.py``. Antes de esta tanda, ninguno
    aparecia en ``/help``. Este test parametrizado bloquea la regresion.
    """
    catalog = _catalog_names()
    assert cmd in catalog, (
        f"/{cmd} debe estar en el catalogo /help. "
        f"Comandos actuales: {sorted(catalog)}"
    )