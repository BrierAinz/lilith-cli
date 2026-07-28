"""El catálogo de ``/help`` no debe listar el mismo comando dos veces.

El catálogo de ``run_help_command`` está dividido por categoría (Session,
Configuration, Development, Information, Files & Git, Utilities,
Environment, System, Help). Si un comando aparece en dos categorías,
``/help <cat>`` lo muestra dos veces y ``/help <comando>`` también,
lo cual confunde al usuario y duplica líneas en cualquier render que
agrupe por categoría.

Históricamente ocurrió con /status (Session + Information), /lint-fix
(Development + Utilities) y /tools (Configuration + Help) — tres
duplicados que se fijaron acá como invariante.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRA_COMMANDS = REPO_ROOT / "lilith_cli" / "extra_commands.py"


def _catalog_by_category() -> dict[str, list[str]]:
    """Devuelve ``{categoria: [nombre, ...]}`` del catálogo de ``/help``."""
    tree = ast.parse(EXTRA_COMMANDS.read_text(encoding="utf-8"))
    run_help = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_help_command"
    )
    catalog_node = next(
        node for node in ast.walk(run_help)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "catalog"
        and isinstance(node.value, ast.Dict)
    )
    assert isinstance(catalog_node.value, ast.Dict)
    out: dict[str, list[str]] = {}
    for key_node, value_node in zip(catalog_node.value.keys, catalog_node.value.values):
        assert isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
        assert isinstance(value_node, ast.List)
        names: list[str] = []
        for elt in value_node.elts:
            assert isinstance(elt, ast.Tuple) and elt.elts
            first = elt.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str)
            names.append(first.value)
        out[key_node.value] = names
    return out


def test_no_command_listed_in_two_categories() -> None:
    """Ningún nombre del catálogo puede aparecer en dos categorías."""
    catalog = _catalog_by_category()
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for category, names in catalog.items():
        for name in names:
            if name in seen:
                duplicates.append((name, seen[name], category))
            else:
                seen[name] = category
    assert not duplicates, (
        "Comandos listados en más de una categoría del catálogo /help: "
        f"{duplicates}. Cada comando debe vivir en una sola categoría — "
        "elegí la más específica (Session > Information > Configuration > "
        "Help > etc.) y borrá las otras."
    )


def test_status_listed_once() -> None:
    """``/status`` debe aparecer exactamente una vez en el catálogo."""
    catalog = _catalog_by_category()
    appearances = [
        (cat, names.count("status"))
        for cat, names in catalog.items()
        if "status" in names
    ]
    assert sum(c for _, c in appearances) == 1, (
        f"/status aparece {appearances} veces. Debería estar en una sola "
        f"categoría (típicamente Session)."
    )


def test_lint_fix_listed_once() -> None:
    """``/lint-fix`` debe aparecer exactamente una vez en el catálogo."""
    catalog = _catalog_by_category()
    appearances = [
        (cat, names.count("lint-fix"))
        for cat, names in catalog.items()
        if "lint-fix" in names
    ]
    assert sum(c for _, c in appearances) == 1, (
        f"/lint-fix aparece {appearances} veces. Debería estar en una sola "
        f"categoría (típicamente Development)."
    )


def test_tools_listed_once() -> None:
    """``/tools`` debe aparecer exactamente una vez en el catálogo."""
    catalog = _catalog_by_category()
    appearances = [
        (cat, names.count("tools"))
        for cat, names in catalog.items()
        if "tools" in names
    ]
    assert sum(c for _, c in appearances) == 1, (
        f"/tools aparece {appearances} veces. Configuration describe la "
        f"funcionalidad (habilitar/deshabilitar herramientas); Help lista "
        f"el comando en sí. Elegí una — preferentemente Configuration."
    )