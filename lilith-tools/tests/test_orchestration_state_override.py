import json

from lilith_tools import orchestration_state as state_module


def test_env_override_does_not_import_global_legacy(monkeypatch, tmp_path) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps({
            "version": 2,
            "plan": None,
            "tasks": [{"id": "legacy-task", "status": "pendiente"}],
            "costs": {"historical": {"presets": {}, "providers": {}, "total": {}}},
            "post_mortems": [],
        }),
        encoding="utf-8",
    )
    override = tmp_path / "isolated.sqlite3"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(override))
    monkeypatch.setattr(state_module, "legacy_state_path", lambda: legacy)

    store = state_module.OrchestrationStateStore()
    state = store.get()
    assert state["tasks"] == []
