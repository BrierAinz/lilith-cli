"""El catálogo de /help debe documentar todos los comandos canónicos de /autocompletar.

Este test es la inversa de ``test_autocomplete_covers_help_catalog``. Mientras
ese asegura que todo lo documentado se autocomplete (``/help`` → ``_SLASH_COMMANDS``),
este asegura que todo comando canónico en ``_SLASH_COMMANDS`` también aparezca
en el catálogo (es decir, que no haya comandos implementados, despachados y
autocompletados que sean invisibles en ``/help``).

Los alias cortos (1-4 letras, p.ej. ``/c``, ``/bm``, ``/peeks``, ``/dcfg``)
son alias legítimos de un comando canónico ya documentado y por eso no
necesitan entrada propia. Este test filtra esos alias expandiendo el mapa
``alias → canónico`` que provee cada ``BaseCommand`` y exige:

1. Que el nombre canónico (no el alias) figure en la lista de ``_SLASH_COMMANDS``.
2. Que ese nombre canónico también esté presente en el catálogo de ``/help``.

Cierra la grieta de 2026-07-27: ``/memory`` y ``/system`` eran autodocumentados
en ``_SLASH_COMMANDS`` pero invisibles en ``/help``. Al exponerlos en el
catálogo cualquier usuario los descubre sin tener que tipearlos al azar.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPL = REPO_ROOT / "lilith_cli" / "repl.py"
EXTRA_COMMANDS = REPO_ROOT / "lilith_cli" / "extra_commands.py"
COMMANDS = REPO_ROOT / "lilith_cli" / "commands.py"


def _catalog_names() -> set[str]:
    """Nombres declarados en el catálogo de ``run_help_command``."""
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


def _canonical_and_aliases() -> tuple[set[str], dict[str, str]]:
    """Devuelve (nombres canónicos, mapa alias→canónico) leyendo BaseCommand.

    Un nombre canónico es el ``name = "x"`` declarado en una clase que hereda
    de ``BaseCommand``. Los ``aliases`` declarados en la misma clase se
    mapean al canónico. Usamos el AST para no arrastrar todo el paquete a un
    import que solo necesitamos para introspección.
    """
    tree = ast.parse(COMMANDS.read_text(encoding="utf-8"))
    canonical: set[str] = set()
    alias_to_canon: dict[str, str] = {}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.ClassDef) and any(
            isinstance(b, ast.Name) and b.id == "BaseCommand" for b in n.bases
        )):
            continue
        canon_name: str | None = None
        for stmt in n.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for tgt in stmt.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                if tgt.id == "name" and isinstance(stmt.value, ast.Constant):
                    canon_name = stmt.value.value
                if tgt.id == "aliases" and isinstance(stmt.value, ast.List):
                    for el in stmt.value.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            if canon_name:
                                alias_to_canon[el.value] = canon_name
        if canon_name:
            canonical.add(canon_name)
    return canonical, alias_to_canon


def _resolve_to_canonicals() -> dict[str, str]:
    """Para cada nombre en _SLASH_COMMANDS, devuelve el canónico BaseCommand.

    Reglas:
    - Si el nombre coincide con un alias → apunta al canónico.
    - Si coincide con el canónico → apunta al canónico.
    - Si no tiene clase BaseCommand asociada → apunta al mismo nombre
      (handlers sueltos en extra_commands / *_command.py).
    """
    _, alias_to_canon = _canonical_and_aliases()
    listed = _slash_commands()
    out: dict[str, str] = {}
    # Invertimos el mapa: canonico -> canonico (identity)
    canonical_set: set[str] = set(alias_to_canon.values()) | set(alias_to_canon.keys())
    for name in listed:
        out[name] = alias_to_canon.get(name, name)
    return out


def test_slash_canonicals_documented_in_help() -> None:
    """Todo nombre canónico BaseCommand presente en ``_SLASH_COMMANDS``
    debe estar documentado en ``/help``, ya sea por su nombre canónico
    o por cualquiera de sus aliases.

    Ejemplo: ``/resume`` (canónico) y ``/load`` (alias) son equivalentes.
    Si ``/load`` aparece en el catálogo, ``/resume`` queda cubierto y no
    duplica la entrada. Esto evita inventariar alias redundantes.
    """
    catalog = _catalog_names()
    canonical, alias_to_canon = _canonical_and_aliases()

    # Para cada canónico relevante en _SLASH_COMMANDS, ¿alguna de sus
    # variantes (canónico + aliases) está en el catálogo?
    listed = _slash_commands()
    canonicals_in_list = {
        alias_to_canon[name] if name in alias_to_canon else name
        for name in listed
        if name in canonical or name in alias_to_canon
    }

    # Invertimos alias_to_canon → canonico → set(aliases + {canonico})
    canon_to_all: dict[str, set[str]] = {}
    for a, c in alias_to_canon.items():
        canon_to_all.setdefault(c, set()).add(a)
    for c in canonical:
        canon_to_all.setdefault(c, set()).add(c)

    missing: list[str] = []
    for c in sorted(canonicals_in_list):
        variants = canon_to_all.get(c, {c})
        if not (variants & catalog):
            missing.append(c)

    assert not missing, (
        "comandos canónicos BaseCommand en _SLASH_COMMANDS sin entrada en "
        f"el catálogo de /help (ni canónico ni alias): {missing}. Agrega al "
        "menos uno de sus nombres como tupla (nombre, descripción) dentro del "
        "dict-literal ``catalog`` de extra_commands.run_help_command."
    )


def test_canonical_aliases_resolve_to_documented_name() -> None:
    """Cada canónico debe aparecer en ``/help``, ya sea por su nombre o por
    cualquiera de sus aliases.

    Detecta el caso degenerado: alguien crea una nueva clase BaseCommand
    con ``name = \"foo\"`` pero olvida agregar ``\"foo\"`` o uno de sus alias al
    catálogo. Si el canónico y todos sus aliases faltan del catálogo, el
    usuario nunca lo descubre vía ``/help``.
    """
    catalog = _catalog_names()
    canonical, alias_to_canon = _canonical_and_aliases()

    canon_to_all: dict[str, set[str]] = {}
    for a, c in alias_to_canon.items():
        canon_to_all.setdefault(c, set()).add(a)
    for c in canonical:
        canon_to_all.setdefault(c, set()).add(c)

    orphans = sorted(c for c in canonical if not (canon_to_all.get(c, {c}) & catalog))
    assert not orphans, (
        "BaseCommand canónicos (y todos sus aliases) sin entrada en /help: "
        f"{orphans}. Agregá al menos uno al catálogo dentro de "
        "extra_commands.run_help_command."
    )
