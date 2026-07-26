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


def test_loads_nearest_agents_md_from_parent_tree():
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
        assert _session()._load_project_instructions() == "Reglas específicas del paquete."


def test_lilith_project_instructions_take_priority_over_agents_md():
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
        assert _session()._load_project_instructions() == "Reglas específicas de Lilith."