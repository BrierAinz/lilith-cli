import json

import lilith_tools.cli_job_inspect as inspector
import pytest
from lilith_cli.collaborator_jobs import jobs_app

JOB = "20260912-000001-1234"


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": tmp_path, "Huginn": tmp_path})
    return tmp_path


def test_json_observation_does_not_conflate_task_success(jobs, capsys):
    (jobs / f"{JOB}.done").write_text("1")
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect", "Vor", JOB, "--json"])
    assert caught.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["observation_ok"]
    assert payload["observation"]["status"] == "reported_failure"
    assert not payload["observation"]["task_verified"]
    assert not payload["observation"]["execution_performed"]


def test_unknown_job_has_distinct_exit(jobs, capsys):
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect", "Vor", JOB, "--json"])
    assert caught.value.code == 3
    assert json.loads(capsys.readouterr().out)["observation"]["status"] == "unknown"
    assert list(jobs.iterdir()) == []


def test_bad_id_has_distinct_exit(jobs, capsys):
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect", "Vor", "../secret", "--json"])
    assert caught.value.code == 2
    assert not json.loads(capsys.readouterr().out)["observation_ok"]


def test_human_report_does_not_claim_verification(jobs, capsys):
    (jobs / f"{JOB}.done").write_text("0")
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect", "Huginn", JOB])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert "salida 0" in out
    assert "requiere revisión" in out
    assert "No se relanzó" in out


def test_registered_main_command_reads_fixture_without_provider(jobs, capsys):
    from lilith_cli.main import app

    (jobs / f"{JOB}.done").write_text("0")
    with pytest.raises(SystemExit) as caught:
        app(["jobs", "inspect", "Vor", JOB, "--json"])
    assert caught.value.code == 0
    assert json.loads(capsys.readouterr().out)["observation"]["job_returncode"] == 0


def test_unavailable_root_is_exit_two_not_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": tmp_path / "missing"})
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect", "Vor", JOB, "--json"])
    assert caught.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert not payload["observation_ok"]
    assert payload["observation"]["status"] == "root_unavailable"


def test_human_output_warns_when_workspace_lease_needs_recovery(jobs, capsys):
    (jobs / f"{JOB}.done").write_text("0")
    (jobs / f"{JOB}.done.worker.json").write_text(
        json.dumps(
            {
                "workerExit": 0,
                "workerCompleted": True,
                "leaseReleased": False,
                "requiresManualRecovery": True,
            }
        )
    )
    with pytest.raises(SystemExit) as caught:
        jobs_app(["inspect", "Vor", JOB])
    assert caught.value.code == 0
    assert "recuperación manual" in capsys.readouterr().out
