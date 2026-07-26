"""Tests for the /quote slash command."""

from __future__ import annotations

import pytest

from lilith_cli.extra_commands import run_quote_command


@pytest.mark.asyncio
async def test_quote_empty_history_error(fake_session, capsys) -> None:
    await run_quote_command(fake_session, "")
    assert "No hay conversación todavía — `/quote` necesita al menos un mensaje." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_quote_default_last_assistant(fake_session, capsys) -> None:
    fake_session.history = [
        {"role": "user", "content": "Pregunta"},
        {"role": "assistant", "content": "Respuesta para citar"},
    ]
    await run_quote_command(fake_session, "")
    assert capsys.readouterr().out.strip() == "Respuesta para citar"


@pytest.mark.asyncio
async def test_quote_last(fake_session, capsys) -> None:
    fake_session.history = [
        {"role": "assistant", "content": "Anterior"},
        {"role": "assistant", "content": "Más reciente"},
    ]
    await run_quote_command(fake_session, "last")
    assert capsys.readouterr().out.strip() == "Más reciente"


@pytest.mark.asyncio
async def test_quote_one(fake_session, capsys) -> None:
    fake_session.history = [
        {"role": "user", "content": "Pregunta"},
        {"role": "assistant", "content": "Último mensaje"},
    ]
    await run_quote_command(fake_session, "1")
    assert capsys.readouterr().out.strip() == "Último mensaje"


@pytest.mark.asyncio
async def test_quote_two(fake_session, capsys) -> None:
    fake_session.history = [
        {"role": "user", "content": "Mensaje dos"},
        {"role": "assistant", "content": "Mensaje uno"},
    ]
    await run_quote_command(fake_session, "2")
    assert capsys.readouterr().out.strip() == "Mensaje dos"


@pytest.mark.asyncio
async def test_quote_user(fake_session, capsys) -> None:
    fake_session.history = [
        {"role": "user", "content": "Primera pregunta"},
        {"role": "assistant", "content": "Respuesta"},
        {"role": "user", "content": "Última pregunta"},
    ]
    await run_quote_command(fake_session, "user")
    assert capsys.readouterr().out.strip() == "Última pregunta"


@pytest.mark.asyncio
async def test_quote_out_of_range_error(fake_session, capsys) -> None:
    fake_session.history = [{"role": "assistant", "content": "Solo uno"}]
    await run_quote_command(fake_session, "2")
    assert "Índice fuera de rango: 2 (la sesión tiene 1 mensajes)." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_quote_invalid_argument_error(fake_session, capsys) -> None:
    await run_quote_command(fake_session, "desconocido")
    assert "Uso: /quote <last|user|N>" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_quote_help_output(fake_session, capsys) -> None:
    await run_quote_command(fake_session, "help")
    output = capsys.readouterr().out
    assert "Uso: /quote <last|user|N>" in output
    assert "/quote user" in output


@pytest.mark.asyncio
async def test_quote_empty_content_error(fake_session, capsys) -> None:
    fake_session.history = [
        {"role": "assistant", "content": None},
    ]
    await run_quote_command(fake_session, "1")
    assert "El mensaje #1 no tiene texto visible para citar." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_quote_multimodal_content_uses_only_text_blocks(fake_session, capsys) -> None:
    fake_session.history = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Texto visible"},
                {"type": "image_url", "image_url": {"url": "imagen omitida"}},
                {"type": "text", "text": "Segunda línea"},
            ],
        },
    ]
    await run_quote_command(fake_session, "1")
    assert capsys.readouterr().out.strip() == "Texto visible\nSegunda línea"
