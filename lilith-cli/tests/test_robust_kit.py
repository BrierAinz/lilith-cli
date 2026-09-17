from unittest.mock import AsyncMock

import pytest
from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.providers import ToolCall
from lilith_cli.robust_kit import catalog, configure_session
from lilith_cli.task_workspace import TaskSpec


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("YGGDRASIL_PROVIDER_HEALTH_DB", str(tmp_path / "health.sqlite3"))
    monkeypatch.chdir(tmp_path)
    return AgentSession(YggdrasilConfig(memory={"enabled": False}))


def test_catalog_is_small_versioned_and_injected_one_skill_at_a_time(session):
    skills = catalog()
    assert len(skills) == 8
    assert all(skill.version == "1.0.0" and len(skill.content) < 2000 for skill in skills.values())
    configure_session(session, "compact", "code-review")
    assert skills["code-review"].content in session.system_prompt
    assert skills["creative-brief"].content not in session.system_prompt


def test_compact_limits_tools_and_reader_exposes_none(session):
    configure_session(session, "compact")
    assert {t["name"] for t in session.get_tool_descriptions()} <= {"file_read", "file_write"}
    assert session.config.max_iterations <= 8
    configure_session(session, "reader")
    assert session.get_tool_descriptions() == []


def test_coordinator_preserves_model_and_exposes_bounded_collaboration(session):
    identity = session.config.provider, session.config.model
    configure_session(session, "coordinator")
    names = {tool["name"] for tool in session.get_tool_descriptions()}
    assert {"mission_delegate", "mission_conclave", "mission_court", "mission_compute", "cli_jobs_recent", "cli_job_reference"} <= names
    assert "system" not in names
    assert len(names) <= 9
    assert session._serial_tools
    assert session._strict_tool_arguments
    assert (session.config.provider, session.config.model) == identity


@pytest.mark.asyncio
async def test_coordinator_rejects_unlisted_tool_before_execution(session):
    configure_session(session, "coordinator")
    session._execute_tool_impl = AsyncMock(side_effect=AssertionError("must not execute"))
    result = await session.execute_tool(ToolCall("not-allowed", "system", {"command": "anything"}))
    assert result.content.startswith("Error:")
    session._execute_tool_impl.assert_not_awaited()


@pytest.mark.asyncio
async def test_weak_model_invalid_args_never_reach_execution(session):
    configure_session(session, "compact")
    session._execute_tool_impl = AsyncMock(side_effect=AssertionError("must not execute"))
    result = await session.execute_tool(ToolCall("bad", "file_read", {"path": 123}))
    assert result.content.startswith("Error:")
    result = await session.execute_tool(ToolCall("bad", "system", {"command": "anything"}))
    assert result.content.startswith("Error:")
    session._execute_tool_impl.assert_not_awaited()


def test_context_overflow_fails_without_silently_dropping_rules(session):
    configure_session(session, "compact")
    session.system_prompt += "x" * 25000
    with pytest.raises(ValueError, match="Contexto excede"):
        session._build_messages()


def test_reader_rejects_edit_spec(tmp_path):
    with pytest.raises(ValueError, match="lector no permite"):
        TaskSpec(tmp_path, "edit", ["a.py"], edit=True, profile="reader").validate()


def test_file_read_ranges_keep_line_evidence(tmp_path):
    from lilith_tools.filesystem import FileReadTool
    path = tmp_path / "source.py"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = FileReadTool().execute(path=str(path), start_line=2, max_lines=1)
    assert result.success
    assert "two" in result.data and "one" not in result.data
    assert "2-2" in result.data
    assert not FileReadTool().execute(path=str(path), start_line=-1).success


@pytest.mark.asyncio
async def test_compact_orders_dependent_writes(session, tmp_path):
    import asyncio

    from lilith_cli.providers import ToolResult
    configure_session(session, "compact")
    session.config.confirm_write = False
    class Provider:
        calls = 0
        async def stream(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield {"tool_calls": [{"id": value, "name": "file_write", "arguments": {
                    "path": "out.txt", "content": value}} for value in ("first", "second")], "finish_reason": "tool_calls"}
            else:
                yield {"content": "done", "finish_reason": "stop"}
    session.provider = Provider()
    async def write(call):
        if call.arguments["content"] == "first":
            await asyncio.sleep(0.02)
        (tmp_path / "out.txt").write_text(call.arguments["content"])
        return ToolResult(call.id, call.name, "Written")
    session._execute_tool_impl = write
    events = [event async for event in session.process_message_stream("Write the two versions in order")]
    assert events[-1]["content"] == "done"
    assert (tmp_path / "out.txt").read_text() == "second"
