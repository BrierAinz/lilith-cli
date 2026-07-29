"""Guardián de wiring del REPL.

Los agentes que agregan slash commands suelen cablear el dispatcher de
``repl.py`` sin agregar el import correspondiente, lo que produce un
``NameError`` en runtime que ningún unit test del comando detecta
(los tests llaman al handler directo, no vía dispatcher).
Este test falla si algún ``run_*_command`` referenciado en ``repl.py``
no está importado o definido a nivel de módulo.
"""

from __future__ import annotations

import re
from pathlib import Path

import lilith_cli.repl as repl_module


def test_repl_command_handlers_are_defined() -> None:
    source = Path(repl_module.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r"\b(run_[a-z0-9_]+_command)\b", source))
    assert used, "no se encontraron handlers en repl.py — ¿cambió la convención?"
    missing = sorted(name for name in used if not hasattr(repl_module, name))
    assert not missing, (
        f"handlers usados en repl.py sin importar/definir: {missing}"
    )


def test_repl_imported_handlers_are_dispatched() -> None:
    """Todo handler ``run_X_command`` importado en repl.py debe tener al
    menos un ``await run_X_command(...)`` (o ``run_X_command(...)`` en
    síncronos heredados) en el mismo archivo. De lo contrario el slash
    command está en el autocompletado y el handler existe, pero la barra
    del usuario nunca lo invoca: cae al agente como prompt libre.

    Esto ya pasó con ``/feedback`` (deleg d9685cd6): import presente,
    autocompletado presente, pero ningún ``if cmd_name == "feedback"``
    en el dispatcher.
    """
    source = Path(repl_module.__file__).read_text(encoding="utf-8")

    imported = set(re.findall(r"^\s+run_[a-z0-9_]+_command", source, re.MULTILINE))
    # Filtra falsos positivos: comentarios, re-exports que no son handlers.
    # Solo nos importan los que están bindeados al módulo REPL con el patrón
    # ``from .x import run_Y_command`` o ``run_Y_command,`` en una lista.
    bindings = set(re.findall(
        r"^\s+(run_[a-z0-9_]+_command)(?:\s*,|\s*$)", source, re.MULTILINE
    ))

    invoked = set(re.findall(
        r"\bawait\s+(run_[a-z0-9_]+_command)\s*\(", source
    )) | set(re.findall(
        r"^\s+(run_[a-z0-9_]+_command)\s*\(\s*session\b",
        source, re.MULTILINE
    ))

    orphans = sorted(bindings - invoked)
    assert not orphans, (
        "handlers importados en repl.py pero nunca despachados "
        f"(el slash command cae al agente): {orphans}"
    )
