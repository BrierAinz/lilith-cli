"""Per-directory conftest for the e2e suite.

Auto-applies the ``@pytest.mark.e2e`` mark to every test in this tree,
so individual tests don't need to repeat the decorator. Operators
opt in by passing ``-m e2e`` on the command line; otherwise the entire
suite is skipped, keeping CI and local default runs hermetic.

Also exposes a ``require_provider_keys`` fixture that maps a preset
name to the env var(s) that gate it, raising ``pytest.skip`` when any
key is missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _opt_in_via_argv(argv: list[str]) -> bool:
    """Return True if the operator asked for the e2e suite via ``-m e2e``.

    We parse ``argv`` directly (rather than ``config.option.markexpr``)
    because pytest's markexpr parsing runs AFTER collection modification,
    which is when our skip is applied. Only an explicit ``-m e2e`` or
    ``-m 'e2e and ...'`` opts in; ``-m 'not e2e'`` does not.

    We accept two pragmatic heuristics:

    1. ``-m e2e`` (with anything *and*-combined) → opt-in.
    2. No ``-m`` flag at all → NO opt-in (default skip).
    """
    i = 0
    while i < len(argv):
        if argv[i] == "-m" and i + 1 < len(argv):
            expr = argv[i + 1]
            # ``e2e`` appears in the expression AND is not negated —
            # covers ``-m e2e``, ``-m 'e2e and not slow'``,
            # ``-m 'not slow and e2e'`` etc.
            if "e2e" in expr and "not e2e" not in expr:
                return True
            return False
        # ``--markers`` long form: ``pytest -m e2e`` is canonical, but
        # some setups write ``--markers=e2e``. Allow either form.
        if argv[i].startswith("--markers=") and "e2e" in argv[i]:
            return True
        if argv[i] == "-m" and i + 1 >= len(argv):
            return False
        i += 1
    return False


_E2E_DEFAULT_SKIP = (
    "e2e suite is skipped by default. Run with `pytest -m e2e` to execute. "
    "Each test also self-skips when its provider's API key env var is missing."
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark every test in this tree as ``e2e`` AND skip it by default.

    The mark is added so ``-m e2e`` selects the suite, and an
    unconditional skip keeps the rest of the lilith-tools test run
    hermetic. To run them, the operator passes ``-m e2e`` on the CLI;
    in that case we drop the skip marker and let the test execute.
    Each test still ALSO self-skips when its provider's API key env
    var is missing (``require_provider_keys`` fixture).
    """
    opt_in = _opt_in_via_argv(list(sys.argv))
    for item in items:
        # Match by path PART, not by string substring — Windows uses "\\"
        # and POSIX uses "/", so a forward-slash string match is wrong
        # on Windows. The ``parts`` tuple is separator-independent.
        item_path = getattr(item, "path", None) or Path(str(item.fspath))
        parts = Path(item_path).parts
        is_e2e = (
            len(parts) >= 3
            and parts[-2] == "e2e"
            and parts[-3] == "tests"
        )
        item.add_marker(pytest.mark.e2e)
        if opt_in:
            continue
        if is_e2e:
            item.add_marker(pytest.mark.skip(reason=_E2E_DEFAULT_SKIP))


# ── Fixtures ─────────────────────────────────────────────────────────
#
# All fixtures here are "real network" fixtures — they only apply
# during the e2e run, but pytest collects them whether or not the
# marker filter is on. The skip behaviour lives in the
# ``require_provider_keys`` fixture; tests that need network decide
# at runtime whether the env is ready.

@pytest.fixture
def tmp_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test workdir with the process CWD pointing at it.

    Agentic delegate writes into the workdir; using ``tmp_path``
    guarantees the test cannot pollute the developer's filesystem.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)
    return workdir


@pytest.fixture
def isolated_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force ``memory_save`` / ``memory_recall`` to use a per-test SQLite DB.

    Without isolation, a passing e2e run would silently write notes
    into the operator's real ``~/.yggdrasil/memory.db``. We point
    ``YGGDRASIL_HOME`` at a temp dir so the DB lives under
    ``tmp_path``.
    """
    fake_home = tmp_path / "ygg_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YGGDRASIL_HOME", str(fake_home))
    return fake_home / "memory.db"


@pytest.fixture
def isolated_state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force ``OrchestrationStateStore`` to use a per-test JSON file."""
    fake_home = tmp_path / "ygg_home_state"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YGGDRASIL_HOME", str(fake_home))
    state_path = fake_home / "orchestration_state.json"
    return state_path


@pytest.fixture
def require_provider_keys():
    """Map preset names to env var(s) that must be set; skip when missing.

    Single source of truth — see ``_providers.keys_by_preset`` for the
    preset → env-var(s) table. Tests can request one or several
    presets; the union of required env keys is what gates the run.

        def test_x(require_provider_keys):
            require_provider_keys("batch-deepseek")
            ...  # real network call to DeepSeek
    """
    from ._providers import missing_keys_for

    missing: list[str] = []

    def _require(*preset_names: str) -> tuple[str, ...]:
        from ._providers import keys_by_preset

        required: list[str] = []
        seen: set[str] = set()
        for name in preset_names:
            for var in keys_by_preset.get(name, ()):
                if var not in seen:
                    seen.add(var)
                    required.append(var)
                    if not os.environ.get(var) and var not in missing:
                        missing.append(var)
        return tuple(required)

    yield _require
    if missing:
        pytest.skip(
            "Provider API key(s) not set for e2e run: "
            + ", ".join(missing)
            + ". Set the env key(s) and re-run with `pytest -m e2e`."
        )
