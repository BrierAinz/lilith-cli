"""Tests enfocados para el comando de barra /random."""

from __future__ import annotations

import asyncio
import inspect
import uuid


def _run(coro):
    return asyncio.run(coro)


def test_random_int_signed_inclusive(fake_session, capsys, monkeypatch):
    from lilith_cli.extra_commands import run_random_command

    monkeypatch.setattr("secrets.randbelow", lambda upper: upper - 1)
    _run(run_random_command(fake_session, "int -5 7"))
    assert "7" in capsys.readouterr().out


def test_random_choice_parses_quoted_items(fake_session, capsys, monkeypatch):
    from lilith_cli.extra_commands import run_random_command

    def choose(items):
        assert items == ["rojo claro", "azul oscuro"]
        return items[1]

    monkeypatch.setattr("secrets.choice", choose)
    _run(run_random_command(fake_session, 'choice "rojo claro" "azul oscuro"'))
    assert "azul oscuro" in capsys.readouterr().out


def test_random_hex_default_and_validation(fake_session, capsys, monkeypatch):
    from lilith_cli.extra_commands import run_random_command

    calls = []
    monkeypatch.setattr("secrets.token_hex", lambda count: calls.append(count) or "abc123")
    _run(run_random_command(fake_session, "hex"))
    assert calls == [16]
    assert "abc123" in capsys.readouterr().out

    _run(run_random_command(fake_session, "hex 0"))
    _run(run_random_command(fake_session, "hex nope"))
    output = capsys.readouterr().out
    assert "entre 1 y 1024" in output
    assert calls == [16]


def test_random_uuid_and_coin(fake_session, capsys, monkeypatch):
    from lilith_cli.extra_commands import run_random_command

    expected = uuid.UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr("uuid.uuid4", lambda: expected)
    monkeypatch.setattr("secrets.choice", lambda items: "cruz")
    _run(run_random_command(fake_session, "uuid"))
    _run(run_random_command(fake_session, "coin"))
    output = capsys.readouterr().out
    assert str(expected) in output
    assert "cruz" in output


def test_random_dice_rolls_and_total(fake_session, capsys, monkeypatch):
    from lilith_cli.extra_commands import run_random_command

    values = iter([0, 5, 2])
    monkeypatch.setattr("secrets.randbelow", lambda sides: next(values))
    _run(run_random_command(fake_session, "dice 3d6"))
    output = capsys.readouterr().out
    assert "1, 6, 3" in output
    assert "Total:" in output
    assert "10" in output


def test_random_invalid_inputs_and_help(fake_session, capsys):
    from lilith_cli.extra_commands import run_random_command

    for args in ("int x 2", "int 5 2", "choice solo", "dice 0d6", "dice 2x6"):
        _run(run_random_command(fake_session, args))
    _run(run_random_command(fake_session, "help"))
    output = capsys.readouterr().out
    assert "deben ser enteros" in output
    assert "mínimo no puede ser mayor" in output
    assert "mínimo 2" in output
    assert "Dados fuera de rango" in output
    assert "Notación de dados inválida" in output
    for mode in ("int", "choice", "hex", "uuid", "coin", "dice"):
        assert mode in output


def test_random_is_wired_in_repl():
    import lilith_cli.repl as repl_module

    assert "/random" in repl_module._SLASH_COMMANDS
    source = inspect.getsource(repl_module.run_repl)
    assert 'cmd_name == "random"' in source
    assert "run_random_command(session, cmd_args)" in source