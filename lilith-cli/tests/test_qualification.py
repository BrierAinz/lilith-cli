import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from lilith_cli import calibration
from lilith_cli.config import YggdrasilConfig
from lilith_cli.qualification import allows_tool, validate_report


def report_for(cfg):
    results = []
    for index in range(1, 4):
        for case in calibration.CASES:
            response = {"read": '{"code":"NORTH-742","count":3}', "review": '{"line":2,"issue":"division_by_zero"}',
                        "edit": '{"code":"def safe_ratio(n,d): return 0 if d == 0 else n/d"}'}.get(case["id"], "")
            calls = [{"name": "file_read", "arguments": {"path": "fixture.txt", "start_line": 1, "max_lines": 2}}] if case["id"] in ("tool", "scope") else []
            results.append({"case": case["id"], "round": index, "outcome": "pass", "response": response, "tool_calls": calls})
    return {"suite_hash": calibration.SUITE_HASH, "validator_hash": hashlib.sha256(Path(calibration.__file__).read_bytes()).hexdigest(),
            "model": calibration.model_identity(cfg), "date": datetime.now(UTC).isoformat(), "rounds": 3, "status": "complete", "results": results}


def test_model_change_removes_edit_eligibility(tmp_path):
    cfg = YggdrasilConfig(provider="local", model="first", require_calibration_for_edits=True)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report_for(cfg)), encoding="utf-8")
    cfg.calibration_report = str(path)
    assert allows_tool(cfg, "file_write")
    cfg.model = "other"
    assert not allows_tool(cfg, "file_write")
    assert allows_tool(cfg, "file_read")


def test_raw_failure_cannot_be_promoted_by_editing_summary(tmp_path):
    cfg = YggdrasilConfig(require_calibration_for_edits=True)
    report = report_for(cfg)
    report["results"][0]["response"] = "wrong"
    report["recommendation"] = {"edit_candidate": True}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    cfg.calibration_report = str(path)
    assert not validate_report(path, cfg)["edit_candidate"]
    assert not allows_tool(cfg, "file_write")


def test_missing_report_fails_closed_only_for_mutations():
    cfg = YggdrasilConfig(require_calibration_for_edits=True)
    assert allows_tool(cfg, "file_read")
    assert not allows_tool(cfg, "file_write")
    assert not allows_tool(cfg, "delegate_subagent")


def test_expired_report_does_not_keep_write_eligibility(tmp_path):
    from datetime import timedelta
    cfg = YggdrasilConfig(require_calibration_for_edits=True)
    report = report_for(cfg)
    report["date"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    cfg.calibration_report = str(path)
    assert not allows_tool(cfg, "file_write")


def test_actual_executor_blocks_unqualified_mutation(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from lilith_cli.agent import AgentSession
    from lilith_cli.providers import ToolCall
    monkeypatch.setenv("YGGDRASIL_PROVIDER_HEALTH_DB", str(tmp_path / "health.sqlite3"))
    session = AgentSession(YggdrasilConfig(require_calibration_for_edits=True, memory={"enabled": False}))
    session._execute_tool_impl = AsyncMock()
    result = asyncio.run(session.execute_tool(ToolCall("write", "file_write", {"path": "a.py", "content": "x"})))
    assert "calibraci" in result.content
    session._execute_tool_impl.assert_not_awaited()
