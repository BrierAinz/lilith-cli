"""Pytest config for the lilith-tools test suite.

Registers the ``e2e`` marker (used by the optional network-gated
suites under ``tests/e2e/``) and provides a helper that skips an
e2e test when a provider-specific environment variable is missing.
Also isolates the orchestration state file so the suite never writes
into the operator's real ``~/.yggdrasil``.

The root ``pyproject.toml`` already owns the global
``[tool.pytest.ini_options]`` config; this conftest only ADDS the
markers/fixtures below so the ``e2e`` mark is well-known to pytest and
CI can filter the suite via ``-m e2e`` without an ``UnknownMark``
warning.
"""

from __future__ import annotations

import os

import pytest


# ── 1. Marker registration ─────────────────────────────────────────────
#
# ``pytest.mark.e2e`` marks tests that hit real provider endpoints and
# require network access + a valid API key. They are SKIPPED by default
# (the default ``addopts`` in the root pyproject doesn't include them)
# and only execute when the operator passes ``-m e2e`` on the CLI:
#
#     pytest -m e2e                     # run only the e2e suite
#     pytest -m "not e2e"               # run everything else
#
# Declaring the mark here keeps ``--strict-markers`` happy and lets
# ``pytest --markers`` list it as a usable selector.

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e: network-gated test that hits a real provider endpoint; "
        "auto-skipped when the provider's env key is missing.",
    )


# ── 2. Env-key skip helper ─────────────────────────────────────────────
#
# Each e2e test passes the env var name that gates it (e.g.
# ``KIMI_API_KEY`` for ``ejecutor-kimi``). The fixture below returns
# a skip-marker when the env var is absent, so a missing key never
# blocks CI or local development runs of the non-e2e suite. When the
# env var IS set, the call is a no-op and the test runs against the
# real provider.
#
# Usage::
#
#     def test_x(require_env):
#         require_env("KIMI_API_KEY")
#         ...  # real network call

@pytest.fixture
def require_env():
    """Skip the calling test unless the env vars on the call list are set.

    Multiple env keys can be required in one call (all must be set)::

        require_env("KIMI_API_KEY", "OPENCODE_API_KEY")
    """
    missing: list[str] = []

    def _require(*env_vars: str) -> None:
        for var in env_vars:
            if not os.environ.get(var):
                missing.append(var)

    yield _require
    if missing:
        pytest.skip(
            "Provider API key(s) not set: "
            + ", ".join(sorted(set(missing)))
            + ". Set the env key(s) and re-run with `pytest -m e2e`."
        )


# ── 3. Orchestration-state isolation ───────────────────────────────────
#
# ``DelegateSubagentTool`` / ``HermesDelegateTool`` persist every
# delegation as a task via ``OrchestrationStateStore()`` with no
# argument, which resolves to ``~/.yggdrasil/orchestration_state.json``
# (see ``lilith_tools.orchestration_state.default_state_path``). Tests
# that exercise those tools therefore appended their fake tasks and
# their fake token costs to the operator's REAL state, which is what
# ``/state`` and ``/costs`` read back — the suite had polluted it with
# ~184 ``fake-preset`` tasks.
#
# Pointing the documented ``YGGDRASIL_ORCHESTRATION_STATE`` override at
# a per-test tmp file keeps that persistence path exercised (so the
# best-effort writes are still covered) while redirecting it somewhere
# disposable. Tests that set the env var themselves or pass an explicit
# ``state_path`` still win: this only supplies the default.

@pytest.fixture(autouse=True)
def _isolate_orchestration_state(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "orchestration_state.json")
    )
