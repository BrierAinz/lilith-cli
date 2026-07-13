"""Wiring test for the /help command catalog vs the slash-command dispatcher.

The ``run_help_command`` function in ``extra_commands.py`` carries a hardcoded
``catalog`` dict that lists every command grouped by category. That catalog can
silently drift away from the real dispatcher (``repl.py``) and the
``CommandRegistry`` (``commands.py``). This test parses those files with ``ast``
(no app instantiation) and asserts that every catalogued command is reachable
through the dispatcher.

Scope of the extraction (intentionally narrow, no app imports):

* ``extra_commands.py`` — find ``run_help_command`` and walk its ``catalog``
  dict literal, collecting the first element of each ``(name, desc)`` tuple.
* ``repl.py`` — collect string literals that appear on the right-hand side of
  ``cmd_name == "..."`` and inside tuples in ``cmd_name in ("...", "...")``.
  Both forms are real dispatcher branches in ``run_repl``.
* ``commands.py`` — for every ``class X(BaseCommand)`` collect the ``name``
  string and every entry of the ``aliases`` list. These land in
  ``CommandRegistry._commands`` / ``_aliases`` and are reachable via
  ``registry.dispatch`` (which ``run_repl`` calls as a fallback).

If the assertion fails, the test lists exactly which catalogued names are not
wiring-checked (i.e. potentially orphaned). When the baseline has known gaps
that the project has not yet fixed, the test asserts the gap list equals a
documented ``KNOWN_MISSING_FROM_DISPATCHER`` snapshot so the test stays green
and surfaces drift on the next change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRA_COMMANDS = REPO_ROOT / "lilith_cli" / "extra_commands.py"
REPL = REPO_ROOT / "lilith_cli" / "repl.py"
COMMANDS = REPO_ROOT / "lilith_cli" / "commands.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def extract_catalog() -> dict[str, list[str]]:
    """Return ``{category: [cmd_name, ...]}`` from ``run_help_command.catalog``."""

    tree = _module(EXTRA_COMMANDS)
    run_help = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run_help_command"
        ),
        None,
    )
    assert run_help is not None, "run_help_command not found in extra_commands.py"

    catalog_node = next(
        (
            node
            for node in ast.walk(run_help)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "catalog"
            and isinstance(node.value, ast.Dict)
        ),
        None,
    )
    assert catalog_node is not None, (
        "Annotated `catalog: dict[...] = {...}` literal not found inside "
        "run_help_command"
    )

    catalog: dict[str, list[str]] = {}
    assert isinstance(catalog_node.value, ast.Dict)
    for key_node, value_node in zip(catalog_node.value.keys, catalog_node.value.values):
        assert isinstance(key_node, ast.Constant) and isinstance(key_node.value, str), (
            f"Catalog key must be a string literal, got {ast.dump(key_node)}"
        )
        assert isinstance(value_node, ast.List), (
            f"Catalog value for {key_node.value!r} must be a list literal"
        )
        names: list[str] = []
        for elt in value_node.elts:
            assert isinstance(elt, ast.Tuple) and len(elt.elts) >= 1, (
                f"Each catalog entry must be a tuple with at least 1 element, "
                f"got {ast.dump(elt)}"
            )
            first = elt.elts[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                f"Catalog command name must be a string literal, got {ast.dump(first)}"
            )
            names.append(first.value)
        catalog[key_node.value] = names

    return catalog


def extract_dispatcher_commands_repl() -> set[str]:
    """String literals reachable from ``cmd_name == "..."`` / ``cmd_name in (...)`` in repl.py."""

    tree = _module(REPL)
    names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id == "cmd_name"):
            continue

        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant) and isinstance(
                comparator.value, str
            ):
                names.add(comparator.value)
            elif isinstance(op, ast.In) and isinstance(comparator, (ast.Tuple, ast.List)):
                for elt in comparator.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.add(elt.value)
    return names


def extract_dispatcher_commands_registry() -> set[str]:
    """Names + aliases of every ``BaseCommand`` subclass in commands.py."""

    tree = _module(COMMANDS)
    names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {
            base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else None
            )
            for base in node.bases
        }
        if "BaseCommand" not in base_names:
            continue

        for stmt in node.body:
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue

            if target.id == "name":
                if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    if stmt.value.value:
                        names.add(stmt.value.value)
            elif target.id == "aliases":
                if isinstance(stmt.value, (ast.List, ast.Tuple)):
                    for elt in stmt.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            if elt.value:
                                names.add(elt.value)

    return names


def extract_autocomplete_commands() -> set[str]:
    """Entries of the ``_SLASH_COMMANDS`` autocomplete list in repl.py, sin ``/``."""

    tree = _module(REPL)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "_SLASH_COMMANDS"):
            continue
        assert isinstance(node.value, ast.List), "_SLASH_COMMANDS must be a list literal"
        return {
            elt.value.lstrip("/")
            for elt in node.value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
    raise AssertionError("_SLASH_COMMANDS not found in repl.py")


def test_autocomplete_commands_are_dispatchable() -> None:
    """Every /command offered by autocomplete must actually be dispatchable.

    Regression: /model-info sat in _SLASH_COMMANDS with its handler imported
    but had no dispatcher branch — typing it produced "Comando desconocido".
    """

    autocomplete = extract_autocomplete_commands()
    dispatchable = (
        extract_dispatcher_commands_repl() | extract_dispatcher_commands_registry()
    )
    missing = sorted(autocomplete - dispatchable)
    assert not missing, (
        f"comandos ofrecidos por autocomplete sin rama de dispatch: {missing}"
    )


KNOWN_MISSING_FROM_DISPATCHER: dict[str, str] = {
    # Empty since 2026-07-12: /alias /env /replay /tour were the original
    # documented gaps and got dispatcher branches in repl.py. Add entries
    # here ONLY as a temporary baseline for gaps that cannot be fixed in
    # the same change.
}


def test_help_catalog_is_wired_to_dispatcher() -> None:
    """Every name in the /help catalog must be reachable via repl.py or commands.py."""

    catalog = extract_catalog()
    flat_catalog = {name for names in catalog.values() for name in names}

    repl_names = extract_dispatcher_commands_repl()
    registry_names = extract_dispatcher_commands_registry()
    dispatchable = repl_names | registry_names

    missing = sorted(flat_catalog - dispatchable)

    actual_gaps = {
        name: KNOWN_MISSING_FROM_DISPATCHER[name]
        for name in missing
        if name in KNOWN_MISSING_FROM_DISPATCHER
    }
    unexpected_gaps = sorted(set(missing) - set(KNOWN_MISSING_FROM_DISPATCHER))
    stale_baseline = sorted(set(KNOWN_MISSING_FROM_DISPATCHER) - set(missing))

    report_lines = [
        "Help catalog wiring report",
        "==========================",
        f"Catalog commands : {len(flat_catalog)} across {len(catalog)} categories",
        f"Repl dispatcher  : {len(repl_names)} names",
        f"Registry names   : {len(registry_names)} names",
        f"Dispatchable un. : {len(dispatchable)} names",
        f"Missing (gap)    : {len(missing)} -> {missing}",
    ]
    if unexpected_gaps:
        report_lines.append(
            "UNEXPECTED GAPS (not in KNOWN_MISSING_FROM_DISPATCHER): "
            f"{unexpected_gaps}"
        )
    if stale_baseline:
        report_lines.append(
            "STALE BASELINE entries that are now wired (consider removing from "
            f"KNOWN_MISSING_FROM_DISPATCHER): {stale_baseline}"
        )

    assert not unexpected_gaps, "\n".join(report_lines)
    assert not stale_baseline, "\n".join(report_lines)

    assert missing == sorted(KNOWN_MISSING_FROM_DISPATCHER), "\n".join(report_lines)


def test_help_catalog_extraction_is_stable() -> None:
    """Sanity: the AST extraction of the catalog itself returns the expected categories."""

    catalog = extract_catalog()

    assert "Session" in catalog
    assert "Configuration" in catalog
    assert "Development" in catalog
    assert "Information" in catalog
    assert "Files & Git" in catalog
    assert "Utilities" in catalog
    assert "Environment" in catalog
    assert "System" in catalog
    assert "Help" in catalog


def test_qr_is_in_dispatcher() -> None:
    """Regression: /qr was the original motivation for this wiring test."""

    repl_names = extract_dispatcher_commands_repl()
    registry_names = extract_dispatcher_commands_registry()
    assert "qr" in repl_names | registry_names, (
        "/qr disappeared from the dispatcher; check repl.py and commands.py"
    )


@pytest.mark.parametrize("catalog_name", [
    "clear", "compact", "history", "undo", "redo", "save", "export",
    "bookmark", "copy", "quit", "log", "capture", "config", "model",
    "provider", "theme", "tools", "profile", "plan", "init", "file",
    "macro", "template", "lint", "test", "fork", "agent", "auto", "cost",
    "tokens", "usage", "metrics", "whereami", "doctor", "deps", "now",
    "git", "diff", "diff-config", "diff-staged", "tree", "multi-file",
    "hash", "uuid", "json", "base64", "lines", "reverse", "tip", "compare",
    "secret", "bifrost", "ygg", "feedback", "recap", "summary", "help",
    "quickstart", "commands", "changelog",
])
def test_well_known_catalog_command_is_dispatchable(catalog_name: str) -> None:
    """Spot-check the canonical commands of each category."""

    if catalog_name in KNOWN_MISSING_FROM_DISPATCHER:
        pytest.skip(f"{catalog_name} is a documented gap")

    repl_names = extract_dispatcher_commands_repl()
    registry_names = extract_dispatcher_commands_registry()
    assert catalog_name in repl_names | registry_names, (
        f"/{catalog_name} is in the help catalog but not reachable through "
        f"repl.py or commands.py"
    )
