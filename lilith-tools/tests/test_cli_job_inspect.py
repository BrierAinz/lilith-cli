"""Read-only marker inspection against disposable job roots only."""

import lilith_tools.cli_job_inspect as inspector
import pytest
from lilith_tools.registry import ToolRegistry

JOB = "20260912-000001-1234"


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": tmp_path, "Huginn": tmp_path})
    return tmp_path


def inspect(**kwargs):
    return inspector.CliJobInspectTool().execute(agent="Vor", job_id=JOB, **kwargs)


@pytest.mark.parametrize(
    "value,code", [(b"0\r\n", 0), (b"1", 1), (b"\xef\xbb\xbf-1", -1)]
)
def test_marker_report_is_not_task_verification(jobs, value, code):
    marker = jobs / f"{JOB}.done"
    marker.write_bytes(value)
    before = marker.stat().st_mtime_ns
    result = inspect()
    assert result.success
    assert result.data["job_returncode"] == code
    assert result.data["status"] == (
        "reported_success" if code == 0 else "reported_failure"
    )
    assert not result.data["task_verified"]
    assert not result.data["retry_safe"]
    assert not result.data["execution_performed"]
    assert marker.read_bytes() == value
    assert marker.stat().st_mtime_ns == before


def test_missing_marker_does_not_claim_running_or_complete(jobs):
    result = inspect()
    assert result.success
    assert result.data["status"] == "unknown"
    assert result.data["process_state"] == "not_checked"
    assert list(jobs.iterdir()) == []


@pytest.mark.parametrize(
    "value", [b"", b"pending", b"0\n1", b"0" * 257, b"\xff", b"2147483648"]
)
def test_invalid_marker_is_not_a_result(jobs, value):
    (jobs / f"{JOB}.done").write_bytes(value)
    result = inspect()
    assert not result.success
    assert result.data["status"] == "unreadable"


@pytest.mark.parametrize(
    "job_id",
    ["../secret", "20260912-000001-1234/other", "", None, [], "20260912-000001-1234\n"],
)
def test_invalid_id_reads_nothing(jobs, job_id):
    result = inspector.CliJobInspectTool().execute(agent="Vor", job_id=job_id)
    assert not result.success
    assert result.data["status"] == "invalid_input"


@pytest.mark.parametrize("agent", ["Claude", "vor", "../Vor", None, []])
def test_unknown_agent_rejected(jobs, agent):
    result = inspector.CliJobInspectTool().execute(agent=agent, job_id=JOB)
    assert not result.success


def test_tool_is_registered():
    assert ToolRegistry.get("cli_job_inspect") is inspector.CliJobInspectTool


def test_marker_directory_rejected(jobs):
    (jobs / f"{JOB}.done").mkdir()
    assert not inspect().success


def test_other_job_marker_is_not_used(jobs):
    (jobs / "20260912-000002-1234.done").write_text("0")
    assert inspect().data["status"] == "unknown"


def test_redirected_marker_rejected(jobs, tmp_path):
    target = tmp_path / "unrelated.txt"
    target.write_text("0")
    marker = jobs / f"{JOB}.done"
    try:
        marker.symlink_to(target)
    except OSError:
        pytest.skip("Creating symbolic links is not permitted in this environment")
    result = inspect()
    assert not result.success
    assert result.data["status"] == "unreadable"
    assert target.read_text() == "0"


def test_access_denied_does_not_mean_missing(jobs, monkeypatch):
    (jobs / f"{JOB}.done").write_text("0")

    def denied(*args, **kwargs):
        raise PermissionError("synthetic access denied")

    monkeypatch.setattr(inspector.Path, "open", denied)
    result = inspect()
    assert not result.success
    assert result.data["status"] == "unreadable"


@pytest.mark.parametrize("root_kind", ["missing", "file"])
def test_unavailable_job_root_is_not_an_unknown_job(tmp_path, monkeypatch, root_kind):
    root = tmp_path / "job-root"
    if root_kind == "file":
        root.write_text("not a directory")
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": root})
    result = inspect()
    assert not result.success
    assert result.data["status"] == "root_unavailable"
    assert "reason" not in result.data


def test_marker_cannot_redirect_to_external_file(jobs, tmp_path_factory):
    external = tmp_path_factory.mktemp("external-job-target") / "external.txt"
    external.write_text("0")
    try:
        (jobs / f"{JOB}.done").symlink_to(external)
    except OSError:
        pytest.skip("Creating symbolic links is not permitted in this environment")
    result = inspect()
    assert not result.success
    assert result.data["status"] == "unreadable"
    assert external.read_text() == "0"


@pytest.mark.parametrize(
    "released,expected",
    [(True, "reported_released"), (False, "manual_recovery_required")],
)
def test_worker_cleanup_is_separate_from_exit_and_token_is_not_exposed(
    jobs, released, expected
):
    import json

    (jobs / f"{JOB}.done").write_text("0")
    (jobs / f"{JOB}.done.worker.json").write_text(
        json.dumps(
            {
                "workerExit": 0,
                "workerCompleted": True,
                "leaseReleased": released,
                "requiresManualRecovery": not released,
                "token": "PRIVATE-LEASE-TOKEN",
                "workerPid": 123,
            }
        )
    )
    result = inspect()
    assert result.data["status"] == "reported_success"
    assert result.data["cleanup_status"] == expected
    assert "PRIVATE" not in json.dumps(result.data)
    assert "workerPid" not in result.data
    assert not result.data["retry_safe"]


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "bad-json",
        "x" * 16385,
        '{"workerExit":true,"workerCompleted":true,"leaseReleased":true,"requiresManualRecovery":false}',
        '{"workerExit":1,"workerCompleted":true,"leaseReleased":true,"requiresManualRecovery":false}',
    ],
)
def test_invalid_cleanup_receipt_does_not_claim_release(jobs, payload):
    (jobs / f"{JOB}.done").write_text("0")
    (jobs / f"{JOB}.done.worker.json").write_text(payload)
    result = inspect()
    assert result.data["cleanup_status"] == "unreadable"
    assert result.data["status"] == "reported_success"


def test_missing_cleanup_receipt_remains_unknown(jobs):
    (jobs / f"{JOB}.done").write_text("0")
    assert inspect().data["cleanup_status"] == "unknown"
