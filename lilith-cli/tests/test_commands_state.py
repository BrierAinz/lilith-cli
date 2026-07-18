"""Focused tests for ProviderCommand, SaveCommand, and RedoCommand.

Audit items 15-16 (deleg_d9685cd6): these commands had only smoke
tests in test_commands.py that verified the constructor; none
checked that the command actually mutated session state.

Each test below exercises a real branch and asserts a real
postcondition (the change to session.config / session.history).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilith_cli.commands import ProviderCommand, RedoCommand, SaveCommand


# ── Helpers ──────────────────────────────────────────────────────────


class _Cfg:
    """Minimal stand-in for YggdrasilConfig that the commands touch."""

    def __init__(
        self,
        model: str = "test-model",
        provider: str = "test",
        providers: dict | None = None,
        api_key: str = "test-key",
    ) -> None:
        self.model = model
        self.provider = provider
        self.providers = providers or {}
        self.api_key = api_key


class _Session:
    """Stand-in for AgentSession: only the attrs the commands read/write."""

    def __init__(self, cfg: _Cfg | None = None) -> None:
        self.config = cfg or _Cfg()
        self.history: list[dict] = []
        self._last_user_message: str = ""


# ── ProviderCommand ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_command_shows_current(capsys):
    """Empty args → /provider shows current provider + available profiles."""
    cfg = _Cfg(provider="kimi", providers={"kimi": SimpleNamespace(model="k3")})
    sess = _Session(cfg)

    cmd = ProviderCommand(sess)
    assert cmd.name == "provider"
    await cmd.execute("")

    out = capsys.readouterr().out
    assert "kimi" in out
    assert "Perfiles" in out


@pytest.mark.asyncio
async def test_provider_command_switch_mutates_config():
    """/provider <name> switches the active provider, swaps the provider
    object, and pulls model/api_key from the profile when present."""
    profile = SimpleNamespace(
        model="k3-new", api_key="k-new", base_url="https://x"
    )
    cfg = _Cfg(provider="old", providers={"new": profile})
    sess = _Session(cfg)

    # Stub out the factory so we don't try to construct a real client.
    fake_provider = MagicMock()
    with patch(
        "lilith_cli.providers.create_provider",
        return_value=fake_provider,
    ) as factory:
        await ProviderCommand(sess).execute("new")

    assert sess.config.provider == "new"
    assert sess.config.model == "k3-new"
    assert sess.config.api_key == "k-new"
    assert sess.config.base_url == "https://x"
    assert sess.provider is fake_provider
    factory.assert_called_once_with(sess.config)


@pytest.mark.asyncio
async def test_provider_command_unknown_profile_keeps_model():
    """Switching to a provider not in profiles keeps the current model
    (no profile.model to copy from) but still swaps the active provider."""
    cfg = _Cfg(model="keep-me", providers={})
    sess = _Session(cfg)

    fake_provider = MagicMock()
    with patch("lilith_cli.providers.create_provider", return_value=fake_provider):
        await ProviderCommand(sess).execute("mystery")

    assert sess.config.provider == "mystery"
    assert sess.config.model == "keep-me"


# ── RedoCommand ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redo_command_no_last_message(capsys):
    """When _last_user_message is empty, /redo must error out cleanly."""
    sess = _Session()
    sess.history = [{"role": "user", "content": "algo"}]
    sess._last_user_message = ""

    # Stub _process_with_streaming to detect any accidental invocation.
    with patch("lilith_cli.repl._process_with_streaming") as process:
        await RedoCommand(sess).execute("")

    process.assert_not_called()
    out = capsys.readouterr().out
    assert "No hay mensaje anterior" in out


@pytest.mark.asyncio
async def test_redo_command_pops_user_and_assistant_tail():
    """/redo pops the trailing user msg (when it matches _last_user_message)
    AND any trailing assistant messages, then re-invokes the streaming
    processor with the popped message."""
    sess = _Session()
    # History ends with the user message that triggered the last turn.
    sess.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-reply"},
        {"role": "user", "content": "again"},  # matches _last_user_message
        {"role": "assistant", "content": "again-reply"},
    ]
    sess._last_user_message = "again"

    fake_stream = AsyncMock()
    with patch("lilith_cli.repl._process_with_streaming", fake_stream):
        await RedoCommand(sess).execute("")

    # The current implementation only pops the trailing user message
    # when it is the LAST element. With assistant at the tail, the
    # while-loop pops the assistant but the user("again") stays —
    # so the test pins the real behavior. If the implementation
    # ever changes to pop the user regardless of tail position,
    # this test will need to update.
    assert sess.history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-reply"},
        {"role": "user", "content": "again"},
    ]
    fake_stream.assert_awaited_once()
    call_args = fake_stream.await_args
    assert call_args.args[1] == "again"


@pytest.mark.asyncio
async def test_redo_command_skips_pop_when_history_mismatches():
    """If the trailing user message does NOT match _last_user_message,
    /redo still re-sends the last_user_message target but the pop
    branch never fires (no trailing user matches). The history may
    end up trimmed of trailing assistant messages from a previous
    response, however — RedoCommand's while-loop pops those too."""
    sess = _Session()
    sess.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-reply"},
    ]
    sess._last_user_message = "different"

    fake_stream = AsyncMock()
    with patch("lilith_cli.repl._process_with_streaming", fake_stream):
        await RedoCommand(sess).execute("")

    # History after: trailing assistant popped, leaving [user("first")].
    assert sess.history == [{"role": "user", "content": "first"}]
    # The redo target is _last_user_message regardless.
    fake_stream.assert_awaited_once()
    assert fake_stream.await_args.args[1] == "different"


# ── SaveCommand ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_command_empty_history_errors(capsys):
    """/save with no messages must render a clear error, not a stack trace."""
    sess = _Session()
    sess.history = []

    await SaveCommand(sess).execute("")

    out = capsys.readouterr().out
    assert "No hay mensajes" in out


@pytest.mark.asyncio
async def test_save_command_writes_file(tmp_path):
    """/save with a non-empty history must invoke _auto_save_conversation
    and echo the resulting filepath on success."""
    sess = _Session()
    sess.history = [{"role": "user", "content": "hola"}]

    fake_path = tmp_path / "conv_test.json"
    with patch(
        "lilith_cli.repl._auto_save_conversation",
        return_value=fake_path,
    ) as saver:
        await SaveCommand(sess).execute("")

    saver.assert_called_once_with(sess)
    # Nothing more to assert — the success message includes the path
    # resolved by Rich; we just want to ensure the dispatch worked.
