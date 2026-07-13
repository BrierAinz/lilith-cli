"""Shared test fixtures for lilith-skills.

The single guard exposed here is :func:`vanaheim_yaml_path`. It locates
the canonical ``<repo>/Vanaheim/Agents/agent_cards.yaml`` by walking up
from this conftest file. When the file is absent (e.g. a standalone
Asgard checkout on CI without the Yggdrasil hub parent) the consuming
test is skipped instead of failing with ``FileNotFoundError``.

This is the unified pattern matching the fixtures already in place in
``lilith-orchestrator/tests/test_card_bridge.py`` and the in-file
fixture in ``lilith-skills/tests/test_card_hooks.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def vanaheim_yaml_path() -> Path:
    """Locate the real Vanaheim/Agents/agent_cards.yaml (skip if absent).

    On a standalone Asgard checkout (e.g. CI ubuntu run that only
    checks out the Asgard submodule) the hub parent is missing and the
    canonical file cannot be located. We ``pytest.skip`` rather than
    raise so the test surface degrades gracefully to a green skip.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "Vanaheim" / "Agents" / "agent_cards.yaml"
        if candidate.exists():
            return candidate
    pytest.skip(
        "requires full Yggdrasil hub checkout (Vanaheim) — "
        "Vanaheim/Agents/agent_cards.yaml not found from this test location"
    )
