"""Pruebas del diagnóstico de servidores LSP en /doctor --deep."""

from __future__ import annotations

from unittest.mock import patch

from lilith_cli.extra_commands import _run_deep_checks


def _lsp_result(results: list[dict]) -> dict:
    return next(row for row in results if row["check"] == "Language servers")


def test_deep_check_reports_available_and_missing_lsp_servers(fake_session):
    """El diagnóstico resume servidores disponibles y faltantes sin instalarlos."""
    commands = {
        "python": ["pyright-langserver", "--stdio"],
        "rust": None,
        "go": ["gopls"],
    }

    with patch.dict(
        "lilith_cli.ide.lsp.languages.PREFERRED_SERVERS",
        {language: [language] for language in commands},
        clear=True,
    ), patch(
        "lilith_cli.ide.lsp.languages.language_server_command",
        side_effect=lambda language: commands[language],
    ):
        row = _lsp_result(_run_deep_checks(fake_session))

    assert row["status"] == "ok"
    assert "python (pyright-langserver)" in row["message"]
    assert "go (gopls)" in row["message"]
    assert "faltan: rust" in row["message"]


def test_deep_check_explains_how_to_add_lsp_when_none_are_found(fake_session):
    """Sin servidores, /doctor --deep ofrece una recomendación accionable."""
    with patch.dict(
        "lilith_cli.ide.lsp.languages.PREFERRED_SERVERS",
        {"python": ["pyright-langserver", "--stdio"]},
        clear=True,
    ), patch(
        "lilith_cli.ide.lsp.languages.language_server_command",
        return_value=None,
    ):
        row = _lsp_result(_run_deep_checks(fake_session))

    assert row["status"] == "warn"
    assert "No se detectó ningún servidor" in row["message"]
    assert "pyright" in row["message"]
