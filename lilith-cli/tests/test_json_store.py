"""Unreadable JSON stores are copied aside before a save can overwrite them."""

from __future__ import annotations

import pytest

from lilith_cli import commands, json_store
from lilith_cli.slash_commands import settings


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(json_store, "_preserved", set())


def test_preserve_corrupt_copies_the_file_once(tmp_path, capsys):
    store = tmp_path / "macros.json"
    store.write_text("{broken", encoding="utf-8")

    backup = json_store.preserve_corrupt(store, ValueError("bad"))
    assert backup is not None and backup.read_text(encoding="utf-8") == "{broken"
    assert "macros.json no se pudo leer" in capsys.readouterr().out
    assert json_store.preserve_corrupt(store, ValueError("bad")) is None


def test_preserve_corrupt_ignores_missing_files(tmp_path):
    assert json_store.preserve_corrupt(tmp_path / "missing.json", ValueError()) is None


def test_corrupt_macros_survive_the_next_save(tmp_path, monkeypatch):
    """Regression: a file that did not parse loaded as {} and was overwritten."""
    store = tmp_path / "macros.json"
    store.write_text('{"deploy": ["/git status"],}', encoding="utf-8")
    monkeypatch.setattr(commands, "_MACROS_PATH", store)

    assert commands._load_macros() == {}
    commands._save_macros({"new": ["/pwd"]})

    backups = list(tmp_path.glob("macros.json.corrupt-*"))
    assert len(backups) == 1
    assert '"deploy"' in backups[0].read_text(encoding="utf-8")


def test_wrong_json_shape_is_preserved_too(tmp_path, monkeypatch):
    store = tmp_path / "aliases.json"
    store.write_text('["gs", "/git status"]', encoding="utf-8")
    monkeypatch.setattr(settings, "_ALIAS_FILE", store)

    assert settings._load_aliases() == {}
    assert list(tmp_path.glob("aliases.json.corrupt-*"))
