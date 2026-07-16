"""Tests for the /learn command — post-mortems → suggested delegation skills."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PKG_ROOT = str(Path(__file__).resolve().parent.parent)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from lilith_cli._learn_section import (  # noqa: E402
    save_suggestion,
    suggest_from_post_mortems,
    suggest_from_state_path,
)


def _pm(preset: str, *, success: bool = True, task_id: str = "", **extra) -> dict:
    return {"preset": preset, "success": success, "task_id": task_id, **extra}


def test_two_successes_same_preset_produce_a_suggestion():
    suggestions = suggest_from_post_mortems(
        [_pm("ejecutor-kimi"), _pm("ejecutor-kimi")]
    )
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.preset == "ejecutor-kimi"
    assert s.success_count == 2
    assert "{TASK}" in s.prompt_template


def test_single_success_is_not_enough():
    assert suggest_from_post_mortems([_pm("ejecutor-kimi")]) == []


def test_failures_and_empty_presets_are_ignored():
    suggestions = suggest_from_post_mortems(
        [
            _pm("ejecutor-kimi", success=False),
            _pm("ejecutor-kimi", success=False),
            _pm(""),
            _pm(""),
        ]
    )
    assert suggestions == []


def test_ordering_by_success_count_desc():
    suggestions = suggest_from_post_mortems(
        [_pm("a-preset")] * 2 + [_pm("b-preset")] * 3
    )
    assert [s.preset for s in suggestions] == ["b-preset", "a-preset"]
    assert [s.index for s in suggestions] == [1, 2]


def test_suggest_from_state_path_reads_json(tmp_path):
    state = tmp_path / "orchestration_state.json"
    state.write_text(
        json.dumps(
            {
                "post_mortems": [
                    _pm("batch-deepseek", task_id="t1"),
                    _pm("batch-deepseek", task_id="t2"),
                ],
                "tasks": [
                    {"id": "t1", "description": "convertir docs"},
                    {"id": "t2", "description": "generar boilerplate"},
                ],
            }
        ),
        encoding="utf-8",
    )
    suggestions = suggest_from_state_path(state)
    assert len(suggestions) == 1
    assert suggestions[0].sample_tasks  # picked up from tasks


def test_suggest_from_state_path_missing_or_corrupt_returns_empty(tmp_path):
    assert suggest_from_state_path(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert suggest_from_state_path(bad) == []


def test_save_suggestion_writes_yaml(tmp_path):
    suggestions = suggest_from_post_mortems(
        [_pm("ejecutor-kimi"), _pm("ejecutor-kimi")]
    )
    path = save_suggestion(suggestions[0], skills_root=tmp_path)
    assert path.exists()
    assert path.suffix in (".yaml", ".yml")
    assert suggestions[0].preset in path.read_text(encoding="utf-8")


def test_learn_is_wired_into_the_repl():
    import lilith_cli.repl as repl_mod

    assert "/learn" in repl_mod._SLASH_COMMANDS
    from lilith_cli.extra_commands import run_learn_command  # noqa: F401


@pytest.mark.asyncio
async def test_run_learn_command_without_state_does_not_crash(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "missing.json")
    )
    from lilith_cli.extra_commands import run_learn_command

    await run_learn_command(None, "")  # renders an error, must not raise
