import pytest
from lilith_cli.delegation_keys import automatic_request_id, validate_namespace


def test_identical_arguments_ignore_json_key_order():
    first = automatic_request_id(
        "a" * 32, "vor_delegate", {"task": "review", "timeout": 5}, "D:/project"
    )
    second = automatic_request_id(
        "a" * 32, "vor_delegate", {"timeout": 5, "task": "review"}, "D:/project"
    )
    assert first == second


@pytest.mark.parametrize(
    "namespace,tool,args,cwd",
    [
        ("b" * 32, "vor_delegate", {"task": "review"}, "D:/project"),
        ("a" * 32, "huginn_delegate", {"task": "review"}, "D:/project"),
        ("a" * 32, "vor_delegate", {"task": "different"}, "D:/project"),
        ("a" * 32, "vor_delegate", {"task": "review"}, "D:/other"),
    ],
)
def test_changed_intent_or_conversation_has_distinct_key(namespace, tool, args, cwd):
    baseline = automatic_request_id(
        "a" * 32, "vor_delegate", {"task": "review"}, "D:/project"
    )
    assert automatic_request_id(namespace, tool, args, cwd) != baseline


@pytest.mark.parametrize("namespace", [True, [], "bad", "A" * 32])
def test_invalid_namespace_rejected(namespace):
    with pytest.raises(ValueError):
        validate_namespace(namespace)


def test_missing_namespace_cannot_generate_global_key():
    with pytest.raises(ValueError):
        automatic_request_id(None, "vor_delegate", {}, "D:/project")
