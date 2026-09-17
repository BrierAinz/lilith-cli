"""Tests for project instructions loaded from the AGENTS.md standard."""

from pathlib import Path
from unittest.mock import patch

from lilith_cli.agent import AgentSession


def _session() -> AgentSession:
    session = AgentSession.__new__(AgentSession)
    session._project_instructions = None
    return session


def _mock_instruction_files(cwd: Path, contents: dict[Path, str]):
    return (
        patch.object(Path, "cwd", return_value=cwd),
        patch.object(Path, "home", return_value=Path("home")),
        patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path in contents),
        patch.object(
            Path,
            "read_text",
            autospec=True,
            side_effect=lambda path, encoding="utf-8": contents[path],
        ),
    )


def test_loads_parent_and_nearest_agents_in_order():
    cwd = Path("repo") / "packages" / "cli"
    root_agents = Path("repo") / "AGENTS.md"
    nearest_agents = cwd.parent / "AGENTS.md"
    contexts = _mock_instruction_files(
        cwd,
        {
            root_agents: "Reglas generales.",
            nearest_agents: "Reglas específicas del paquete.",
        },
    )

    with contexts[0], contexts[1], contexts[2], contexts[3]:
        result = _session()._load_project_instructions()
        assert result.index("Reglas generales.") < result.index("Reglas específicas del paquete.")
        assert str(root_agents) in result and str(nearest_agents) in result


def test_lilith_project_instructions_supplement_shared_rules():
    cwd = Path("repo") / "package"
    local_lilith = cwd / ".lilith" / "CLAUDE.md"
    contexts = _mock_instruction_files(
        cwd,
        {
            cwd / "AGENTS.md": "Reglas compartidas.",
            local_lilith: "Reglas específicas de Lilith.",
        },
    )

    with contexts[0], contexts[1], contexts[2], contexts[3]:
        result = _session()._load_project_instructions()
        assert result.index("Reglas compartidas.") < result.index("Reglas específicas de Lilith.")


def test_instruction_edits_are_seen_on_next_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "AGENTS.md"
    path.write_text("Primera regla", encoding="utf-8-sig")
    session = _session()
    assert "Primera regla" in session._load_project_instructions()
    path.write_text("Nueva regla", encoding="utf-8")
    result = session._load_project_instructions()
    assert "Nueva regla" in result and "Primera regla" not in result


def test_changing_project_does_not_reuse_previous_rules(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "AGENTS.md").write_text("FIRST_ONLY", encoding="utf-8")
    (second / "AGENTS.md").write_text("SECOND_ONLY", encoding="utf-8")
    session = _session()
    monkeypatch.chdir(first)
    assert "FIRST_ONLY" in session._load_project_instructions()
    monkeypatch.chdir(second)
    result = session._load_project_instructions()
    assert "SECOND_ONLY" in result and "FIRST_ONLY" not in result


def test_unreadable_instructions_do_not_silently_disappear(tmp_path, monkeypatch):
    import pytest

    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe\x80")
    with pytest.raises(RuntimeError, match="instrucciones"):
        _session()._load_project_instructions()
