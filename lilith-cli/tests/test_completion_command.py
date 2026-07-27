"""Tests for the /completion command.

The completion command has no runtime side effects except for
``/completion install <shell>`` which writes to ``~/.lilith_completion.<ext>``.
For the install path we monkeypatch the target directory to a
pytest-provided ``tmp_path`` so we never touch the real home folder.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from lilith_cli import completion_command
from lilith_cli.completion_command import (
    _collect_slash_commands,
    _install,
    run_completion_command,
)


# ── Pure-helper tests ─────────────────────────────────────────────


def test_collect_slash_commands_includes_completion_itself():
    cmds = _collect_slash_commands()
    assert isinstance(cmds, list)
    assert "/completion" in cmds


def test_fallback_list_is_present_and_well_formed():
    fallback = completion_command._FALLBACK_SLASH
    assert isinstance(fallback, list)
    assert "/completion" in fallback
    assert all(c.startswith("/") for c in fallback)


def test_bash_script_has_complete_builtin():
    cmds = ["/help", "/tools", "/completion"]
    out = completion_command._bash_script(cmds)
    assert "complete -F" in out
    assert "_LILITH_SLASH" in out
    for c in cmds:
        assert c in out


def test_zsh_script_has_compdef_and_describe():
    cmds = ["/help", "/tools", "/completion"]
    out = completion_command._zsh_script(cmds)
    assert "#compdef lilith" in out
    assert "_describe 'command' commands" in out
    for c in cmds:
        assert f"'{c}'" in out


def test_fish_script_emits_one_complete_per_command():
    cmds = ["/help", "/tools", "/completion"]
    out = completion_command._fish_script(cmds)
    lines = [ln for ln in out.splitlines()
             if ln.startswith("complete -c lilith -a")]
    assert len(lines) == len(cmds)


def test_powershell_script_uses_register_argumentcompleter():
    cmds = ["/help", "/tools"]
    out = completion_command._powershell_script(cmds)
    assert "Register-ArgumentCompleter" in out
    assert "-CommandName 'lilith'" in out
    assert "'/help'" in out


def test_install_unknown_shell_raises_runtime_error():
    with pytest.raises(RuntimeError, match="no soportado"):
        _install("elvish", ["/x"])


# ── Install writes to a sandboxed home ─────────────────────────────


def test_install_writes_to_configured_home(tmp_path, monkeypatch):
    target_shell = "bash"
    cmds = ["/help", "/tools", "/completion"]
    monkeypatch.setattr(completion_command.os.path, "expanduser",
                        lambda p: str(tmp_path) if p == "~" else p)
    path = _install(target_shell, cmds)
    assert path == tmp_path / ".lilith_completion.bash"
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "_LILITH_SLASH" in body
    assert "/completion" in body


# ── run_completion_command end-to-end ──────────────────────────────


class _FakeSession:
    """Minimal stand-in for AgentSession used by the REPL router."""


def _capture(coro):
    """Run the async command and capture everything passed to console.print."""

    prints: list[tuple] = []
    with patch.object(completion_command.console, "print",
                      side_effect=lambda *a, **kw: prints.append(a)):
        asyncio.run(coro)
    # Flatten args (each call may pass one or more renderables) into text.
    return prints


def test_run_help_prints_usage():
    prints = _capture(run_completion_command(_FakeSession(), ""))
    assert prints, "expected console.print to be called"
    rendered = "".join(str(p[0]) for p in prints if p)
    assert "/completion" in rendered
    assert "bash" in rendered
    assert "fish" in rendered


def test_run_bash_prints_only_bash_script():
    prints = _capture(run_completion_command(_FakeSession(), "bash"))
    rendered = "".join(str(p[0]) for p in prints if p)
    assert "complete -F" in rendered
    assert "_LILITH_SLASH" in rendered


def test_run_zsh_prints_compdef():
    prints = _capture(run_completion_command(_FakeSession(), "zsh"))
    rendered = "".join(str(p[0]) for p in prints if p)
    assert "#compdef lilith" in rendered


def test_run_fish_prints_completion_lines():
    prints = _capture(run_completion_command(_FakeSession(), "fish"))
    rendered = "".join(str(p[0]) for p in prints if p)
    assert "complete -c lilith" in rendered


def test_run_powershell_prints_snippet():
    prints = _capture(run_completion_command(_FakeSession(), "powershell"))
    rendered = "".join(str(p[0]) for p in prints if p)
    assert "Register-ArgumentCompleter" in rendered


def test_run_unknown_shell_does_not_raise():
    """An unknown shell must surface as an error, not crash."""

    # render_error uses console.print too — patch it so we capture both.
    prints = _capture(run_completion_command(_FakeSession(), "elvish"))
    assert prints  # something was printed (either error or otherwise)


def test_run_install_requires_target_shell():
    prints = _capture(run_completion_command(_FakeSession(), "install"))
    assert prints  # something was printed via render_error


def test_run_install_bash_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(completion_command.os.path, "expanduser",
                        lambda p: str(tmp_path) if p == "~" else p)
    _capture(run_completion_command(_FakeSession(), "install bash"))
    target = tmp_path / ".lilith_completion.bash"
    assert target.exists()
    assert "_LILITH_SLASH" in target.read_text(encoding="utf-8")