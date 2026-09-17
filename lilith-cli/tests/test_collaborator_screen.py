import lilith_tools.cli_job_inspect as inspector
import pytest
from lilith_cli.collaborator_screen import CollaboratorScreen
from lilith_cli.hearth_ui import HearthApp
from lilith_tools.cli_job_journal import CliJobJournal
from textual.widgets import OptionList, Static


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 30)])
async def test_hearth_opens_and_inspects_saved_reference(tmp_path, monkeypatch, size):
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "state.sqlite3"))
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(inspector, "JOB_ROOTS", {"Vor": jobs})
    journal = CliJobJournal()
    ref = journal.begin("Vor", "private task", 15)
    job_id = "20260912-000001-1234"
    journal.finish(ref, {"status": "timeout", "job_id": job_id})
    (jobs / f"{job_id}.done").write_text("0")
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "ui.yaml")
    async with app.run_test(size=size) as pilot:
        await pilot.press("f7")
        await app.screen.workers.wait_for_complete()
        assert isinstance(app.screen, CollaboratorScreen)
        listing = app.screen.query_one("#collab-list", OptionList)
        assert listing.option_count == 1
        assert listing.region.height > 1
        listing.highlighted = 0
        await pilot.press("enter")
        await app.screen.workers.wait_for_complete()
        assert "Salida 0" in str(
            app.screen.query_one("#collab-detail", Static).render()
        )
        assert CliJobJournal().recent()[0]["observation"]["status"] == "timeout"
        await pilot.press("escape")
        assert not isinstance(app.screen, CollaboratorScreen)
        assert app.return_value is None


@pytest.mark.asyncio
async def test_empty_journal_view_creates_no_storage(tmp_path, monkeypatch):
    state = tmp_path / "state" / "state.sqlite3"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state))
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "ui.yaml")
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.click("#collaborators")
        await app.screen.workers.wait_for_complete()
        assert app.screen.query_one("#collab-list", OptionList).option_count == 0
        assert not state.parent.exists()
