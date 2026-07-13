"""Tests for lilith_core.config — central configuration management."""

import json
from pathlib import Path

import pytest

from lilith_core.config import Config


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary directory for Config tests."""
    return tmp_path / "lilith_test_config"


@pytest.fixture
def config(config_dir: Path) -> Config:
    """Provide a Config instance backed by a temporary directory."""
    return Config(root_path=config_dir)


class TestDefaults:
    """Tests for Config default values."""

    def test_model_default_is_auto(self, config: Config) -> None:
        assert config.get("model") == "auto"

    def test_lm_studio_url_default(self, config: Config) -> None:
        assert config.get("lm_studio_url") == "http://localhost:1234/v1"

    def test_max_context_default(self, config: Config) -> None:
        assert config.get("max_context") == 8192

    def test_temperature_default(self, config: Config) -> None:
        assert config.get("temperature") == 0.7

    def test_unknown_key_returns_none(self, config: Config) -> None:
        assert config.get("nonexistent") is None

    def test_unknown_key_with_fallback(self, config: Config) -> None:
        assert config.get("nonexistent", "fallback") == "fallback"

    def test_defaults_method_returns_four_keys(self, config: Config) -> None:
        defaults = config._defaults()
        assert len(defaults) == 4
        assert "model" in defaults
        assert "lm_studio_url" in defaults
        assert "max_context" in defaults
        assert "temperature" in defaults


class TestPersistence:
    """Tests for Config persistence across instances."""

    def test_set_and_get(self, config: Config) -> None:
        config.set("test_key", "test_value")
        assert config.get("test_key") == "test_value"

    def test_set_overwrites_default(self, config: Config) -> None:
        config.set("model", "gpt-4")
        assert config.get("model") == "gpt-4"

    def test_set_creates_config_file(self, config: Config, config_dir: Path) -> None:
        config.set("new_key", "new_val")
        assert (config_dir / "config.json").exists()

    def test_config_file_is_valid_json(self, config: Config, config_dir: Path) -> None:
        config.set("k", "v")
        data = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert data["k"] == "v"

    def test_config_file_uses_indent_2(self, config: Config, config_dir: Path) -> None:
        config.set("k", "v")
        content = (config_dir / "config.json").read_text(encoding="utf-8")
        assert "\n  " in content

    def test_persistence_across_instances(self, config_dir: Path) -> None:
        c1 = Config(root_path=config_dir)
        c1.set("persistent_key", "persistent_value")
        c2 = Config(root_path=config_dir)
        assert c2.get("persistent_key") == "persistent_value"

    def test_set_preserves_existing_keys(self, config: Config) -> None:
        config.set("key_a", "val_a")
        config.set("key_b", "val_b")
        assert config.get("key_a") == "val_a"
        assert config.get("key_b") == "val_b"

    def test_multiple_set_same_key(self, config: Config) -> None:
        config.set("counter", 1)
        config.set("counter", 2)
        assert config.get("counter") == 2

    def test_set_various_types(self, config: Config) -> None:
        config.set("int_val", 42)
        config.set("float_val", 3.14)
        config.set("bool_val", True)
        config.set("list_val", [1, 2, 3])
        assert config.get("int_val") == 42
        assert config.get("float_val") == 3.14
        assert config.get("bool_val") is True
        assert config.get("list_val") == [1, 2, 3]


class TestInit:
    """Tests for Config initialization."""

    def test_creates_directory_on_init(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "nested" / "config"
        Config(root_path=new_dir)
        assert new_dir.exists()

    def test_default_root_path(self) -> None:
        config = Config()
        assert config.root == Path.home() / ".lilith"

    def test_custom_root_path(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom_config"
        config = Config(root_path=custom)
        assert config.root == custom

    def test_config_file_path(self, config_dir: Path) -> None:
        config = Config(root_path=config_dir)
        assert config.config_file == config_dir / "config.json"

    def test_load_from_existing_file(self, config_dir: Path) -> None:
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(
            json.dumps({"preloaded_key": "preloaded_value"}),
            encoding="utf-8",
        )
        config = Config(root_path=config_dir)
        assert config.get("preloaded_key") == "preloaded_value"


class TestEdgeCases:
    """Tests for Config edge cases."""

    def test_unicode_values(self, config: Config) -> None:
        config.set("rune", "\u16a0\u16a2\u16a6\u16a8\u16b1\u16b2")
        assert config.get("rune") == "\u16a0\u16a2\u16a6\u16a8\u16b1\u16b2"

    def test_empty_string_value(self, config: Config) -> None:
        config.set("empty", "")
        assert config.get("empty") == ""

    def test_none_value_explicit(self, config: Config) -> None:
        config.set("null_val", None)
        assert config.get("null_val") is None

    def test_nested_dict_value(self, config: Config) -> None:
        config.set("nested", {"a": {"b": 1}})
        assert config.get("nested") == {"a": {"b": 1}}

    def test_ensure_ascii_false_preserves_unicode(self, config: Config, config_dir: Path) -> None:
        config.set("name", "\u00d1oldor")
        content = (config_dir / "config.json").read_text(encoding="utf-8")
        assert "\u00d1oldor" in content
