from typing import ClassVar
from unittest.mock import MagicMock

from lilith_cli.agent import AgentSession
from lilith_cli.agent_modes import tool_capability
from lilith_cli.config import YggdrasilConfig
from lilith_cli.robust_kit import configure_session
from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry


def _session(tmp_path, monkeypatch) -> AgentSession:
    monkeypatch.chdir(tmp_path)
    cfg = YggdrasilConfig(memory={"enabled": False}, max_iterations=32)
    session = AgentSession(cfg, provider=MagicMock())
    configure_session(session, "agent")
    return session


def test_agent_profile_exposes_mission_and_orchestration_tools(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    names = {item["name"] for item in session.get_tool_descriptions()}
    assert {
        "mission_court",
        "mission_compute",
        "mission_authority",
        "mission_prepare",
        "mission_status",
        "mission_complete",
        "mission_resume_expired",
        "mission_delegate",
        "mission_skill_run",
        "orchestration_state",
        "mission_conclave",
        "mission_desktop_observe",
        "mission_desktop_act",
        "mission_admin_exec",
    } <= names
    assert "vor_delegate" not in names
    assert "huginn_delegate" not in names


def test_mission_observation_tools_are_read_only_but_state_tools_are_not() -> None:
    for name in (
        "mission_court", "mission_compute", "mission_authority", "mission_status",
        "mission_learning", "mission_desktop_observe",
    ):
        assert tool_capability(name) == "read"
    for name in (
        "mission_prepare", "mission_complete", "mission_resume_expired",
        "mission_desktop_act", "mission_admin_exec",
    ):
        assert tool_capability(name) == "mutate"


def test_dynamic_mcp_tool_is_allowed_by_agent_prefix(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    class DynamicMcpTool(BaseTool):
        name = "mcp_fixture_ping"
        description = "fixture"
        parameters: ClassVar[dict] = {}

        def execute(self, **kwargs):
            return ToolResult(True, {"pong": True})

    ToolRegistry.register(DynamicMcpTool)
    try:
        session._tools_cache = None
        names = {item["name"] for item in session.get_tool_descriptions()}
        assert "mcp_fixture_ping" in names
        session.disable_tool("mcp_fixture_ping")
        assert "mcp_fixture_ping" not in {
            item["name"] for item in session.get_tool_descriptions()
        }
    finally:
        ToolRegistry.unload("mcp_fixture_ping")


def test_learning_is_read_only_and_visible_to_agent(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    names = {item["name"] for item in session.get_tool_descriptions()}
    assert "mission_learning" in names
    assert tool_capability("mission_learning") == "read"


def test_mission_complete_forwards_campaign_followups(monkeypatch) -> None:
    from lilith_cli.mission.kernel import MissionKernel
    from lilith_cli.mission.tools import MissionCompleteTool

    seen = {}

    def fake_complete(self, task_id, **kwargs):
        seen.update(task_id=task_id, **kwargs)
        return {"ok": True}

    monkeypatch.setattr(MissionKernel, "complete", fake_complete)
    followup = {"objective": "Add diagnostics", "success_criteria": ["tests pass"]}
    result = MissionCompleteTool().execute(
        task_id="mission-1",
        success=True,
        summary="done",
        next_missions=[followup],
        campaign_mode=True,
    )
    assert result.success
    assert seen["next_missions"] == [followup]
    assert seen["campaign_mode"] is True


def test_mission_compute_uses_health_snapshot_for_routing(monkeypatch) -> None:
    import lilith_cli.mission.tools as tools_module

    health = [
        {"resource_id": "opencode_go", "available": False, "state": "open"},
        {"resource_id": "minimax_plus", "available": False, "state": "open"},
        {"resource_id": "claude_pro", "available": False, "state": "unavailable"},
        {"resource_id": "gpt_plus", "available": True, "state": "ready"},
    ]
    monkeypatch.setattr(
        tools_module.ComputeHealthResolver,
        "public_snapshot",
        lambda self, **kwargs: health,
    )
    result = tools_module.MissionComputeTool().execute(
        capabilities=["code_write"],
        court_agent="cocytus",
    )
    assert result.success
    assert result.data["resource"]["resource_id"] == "gpt_plus"
    assert result.data["health"] == health


def test_mission_compute_inventory_includes_health(monkeypatch) -> None:
    import lilith_cli.mission.tools as tools_module

    health = [{"resource_id": "gpt_plus", "available": True, "state": "ready"}]
    monkeypatch.setattr(
        tools_module.ComputeHealthResolver,
        "public_snapshot",
        lambda self, **kwargs: health,
    )
    result = tools_module.MissionComputeTool().execute()
    assert result.success
    assert result.data["resources"]
    assert result.data["health"] == health
