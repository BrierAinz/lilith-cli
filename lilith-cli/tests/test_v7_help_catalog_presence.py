"""Tanda 16 — presencia de los comandos v7 recientes en /help y /quickstart.

Los comandos ``/conclave`` (commit ``550c494``) y ``/learn`` (``b50f062``)
se agregaron al REPL en las tandas 11 y 14 pero nunca aparecieron en el
catálogo de ``/help``. El comando ``lilith doctor`` (``2ffcac4``) y los
flags nuevos de ``lilith delegate`` (``cef0529``) tampoco figuraban en el
quickstart. Este test (archivo nuevo por regla de las tandas) verifica
que el catálogo y el tour reflejen el arsenal completo del programa v7.

Patrón: parsea el dict literal del catálogo y el body de
``QuickstartCommand._brief_tour`` directamente (sin instanciar la app),
igual que ``test_help_catalog_wiring.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRA_COMMANDS = REPO_ROOT / "lilith_cli" / "extra_commands.py"
COMMANDS = REPO_ROOT / "lilith_cli" / "commands.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def _catalog_names() -> dict[str, list[str]]:
    """Parsea ``run_help_command.catalog`` -> ``{categoria: [cmd, ...]}``."""

    tree = _module(EXTRA_COMMANDS)
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
    out: dict[str, list[str]] = {}
    assert isinstance(catalog_node.value, ast.Dict)
    for key_node, value_node in zip(
        catalog_node.value.keys, catalog_node.value.values
    ):
        assert isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
        assert isinstance(value_node, ast.List)
        names: list[str] = []
        for elt in value_node.elts:
            assert isinstance(elt, ast.Tuple) and len(elt.elts) >= 1
            first = elt.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str)
            names.append(first.value)
        out[key_node.value] = names
    return out


def _quickstart_body() -> str:
    """Devuelve el cuerpo textual de ``QuickstartCommand._brief_tour``."""

    tree = _module(COMMANDS)
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "QuickstartCommand"
    )
    method = next(
        n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_brief_tour"
    )
    # Unparse each stmt and join so we can substring-match the literal
    # lines the user reads in the panel.
    return "\n".join(ast.unparse(stmt) for stmt in method.body)


@pytest.mark.parametrize("cmd", ["conclave", "learn"])
def test_recent_slash_command_is_in_help_catalog(cmd: str) -> None:
    """``/conclave`` y ``/learn`` deben figurar en el catálogo de ``/help``."""

    catalog = _catalog_names()
    flat = {name for names in catalog.values() for name in names}
    assert cmd in flat, (
        f"/{cmd} falta del catálogo /help. "
        f"Categorías: {sorted(catalog)}. "
        f"Plano: {sorted(flat)}"
    )


def test_conclave_listed_under_system() -> None:
    """``/conclave`` es un comando de sistema (orquestación)."""

    catalog = _catalog_names()
    system = catalog.get("System", [])
    assert "conclave" in system, (
        f"/conclave debe estar en 'System' (vecino de /subagents y /mcp). "
        f"System actual: {system}"
    )


def test_learn_listed_under_information() -> None:
    """``/learn`` está ligado a telemetría de delegaciones (costas/skills)."""

    catalog = _catalog_names()
    info = catalog.get("Information", [])
    assert "learn" in info, (
        f"/learn debe estar en 'Information' (vecino de /costs y /skills). "
        f"Information actual: {info}"
    )


def test_quickstart_mentions_lilith_doctor() -> None:
    """El tour debe mencionar ``lilith doctor`` (commit ``2ffcac4``)."""

    body = _quickstart_body()
    assert "lilith doctor" in body, (
        "El quickstart debe listar 'lilith doctor' bajo el bloque v7. "
        "Patch: añade sections.append('  [bold cyan]lilith doctor[/] ...') "
        "en QuickstartCommand._brief_tour dentro del bloque v7."
    )


@pytest.mark.parametrize(
    "flag",
    ["--preset", "--agentic", "--structured", "--max-tokens", "--max-turns"],
)
def test_quickstart_mentions_delegate_flags(flag: str) -> None:
    """Los cinco flags de ``lilith delegate`` (commit ``cef0529``) en el tour."""

    body = _quickstart_body()
    assert flag in body, (
        f"Falta {flag} en el quickstart (commit cef0529). "
        "Línea esperada: sections.append con 'lilith delegate --preset/"
        "--agentic/--structured/--max-tokens/--max-turns'"
    )


def test_quickstart_mentions_conclave_and_learn() -> None:
    """El tour debe llamar a ``/conclave`` y ``/learn`` por su nombre."""

    body = _quickstart_body()
    assert "/conclave" in body and "/learn" in body, (
        "El quickstart debe listar /conclave y /learn en el bloque v7."
    )