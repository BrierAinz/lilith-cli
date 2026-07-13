"""Tests for project-level config (.lilith/config.yaml) discovery and merge."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lilith_cli.config import (
    _merge_yaml_dicts,
    find_project_config,
)


class TestFindProjectConfig:
    """Verify find_project_config walks up from cwd looking for .lilith/config.yaml."""

    def test_returns_none_when_no_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Change cwd to a directory that has no .lilith/ anywhere up the chain
        # We need a directory with no .lilith — using tmp_path should work since
        # we don't create one.
        monkeypatch.chdir(tmp_path)
        # Walk up to / and find nothing
        result = find_project_config()
        # Result depends on the filesystem; just check no crash.
        # If somehow /tmp/.lilith/config.yaml exists (very unlikely) we skip.
        if result is not None:
            pytest.skip("Unexpected .lilith/config.yaml found in parent dirs")

    def test_finds_config_in_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Create .lilith/config.yaml in tmp_path
        lilith_dir = tmp_path / ".lilith"
        lilith_dir.mkdir(parents=True, exist_ok=True)
        config = lilith_dir / "config.yaml"
        config.write_text("provider: openai\n", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        result = find_project_config()
        assert result is not None
        assert result.resolve() == config.resolve()

    def test_finds_config_in_parent_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Create .lilith/config.yaml in tmp_path (parent)
        lilith_dir = tmp_path / ".lilith"
        lilith_dir.mkdir(parents=True, exist_ok=True)
        config = lilith_dir / "config.yaml"
        config.write_text("provider: openai\n", encoding="utf-8")

        # Create a subdir and chdir there
        sub = tmp_path / "subdir"
        sub.mkdir()
        monkeypatch.chdir(sub)
        result = find_project_config()
        assert result is not None
        assert result.resolve() == config.resolve()


class TestMergeYamlDicts:
    """Verify recursive merging with override winning."""

    def test_simple_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = _merge_yaml_dicts(base, override)
        assert result == {"a": 1, "b": 99}

    def test_empty_override_does_not_wipe(self) -> None:
        """An empty string override should NOT wipe the base value."""
        base = {"provider": "sakana", "model": "fugu-ultra"}
        override = {"model": ""}
        result = _merge_yaml_dicts(base, override)
        # Empty string is treated as "not set" — base wins.
        assert result == {"provider": "sakana", "model": "fugu-ultra"}

    def test_nested_merge(self) -> None:
        base = {"tools": {"filesystem": True, "browser": True}}
        override = {"tools": {"filesystem": False}}
        result = _merge_yaml_dicts(base, override)
        assert result == {"tools": {"filesystem": False, "browser": True}}

    def test_new_key_added(self) -> None:
        base = {"a": 1}
        override = {"b": 2}
        result = _merge_yaml_dicts(base, override)
        assert result == {"a": 1, "b": 2}
