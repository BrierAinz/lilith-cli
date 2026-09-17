import json
import sqlite3

import pytest
from lilith_tools.cli_job_journal import CliJobJournal


def test_reopen_preserves_reference_without_private_content(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = CliJobJournal(path)
    ref = journal.begin("Vor", "PRIVATE PROMPT", 15)
    journal.finish(
        ref,
        {
            "status": "timeout",
            "job_id": "20260912-000001-1234",
            "output": "PRIVATE RESPONSE",
            "stderr": "PRIVATE ERROR",
        },
    )
    rows = CliJobJournal(path).recent()
    assert rows[0]["reference"] == ref
    assert rows[0]["observation"]["job_id"] == "20260912-000001-1234"
    assert not rows[0]["retry_safe"]
    with sqlite3.connect(path) as conn:
        dump = "\n".join(conn.iterdump())
    assert "PRIVATE" not in dump


def test_unfinished_reference_does_not_claim_running(tmp_path):
    journal = CliJobJournal(tmp_path / "journal.sqlite3")
    ref = journal.begin("Huginn", "review", 15)
    row = CliJobJournal(journal.path).recent()[0]
    assert row["reference"] == ref
    assert row["observation"] is None
    assert not row["task_verified"]


def test_read_does_not_create_storage(tmp_path):
    path = tmp_path / "missing" / "journal.sqlite3"
    assert CliJobJournal(path).recent() == []
    assert not path.parent.exists()


def test_second_observation_does_not_overwrite_first(tmp_path):
    journal = CliJobJournal(tmp_path / "journal.sqlite3")
    ref = journal.begin("Vor", "review", 15)
    journal.finish(ref, {"status": "timeout"})
    with pytest.raises(ValueError):
        journal.finish(ref, {"status": "ok"})
    assert journal.recent()[0]["observation"]["status"] == "timeout"


def test_recent_command_uses_isolated_journal(monkeypatch, tmp_path, capsys):
    from lilith_cli.collaborator_jobs import jobs_app

    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "state.sqlite3"))
    ref = CliJobJournal().begin("Vor", "review", 15)
    with pytest.raises(SystemExit) as caught:
        jobs_app(["recent"])
    assert caught.value.code == 0
    assert json.loads(capsys.readouterr().out)["references"][0]["reference"] == ref


@pytest.mark.parametrize(
    "payload", ["[]", "not-json", '{"job_id":"../secret"}', " " * 4097]
)
def test_lookup_rejects_corrupt_observations(tmp_path, payload):
    journal = CliJobJournal(tmp_path / "journal.sqlite3")
    ref = journal.begin("Vor", "review", 15)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE attempts SET observation=? WHERE reference=?", (payload, ref)
        )
    with pytest.raises(ValueError):
        journal.lookup(ref)


def test_lookup_is_exact_and_does_not_create_missing_database(tmp_path):
    journal = CliJobJournal(tmp_path / "missing.sqlite3")
    assert journal.lookup("a" * 32) is None
    assert not journal.path.exists()
    with pytest.raises(ValueError):
        journal.lookup("../secret")


def test_reference_inspection_after_reopen_reads_marker_only(
    monkeypatch, tmp_path, capsys
):
    import lilith_tools.cli_job_inspect as inspector
    from lilith_cli.collaborator_jobs import jobs_app

    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "state.sqlite3"))
    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": root})
    job_id = "20260912-000001-1234"
    journal = CliJobJournal()
    ref = journal.begin("Vor", "private prompt", 15)
    journal.finish(ref, {"status": "timeout", "job_id": job_id})
    (root / f"{job_id}.done").write_text("0")
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect-reference", ref])
    assert caught.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observation"]["status"] == "reported_success"
    assert not result["observation"]["execution_performed"]
    assert CliJobJournal().recent()[0]["observation"]["status"] == "timeout"


def test_unresolved_reference_does_not_probe_worker(monkeypatch, tmp_path, capsys):
    import lilith_tools.cli_job_inspect as inspector
    from lilith_cli.collaborator_jobs import jobs_app

    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "state.sqlite3"))
    ref = CliJobJournal().begin("Huginn", "review", 15)

    def forbidden(*args, **kwargs):
        pytest.fail("Unresolved reference must not query any worker")

    monkeypatch.setattr(inspector.CliJobInspectTool, "execute", forbidden)
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect-reference", ref])
    assert caught.value.code == 3
    assert json.loads(capsys.readouterr().out)["reason"] == "worker_id_not_recorded"


def test_finish_does_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        CliJobJournal(path).finish("a" * 32, {"status": "ok"})
    assert not path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"status":"private text"}',
        '{"returncode":true}',
        '{"status":"ok","status":"failed"}',
        '{"output":"private"}',
        " " * 4097,
    ],
)
def test_recent_refuses_corruption_without_silently_emptying_history(tmp_path, payload):
    journal = CliJobJournal(tmp_path / "journal.sqlite3")
    ref = journal.begin("Vor", "review", 15)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE attempts SET observation=? WHERE reference=?", (payload, ref)
        )
    with pytest.raises(ValueError):
        journal.recent()


@pytest.mark.parametrize(
    "data",
    [
        {"status": "PRIVATE"},
        {"job_id": "../file"},
        {"job_id_source": "PRIVATE"},
        {"returncode": True},
        {"effects_unknown": "false"},
    ],
)
def test_finish_rejects_invalid_metadata_without_saving_it(tmp_path, data):
    journal = CliJobJournal(tmp_path / "journal.sqlite3")
    ref = journal.begin("Vor", "review", 15)
    with pytest.raises(ValueError):
        journal.finish(ref, data)
    assert journal.recent()[0]["observation"] is None


def test_corrupt_database_is_not_empty_history(tmp_path):
    path = tmp_path / "journal.sqlite3"
    path.write_text("not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        CliJobJournal(path).recent()


def test_concurrent_request_reservation_has_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "journal.sqlite3"

    def reserve(_):
        return CliJobJournal(path).reserve(
            "Vor", "review", 15, "c" * 32, "same context"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(created for _, created in results) == 1
    assert len({reference for reference, _ in results}) == 1
    assert len(CliJobJournal(path).recent()) == 1


@pytest.mark.parametrize("agent", ["unknown", "vor", None, []])
def test_invalid_agent_does_not_create_journal(tmp_path, agent):
    path = tmp_path / "missing" / "journal.sqlite3"
    with pytest.raises(ValueError):
        CliJobJournal(path).begin(agent, "review", 15)
    assert not path.parent.exists()


def test_request_reservation_across_independent_processes(tmp_path):
    import subprocess
    import sys

    journal_path = tmp_path / "multiprocess.sqlite3"
    gate = tmp_path / "start.flag"
    script = (
        "import json,sys,time; from pathlib import Path; "
        "from lilith_tools.cli_job_journal import CliJobJournal; "
        "gate=Path(sys.argv[2]); deadline=time.monotonic()+10\n"
        "while not gate.exists():\n"
        " if time.monotonic()>deadline: raise RuntimeError('gate timeout')\n"
        " time.sleep(0.02)\n"
        "print(json.dumps(CliJobJournal(Path(sys.argv[1])).reserve('Vor','synthetic',15,'e'*32,'same context')))\n"
    )
    children = []
    try:
        for _ in range(4):
            children.append(
                subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(journal_path), str(gate)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            )
        gate.write_text("start")
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sum(created for _, created in results) == 1
        assert len({reference for reference, _ in results}) == 1
        assert len(CliJobJournal(journal_path).recent()) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("limit", [0, 21, True, "3"])
def test_recent_tool_rejects_bad_limits(limit):
    from lilith_tools.cli_job_inspect import CliJobsRecentTool

    result = CliJobsRecentTool().execute(limit=limit)
    assert not result.success
    assert result.data["status"] == "invalid_input"


@pytest.mark.asyncio
async def test_review_session_can_recover_without_model_or_dispatch(
    tmp_path, monkeypatch
):
    from unittest.mock import MagicMock

    import lilith_tools.cli_job_inspect as inspector
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig
    from lilith_cli.providers import ToolCall

    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "state.sqlite3"))
    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": root})
    journal = CliJobJournal()
    ref = journal.begin("Vor", "synthetic", 15)
    job_id = "20260912-000001-1234"
    journal.finish(ref, {"status": "timeout", "job_id": job_id})
    (root / f"{job_id}.done").write_text("0")
    provider = MagicMock()
    session = AgentSession(
        YggdrasilConfig(memory={"enabled": False}, agent_mode="review-only"),
        provider=provider,
    )
    provider.reset_mock()  # constructor only checked provider truthiness
    recent = await session.execute_tool(
        ToolCall(id="recent", name="cli_jobs_recent", arguments={})
    )
    assert json.loads(recent.content)["references"][0]["reference"] == ref
    observed = await session.execute_tool(
        ToolCall(id="inspect", name="cli_job_reference", arguments={"reference": ref})
    )
    data = json.loads(observed.content)
    assert data["status"] == "reported_success"
    assert not data["task_verified"]
    assert not data["execution_performed"]
    assert not provider.mock_calls
