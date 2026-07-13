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
