"""Focused tests for /macro edit editor selection and command parsing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from lilith_cli.commands import MacroCommand


class _Session:
    history: list = []


@pytest.fixture
def macros_path(tmp_path, monkeypatch):
    path = tmp_path / "macros.json"
    path.write_text(json.dumps({"deploy": ["/status"]}), encoding="utf-8")
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", path)
    return path


@pytest.mark.asyncio
async def test_macro_edit_uses_editor_command_with_arguments(
    macros_path, monkeypatch
):
    """EDITOR may contain arguments such as ``code --wait``."""
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text("/status\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.setattr("subprocess.run", fake_run)

    await MacroCommand(_Session()).execute("edit deploy")

    assert calls and calls[0][:2] == ["code", "--wait"]
    assert calls[0][-1].endswith(".lilith-macro")


# El fallback de editor se decide con `os.name == "nt"`, y `os` se importa
# DENTRO de la funcion, asi que no hay un atributo de modulo para parchear.
# Parchear el `os.name` global tampoco sirve: `pathlib.Path` elige su
# flavour con esa misma variable, asi que ponerla en "nt" bajo Linux hace
# que cualquier `Path(...)` intente construir un WindowsPath y reviente con
# NotImplementedError — en CI se llevo puesta la sesion entera de pytest con
# un INTERNALERROR. En Windows es inofensivo, asi que no se nota en local.
#
# Por eso cada fallback se verifica en su propia plataforma, sin simular
# nada: entre los dos tests el ramal queda cubierto en cualquier runner.


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="el fallback a notepad es de Windows")
async def test_macro_edit_defaults_to_notepad_on_windows(
    macros_path, monkeypatch
):
    """Without EDITOR, Windows uses notepad as the required fallback."""
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text("/status\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("subprocess.run", fake_run)

    await MacroCommand(_Session()).execute("edit deploy")

    assert calls and calls[0][0] == "notepad"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="el fallback a vi es de POSIX")
async def test_macro_edit_defaults_to_vi_on_posix(macros_path, monkeypatch):
    """Sin EDITOR, fuera de Windows el fallback obligatorio es vi."""
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_text("/status\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("subprocess.run", fake_run)

    await MacroCommand(_Session()).execute("edit deploy")

    assert calls and calls[0][0] == "vi"
