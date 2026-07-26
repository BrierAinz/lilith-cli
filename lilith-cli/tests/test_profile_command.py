"""Tests for the /profile slash command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli import extra_commands
from lilith_cli.extra_commands import (
    _DEFAULT_PROFILES,
    _ensure_profiles,
    _load_profiles,
    _profiles_path,
    _save_profiles,
    run_profile_command,
)


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""
        self.temperature = 0.7
        self.max_tokens = 4096


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.fixture
def profiles_file(tmp_path, monkeypatch):
    """Redirect profile storage to a temporary directory."""
    original = extra_commands._PROFILES_PATH
    path = tmp_path / "profiles.json"
    extra_commands._PROFILES_PATH = path
    yield path
    extra_commands._PROFILES_PATH = original


@pytest.mark.asyncio
async def test_profile_list_pre_populated(profiles_file):
    """/profile list muestra los perfiles pre-poblados."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_profile_command(session, "list")

    output = "".join(str(p) for p in prints)
    assert "fast" in output
    assert "reasoning" in output
    assert "local" in output


@pytest.mark.asyncio
async def test_profile_save_load_and_show(profiles_file):
    """/profile save, load y show persisten y aplican perfiles."""
    session = DummySession()
    session.config.model = "custom-model"
    session.config.provider = "custom-provider"
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_profile_command(session, "save custom")
        # Reset and load
        session.config.model = "other"
        session.config.provider = "other"
        await run_profile_command(session, "load custom")
        await run_profile_command(session, "show custom")

    output = "".join(str(p) for p in prints)
    assert "Perfil guardado: custom" in output
    assert "Perfil cargado: custom" in output
    assert "custom-model" in output
    assert "custom-provider" in output
    assert session.config.model == "custom-model"
    assert session.config.provider == "custom-provider"


@pytest.mark.asyncio
async def test_profile_delete(profiles_file):
    """/profile delete elimina un perfil guardado."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_profile_command(session, "save temp")
        await run_profile_command(session, "delete temp")
        await run_profile_command(session, "list")

    output = "".join(str(p) for p in prints)
    assert "Perfil eliminado: temp" in output
    assert "temp" not in _load_profiles()


def test_profiles_path_uses_config_dir(tmp_path, monkeypatch):
    """La ruta de perfiles se basa en el directorio de configuración."""
    monkeypatch.setenv("HOME", str(tmp_path))
    extra_commands._PROFILES_PATH = None
    path = _profiles_path()
    assert path == tmp_path / ".yggdrasil" / "profiles.json"


def test_default_profiles_structure():
    """Los perfiles por defecto tienen los modelos esperados."""
    assert _DEFAULT_PROFILES["fast"]["model"] == "deepseek-v4-flash"
    assert _DEFAULT_PROFILES["reasoning"]["model"] == "claude-opus-4"
    assert _DEFAULT_PROFILES["local"]["model"] == "local-model"


@pytest.mark.asyncio
async def test_profile_save_persiste_tema_sin_secretos(profiles_file):
    """/profile save guarda el tema activo, pero nunca credenciales ni prompts."""
    session = DummySession()
    session.config.provider = "proveedor-seguro"
    session.config.model = "modelo-seguro"
    session.config.api_key = "secreto-que-no-debe-guardarse"
    session.config.system_prompt = "prompt privado"
    previous_theme = extra_commands.get_theme().name

    try:
        extra_commands.set_theme("cyberpunk")
        await run_profile_command(session, "save seguro")
    finally:
        extra_commands.set_theme(previous_theme)

    saved = _load_profiles()["seguro"]
    assert saved["provider"] == "proveedor-seguro"
    assert saved["model"] == "modelo-seguro"
    assert saved["theme"] == "cyberpunk"
    assert "api_key" not in saved
    assert "system_prompt" not in saved


@pytest.mark.asyncio
async def test_profile_list_y_load_incluyen_tema(profiles_file):
    """/profile lista los tres campos y load aplica también el tema guardado."""
    _save_profiles(
        {
            "minimalista": {
                "provider": "local",
                "model": "modelo-local",
                "theme": "minimal",
            }
        }
    )
    session = DummySession()
    prints = []
    previous_theme = extra_commands.get_theme().name

    def capture(text: str = ""):
        prints.append(text)

    try:
        extra_commands.set_theme("norse")
        with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
            await run_profile_command(session, "list")
            await run_profile_command(session, "load minimalista")

        output = "".join(str(item) for item in prints)
        assert "provider=local" in output
        assert "model=modelo-local" in output
        assert "theme=minimal" in output
        assert session.config.provider == "local"
        assert session.config.model == "modelo-local"
        assert extra_commands.get_theme().name == "minimal"
    finally:
        extra_commands.set_theme(previous_theme)
