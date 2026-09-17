from lilith_cli.mission.authority import AuthorityPolicy


def test_d_drive_work_is_autonomous() -> None:
    decision = AuthorityPolicy().evaluate(
        "file_edit", path=r"D:\workspace\demo\file.py"
    )
    assert decision.allowed
    assert not decision.requires_operator
    assert not decision.requires_elevation
    assert decision.domain == "crown"


def test_c_drive_admin_work_is_autonomous_but_elevated() -> None:
    decision = AuthorityPolicy().evaluate(
        "service_change", path=r"C:\ProgramData\Demo"
    )
    assert decision.allowed
    assert not decision.requires_operator
    assert decision.requires_elevation
    assert decision.domain == "system"


def test_reversible_delete_creates_recovery_before_retry() -> None:
    policy = AuthorityPolicy()
    blocked = policy.evaluate("delete", path=r"D:\workspace\demo")
    assert not blocked.allowed
    assert not blocked.requires_operator
    assert blocked.preferred_recovery == "trash_or_git_checkpoint"

    allowed = policy.evaluate(
        "delete", path=r"D:\workspace\demo", recovery_available=True
    )
    assert allowed.allowed
    assert not allowed.requires_operator


def test_external_and_irreversible_actions_escalate() -> None:
    policy = AuthorityPolicy()
    external = policy.evaluate("purchase")
    irreversible = policy.evaluate("format", path="D:\\")
    assert external.requires_operator and not external.allowed
    assert irreversible.requires_operator and not irreversible.allowed


def test_other_drive_is_outside_sovereign_scope() -> None:
    decision = AuthorityPolicy().evaluate("file_edit", path=r"E:\Other\file.txt")
    assert not decision.allowed
    assert decision.requires_operator
    assert decision.domain == "outside"
