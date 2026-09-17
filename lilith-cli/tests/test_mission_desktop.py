from pathlib import Path
from types import SimpleNamespace

import pytest
from lilith_cli.mission.desktop import (
    AdminCommandBroker,
    DesktopActor,
    _effect_decision,
)


def test_external_desktop_effects_are_blocked_before_input() -> None:
    decision = _effect_decision("publish")
    assert decision["allowed"] is False
    assert decision["requires_operator"] is True
    with pytest.raises(PermissionError):
        DesktopActor.click(10, 10, effect="publish")


def test_admin_command_inference_upgrades_declared_risk() -> None:
    assert AdminCommandBroker.infer_action(
        "Set-ItemProperty HKLM:\\Software -Name X -Value 1", "local_command"
    ) == "registry_write"
    assert AdminCommandBroker.infer_action(
        "winget install Example.App", "local_command"
    ) == "system_install"


def test_destructive_command_cannot_hide_as_local_command(tmp_path: Path) -> None:
    result = AdminCommandBroker.run(
        "Format-Volume -DriveLetter Z",
        action="local_command",
        cwd=str(tmp_path),
    )
    assert result["executed"] is False
    assert result["effective_action"] == "wipe_disk"
    assert result["authority"]["requires_operator"] is True


def test_local_admin_command_reports_effective_action(monkeypatch, tmp_path: Path) -> None:
    from lilith_cli.mission import desktop

    monkeypatch.setattr(desktop, "_admin_token", lambda: True)
    monkeypatch.setattr(
        desktop.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="OK\n", stderr=""),
    )
    result = AdminCommandBroker.run(
        "Write-Output OK",
        action="local_command",
        cwd=str(tmp_path),
    )
    assert result["executed"] is True
    assert result["effective_action"] == "local_command"
    assert result["exit_code"] == 0
