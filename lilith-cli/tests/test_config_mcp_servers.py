"""Tests for ``mcp_servers`` parsing in :class:`YggdrasilConfig`.

Verifies:
* ``mcp_servers`` defaults to ``None`` and ``effective_mcp_servers``
  returns an empty dict in that case.
* Loading a YAML config with ``mcp_servers:`` set to ``null`` or an
  empty mapping round-trips through Pydantic without raising.
* A populated ``mcp_servers`` mapping is parsed into ``MCPServerConfig``
  instances and exposed via ``effective_mcp_servers``.
* The default-config YAML embedded in the loader also parses cleanly.
"""

from __future__ import annotations

import textwrap

import pytest


# ── Default state ─────────────────────────────────────────────────────


def test_default_mcp_servers_is_none():
    """No ``mcp_servers:`` in YAML → field defaults to None, property
    coerces to empty dict so consumers can iterate unconditionally."""
    from lilith_cli.config import YggdrasilConfig

    cfg = YggdrasilConfig()
    assert cfg.mcp_servers is None
    assert cfg.effective_mcp_servers == {}


# ── YAML round-trip ───────────────────────────────────────────────────


def test_yaml_with_explicit_null_mcp_servers(tmp_path):
    """Writing ``mcp_servers: null`` in YAML must parse without raising.

    This guards the field-validator behaviour: an explicit ``None``
    (rather than omission) is the common user mistake when copy-pasting
    from docs."""
    from lilith_cli.config import load_config

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """\
            provider: local
            model: local-model
            mcp_servers: null
            """
        )
    )
    cfg = load_config(yaml_path)
    assert cfg.mcp_servers is None
    assert cfg.effective_mcp_servers == {}


def test_yaml_with_empty_mcp_servers(tmp_path):
    """An empty ``mcp_servers: {}`` should parse into an empty dict
    (not None) and the property should still return it as-is."""
    from lilith_cli.config import load_config

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """\
            provider: local
            model: local-model
            mcp_servers: {}
            """
        )
    )
    cfg = load_config(yaml_path)
    assert cfg.mcp_servers == {}
    assert cfg.effective_mcp_servers == {}


def test_yaml_with_populated_mcp_servers(tmp_path):
    """A real ``mcp_servers:`` block parses into MCPServerConfig objects."""
    from lilith_cli.config import MCPServerConfig, load_config

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """\
            provider: local
            model: local-model
            mcp_servers:
              fake:
                command: python
                args: ["-m", "lilith_tools.fake_mcp_server"]
                enabled: true
                timeout: 5
              disabled_one:
                command: /bin/true
                enabled: false
            """
        )
    )
    cfg = load_config(yaml_path)
    effective = cfg.effective_mcp_servers
    assert set(effective) == {"fake", "disabled_one"}
    assert isinstance(effective["fake"], MCPServerConfig)
    assert effective["fake"].command == "python"
    assert effective["fake"].args == ["-m", "lilith_tools.fake_mcp_server"]
    assert effective["fake"].enabled is True
    assert effective["fake"].timeout == 5
    assert effective["disabled_one"].enabled is False


# ── Default config block ──────────────────────────────────────────────


def test_default_config_yaml_parses_cleanly(tmp_path, monkeypatch):
    """Writing the embedded default YAML to a temp file and loading it
    via :func:`load_config` must succeed — guards against accidental
    schema regressions in the config model."""
    from lilith_cli.config import _DEFAULT_CONFIG_YAML, load_config

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")
    cfg = load_config(yaml_path)
    assert cfg.mcp_servers is None
    assert cfg.effective_mcp_servers == {}


# ── Field-level behaviour ─────────────────────────────────────────────


def test_mcp_server_config_defaults():
    from lilith_cli.config import MCPServerConfig

    srv = MCPServerConfig(command="python")
    assert srv.args == []
    assert srv.enabled is True
    assert srv.timeout == 30.0
    assert srv.env is None