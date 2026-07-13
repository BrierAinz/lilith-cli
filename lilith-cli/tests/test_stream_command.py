"""Tests for the /stream slash command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_stream_config(tmp_path: Path, monkeypatch):
    """Redirect the stream config file to a tmp_path so tests do not touch real config."""
    from lilith_cli import extra_commands as ec

    fake_file = tmp_path / "stream_config.json"
    monkeypatch.setattr(ec, "_STREAM_CONFIG_FILE", fake_file)
    return fake_file


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_stream_default_defaults_to_enabled(fake_session, isolated_stream_config):
    """/stream (no args) on a missing config file must report streaming enabled by default."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_stream_command

        await run_stream_command(fake_session, "")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Streaming" in combined
    assert "activado" in combined


@pytest.mark.asyncio
async def test_stream_status_alias(fake_session, isolated_stream_config):
    """/stream status and /stream show must also display the current mode."""
    isolated_stream_config.write_text(
        json.dumps({"enabled": False}), encoding="utf-8"
    )

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_stream_command

        await run_stream_command(fake_session, "status")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "desactivado" in combined


@pytest.mark.asyncio
async def test_stream_on_persists_true(fake_session, isolated_stream_config):
    """/stream on (and variants true/1) must persist enabled=True."""
    for variant in ("on", "true", "1"):
        isolated_stream_config.unlink(missing_ok=True)

        with patch("lilith_cli.extra_commands.console.print"):
            from lilith_cli.extra_commands import run_stream_command

            await run_stream_command(fake_session, variant)

        assert _load(isolated_stream_config).get("enabled") is True


@pytest.mark.asyncio
async def test_stream_off_persists_false(fake_session, isolated_stream_config):
    """/stream off (and variants false/0) must persist enabled=False."""
    for variant in ("off", "false", "0"):
        isolated_stream_config.unlink(missing_ok=True)

        with patch("lilith_cli.extra_commands.console.print"):
            from lilith_cli.extra_commands import run_stream_command

            await run_stream_command(fake_session, variant)

        assert _load(isolated_stream_config).get("enabled") is False


@pytest.mark.asyncio
async def test_stream_unknown_arg_reports_usage_error(fake_session, isolated_stream_config):
    """/stream foo (unknown arg) must render a usage error and not modify the config."""
    isolated_stream_config.write_text(
        json.dumps({"enabled": True}), encoding="utf-8"
    )

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_stream_command

        await run_stream_command(fake_session, "frobnicate")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Uso:" in combined
    # Configuration must be untouched.
    assert _load(isolated_stream_config) == {"enabled": True}
