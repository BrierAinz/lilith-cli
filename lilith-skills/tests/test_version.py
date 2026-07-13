"""Version + public-API import tests for lilith_skills."""

from __future__ import annotations

import lilith_skills
import lilith_skills.cross_context as cc


def test_version_is_1_6_0():
    """lilith_skills 1.6.0 ships card_validator + the cross-cutting context facade."""
    assert lilith_skills.__version__ == "1.6.0"


def test_cross_context_is_exported():
    for name in [
        "CrossContext",
        "Goal",
        "GoalTurn",
        "GoalGate",
        "GoalsStore",
        "HandoffsStore",
        "AuditEvent",
        "AuditLog",
        "PolicyRule",
        "PolicySet",
        "PoliciesStore",
        "Workflow",
        "WorkflowStep",
        "WorkflowsStore",
    ]:
        assert hasattr(lilith_skills, name), f"missing export: {name}"


def test_cross_context_module_all_is_complete():
    """`__all__` should expose every public class in the module."""
    for cls in (cc.CrossContext, cc.Goal, cc.AuditLog, cc.PoliciesStore, cc.WorkflowsStore):
        assert cls.__name__ in cc.__all__, f"{cls.__name__} not in __all__"
