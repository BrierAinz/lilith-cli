from __future__ import annotations

from lilith_cli.agent import (
    _DEFAULT_HISTORY_CHAR_BUDGET,
    _DEFAULT_TOOL_RESULT_CHAR_CAP,
    _drop_orphan_tool_messages,
    _shrink_tool_result,
    _trim_history_to_budget,
)


def test_default_context_limits_are_the_expected_values():
    assert _DEFAULT_HISTORY_CHAR_BUDGET == 120_000
    assert _DEFAULT_TOOL_RESULT_CHAR_CAP == 8_000


def test_history_below_budget_is_not_modified():
    history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "que tal"},
    ]

    assert _trim_history_to_budget(history, 100, 20) == history


def test_long_tool_result_keeps_both_ends_and_omission_marker():
    text = "A" * 40 + "B" * 40

    shrunk = _shrink_tool_result(text, 50)

    assert len(shrunk) == 50
    assert shrunk.startswith("A")
    assert shrunk.endswith("B")
    assert "omit" in shrunk.lower()


def test_history_over_budget_drops_messages_from_the_start():
    history = [
        {"role": "user", "content": "a" * 20},
        {"role": "assistant", "content": "b" * 20},
        {"role": "user", "content": "c" * 20},
    ]

    assert _trim_history_to_budget(history, 40, 100) == history[1:]


def test_last_message_is_never_dropped_even_if_it_exceeds_budget():
    last = {"role": "user", "content": "x" * 200}
    history = [{"role": "assistant", "content": "old"}, last]

    assert _trim_history_to_budget(history, 10, 100) == [last]


def test_trimming_then_orphan_cleanup_keeps_only_complete_tool_groups():
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call-1", "type": "function", "function": {}},
            {"id": "call-2", "type": "function", "function": {}},
        ],
    }
    history = [
        {"role": "user", "content": "old" * 20},
        assistant,
        {"role": "tool", "tool_call_id": "call-1", "content": "one"},
        {"role": "tool", "tool_call_id": "call-2", "content": "two"},
        {"role": "user", "content": "latest"},
    ]

    trimmed = _trim_history_to_budget(history, 16, 100)
    cleaned = _drop_orphan_tool_messages(trimmed)

    declared = {
        call["id"]
        for message in cleaned
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", [])
    }
    results = {
        message["tool_call_id"]
        for message in cleaned
        if message.get("role") == "tool"
    }
    assert results == declared
    assert all(
        {call["id"] for call in message.get("tool_calls", [])} <= results
        for message in cleaned
        if message.get("role") == "assistant" and message.get("tool_calls")
    )


def test_non_string_tool_content_does_not_raise():
    history = [{"role": "tool", "tool_call_id": "call-1", "content": None}]

    assert _shrink_tool_result(None, 10) is None
    assert _trim_history_to_budget(history, 10, 5) == history
