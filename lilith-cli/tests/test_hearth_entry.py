"""Exercise public commands through the actual Cyclopts parser."""

from lilith_cli.main import app
from lilith_cli import main as main_module
import pytest


def test_bare_entry_opens_agent_conversation(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "home", lambda **kw: calls.append(("home", kw)))
    monkeypatch.setattr(main_module, "start", lambda **kw: calls.append(("start", kw)))
    main_module.default_command()
    assert calls == [("start", {"profile": "agent"})]


def test_cli_setup_accepts_explicit_flags(tmp_path, capsys):
    import yaml
    path = tmp_path / "lilith.yaml"
    with pytest.raises(SystemExit) as result:
        app(["setup", "--model", "fixture", "--base-url", "http://localhost:1234/v1",
             "--config", str(path), "--personalize"])
    assert result.value.code == 0
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["model"] == "fixture"
    assert "Eres Lilith" in saved["system_prompt"]
    assert "Configuración guardada" in capsys.readouterr().out


def test_start_parser_exposes_optional_profile(capsys):
    with pytest.raises(SystemExit) as result:
        app(["start", "--help"])
    assert result.value.code == 0
    assert "--profile" in capsys.readouterr().out


def test_home_coordinator_choice_selects_coordinator_profile(tmp_path, monkeypatch):
    from lilith_cli import hearth, hearth_ui
    choices = iter([("coordinate", str(tmp_path), None), None])
    monkeypatch.setattr(hearth_ui.HearthApp, "run", lambda self: next(choices))
    calls = []
    monkeypatch.setattr(hearth, "start", lambda **kwargs: calls.append(kwargs))
    hearth.home(str(tmp_path), interactive=True)
    assert calls == [{"root": str(tmp_path), "profile": "coordinator"}]
