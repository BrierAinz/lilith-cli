from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def session():
    return SimpleNamespace(
        config=SimpleNamespace(confirm_write=True),
        _command_history=[],
        _tool_call_history=[{
            "name": "delegate_subagent",
            "arguments": {
                "preset": "cocytus",
                "prompt": "Implement {TASK}\nProject {PROJECT}\nContext {CONTEXT}",
                "agentic": True,
            },
            "success": True,
        }],
        last_user_message="",
    )


@pytest.mark.asyncio
async def test_skills_history_bench_and_rollback(session, monkeypatch, tmp_path: Path) -> None:
    from lilith_cli.commands import CommandRegistry
    from lilith_skills.delegation_skills import DelegationSkillRegistry

    root = tmp_path / "skills"
    monkeypatch.setenv("YGGDRASIL_DELEGATION_SKILLS", str(root))
    registry = CommandRegistry(session)
    registry.discover()
    command = registry.get("skills")

    await command.execute("save latest --name evolving --description v1")
    first = DelegationSkillRegistry(root, seed_defaults=False).versions("evolving")[0]
    await command.execute("save latest --name evolving --description v2")
    versions = DelegationSkillRegistry(root, seed_defaults=False).versions("evolving")
    assert len(versions) == 2

    with command.session_console_capture() as capture:
        await command.execute("history evolving")
    history_output = capture.get()
    assert first.version_id in history_output
    assert "operator:/skills save latest" in history_output

    with command.session_console_capture() as capture:
        await command.execute("bench evolving")
    assert '"passed": true' in capture.get().lower()

    await command.execute(f"rollback evolving {first.version_id} CONFIRMAR")
    restored = DelegationSkillRegistry(root, seed_defaults=False).get("evolving")
    assert restored is not None
    assert restored.description == "v1"
    assert len(DelegationSkillRegistry(root, seed_defaults=False).versions("evolving")) == 3


@pytest.mark.asyncio
async def test_skills_metrics_surface_is_copy_safe(session, monkeypatch, tmp_path: Path) -> None:
    from lilith_cli.commands import CommandRegistry
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry

    root = tmp_path / "skills"
    monkeypatch.setenv("YGGDRASIL_DELEGATION_SKILLS", str(root))
    version = DelegationSkillRegistry(root, seed_defaults=False).save_versioned(
        DelegationSkill(
            name="metric-skill",
            description="metric",
            preset="cocytus",
            prompt_template="Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
            agentic=True,
        ),
        source="fixture",
    )
    registry = CommandRegistry(session)
    registry.discover()
    command = registry.get("skills")
    with command.session_console_capture() as capture:
        await command.execute("metrics metric-skill")
    output = capture.get()
    assert version.version_id in output
    assert "runs=0" in output
    assert "rollbacks=0" in output
