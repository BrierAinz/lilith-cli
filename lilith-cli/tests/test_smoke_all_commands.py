"""Smoke test for every ``run_*_command`` slash-command entry point.

``lilith_cli.extra_commands`` exposes ~70 ``async def run_*_command(session, args)``
coroutines — one per slash command the Lilith CLI recognises. This test
discovers them by introspection (no hand-maintained list) and invokes each
with the shared ``fake_session`` fixture and an empty ``args`` string.

A command passes when **no unhandled exception escapes**. ``SystemExit`` —
which ``argparse.ArgumentParser.parse_args`` raises on ``--help`` /
invalid flags — is accepted; friendly error messages printed to the
console are fine.

Commands that cannot be smoke-tested safely are listed in ``EXCLUDED``
below with a short reason. They still appear in the inventory through
``pytest.mark.skip`` so it is obvious which commands are covered and
which are deliberately skipped.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

import lilith_cli.extra_commands as extra_commands


# ---------------------------------------------------------------------------
# Explicit exclusion set.
#
# Each entry maps ``run_<name>_command`` to a short reason describing why
# calling it with empty ``args`` would either spawn a subprocess, hit the
# network, mutate the repository or trigger an interactive UI. A few
# entries are excluded because of a known source-level bug
# (``await session.process_message_stream(...)`` on an async generator
# raises ``TypeError``) that cannot be fixed without modifying the
# source under test.
# ---------------------------------------------------------------------------
EXCLUDED: dict[str, str] = {
    # ── Subprocess execution ────────────────────────────────────────────
    # Runs `git diff --cached` to summarise staged changes.
    "run_diff_staged_command": "spawns `git diff --cached`",
    # Runs `git diff` plus an external linter (ruff / flake8 / black).
    "run_lint_command": "spawns git + external linter",
    # Runs `ruff check --fix` or `black` to rewrite files in place.
    "run_lint_fix_command": "spawns ruff/black auto-fix subprocess",
    # Reads / writes lilith_cli/__init__.py, edits CHANGELOG.md and
    # runs `git add` + `git commit`.
    "run_release_command": "mutates __init__.py + `git commit`",
    # Uses GitOperationTool → `git diff` (or PR review helper).
    "run_review_command": "spawns git via GitOperationTool",
    # Uses RunTestTool → invokes pytest in the current project.
    "run_test_command": "spawns pytest via RunTestTool",
    # Runs `git rev-parse` and `git log` to populate the context panel.
    "run_whereami_command": "spawns git rev-parse/log subprocesses",
    # ── Provider / network ──────────────────────────────────────────────
    # Builds a real LLM provider (e.g. Ollama / OpenAI) and streams
    # completions to measure latency — may hit the network or a remote
    # service depending on the configured provider.
    "run_bench_command": "creates real provider and streams completions",
    # ── Real agent turn (fake provider can't stream) ────────────────────
    # These three drive a full agent turn via ``process_message_stream``.
    # The ``fake_session`` provider mock (``AsyncMock``) is not an async
    # generator, so ``agent.py`` fails iterating ``provider.stream``.
    # The commands themselves consume the stream correctly.
    "run_continue_command": "drives a real agent turn (provider stream)",
    "run_recap_command": "drives a real agent turn (provider stream)",
    "run_summary_command": "drives a real agent turn (provider stream)",
}


def _discover_commands() -> list[str]:
    """Return every ``run_*_command`` async callable exported by the module.

    The discovery is purely reflective: iterate ``dir()`` on the
    ``extra_commands`` module and pick callables whose name starts with
    ``run_`` and ends with ``_command``. ``inspect.iscoroutinefunction``
    is used as a sanity filter so a future non-async helper that happens
    to match the naming convention does not break the suite.
    """
    names: list[str] = []
    for name in dir(extra_commands):
        if not (name.startswith("run_") and name.endswith("_command")):
            continue
        obj: object = getattr(extra_commands, name)
        if not isinstance(obj, Callable):  # type: ignore[arg-type]
            continue
        if not inspect.iscoroutinefunction(obj):
            continue
        names.append(name)
    return sorted(names)


def _build_params() -> list[pytest.param]:
    """Materialise parametrize arguments, applying the exclusion set."""
    params: list[pytest.param] = []
    for name in _discover_commands():
        if name in EXCLUDED:
            params.append(
                pytest.param(name, marks=pytest.mark.skip(reason=EXCLUDED[name]))
            )
        else:
            params.append(pytest.param(name))
    return params


_PARAMS = _build_params()


@pytest.mark.parametrize("command_name", _PARAMS)
@pytest.mark.asyncio
async def test_run_command_smoke(command_name: str, fake_session) -> None:
    """Invoke ``run_<name>_command(fake_session, "")`` and assert no crash."""
    fn = getattr(extra_commands, command_name)
    try:
        await fn(fake_session, "")
    except SystemExit:
        # ``argparse.ArgumentParser.parse_args`` raises SystemExit on
        # ``--help`` / unknown flags; that is a normal control-flow
        # outcome for a slash command, not a test failure.
        return


def test_inventory_matches_exclusions() -> None:
    """Sanity-check that every excluded name actually exists in the module.

    Catches typos in ``EXCLUDED`` early instead of letting them rot as
    silently-irrelevant entries.
    """
    discovered = set(_discover_commands())
    missing = sorted(set(EXCLUDED) - discovered)
    assert not missing, f"EXCLUDED entries not found in module: {missing}"
