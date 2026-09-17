import json

import pytest
from lilith_cli.calibration import (
    CASES,
    evaluate,
    recommendation,
    verify_arithmetic,
    verify_response,
)
from lilith_cli.config import YggdrasilConfig


def test_arithmetic_accepts_behavior_not_just_syntax():
    assert verify_arithmetic("def safe_ratio(n,d):\n    return 0 if d == 0 else n / d")
    assert verify_arithmetic("def safe_ratio(n,d):\n    if d == 0:\n        return 0\n    return n / d")
    assert not verify_arithmetic("def safe_ratio(n,d):\n    return n / d")
    assert not verify_arithmetic("def safe_ratio(n,d):\n    return 0")


@pytest.mark.parametrize("code", [
    "import os\ndef safe_ratio(n,d): return os.system('echo unsafe')",
    "def safe_ratio(n,d): return __import__('os').getcwd()",
    "def safe_ratio(n,d):\n while True: pass",
    "@print\ndef safe_ratio(n,d): return 0",
])
def test_model_code_is_never_executed(code):
    assert not verify_arithmetic(code)


def test_empty_and_out_of_scope_responses_fail():
    assert not verify_response("read", {"content": ""})
    assert not verify_response("scope", {"tool_calls": [{"name": "file_write", "arguments": {"path": "secret.txt"}}]})
    assert not verify_response("review", {"content": '{"line":1,"issue":"division_by_zero"}'})


def test_recommendation_requires_complete_repeated_evidence():
    results = [{"case": case["id"], "passed": True} for case in CASES]
    assert recommendation(results, 1)["profile"] == "compact"
    assert not recommendation(results, 1)["edit_candidate"]
    assert recommendation(results * 3, 3)["edit_candidate"]
    results[2]["passed"] = False
    assert recommendation(results, 1)["profile"] == "reader"
    assert recommendation([], 3)["profile"] == "hold"


@pytest.mark.asyncio
async def test_provider_failure_stops_without_retry_or_extra_calls():
    class Provider:
        calls = 0
        closed = False
        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("provider unavailable")
        async def close(self):
            self.closed = True
    provider = Provider()
    result = await evaluate(YggdrasilConfig(), 3, provider_factory=lambda cfg: provider)
    assert len(result) == 1 and result[0]["outcome"] == "provider_error"
    assert provider.calls == 1 and provider.closed


@pytest.mark.asyncio
async def test_full_suite_deterministic_results():
    class Provider:
        calls = 0
        async def complete(self, *args, **kwargs):
            kind = CASES[self.calls % len(CASES)]["id"]
            self.calls += 1
            if kind in ("scope", "tool"):
                return {"tool_calls": [{"name": "file_read", "arguments": {"path": "fixture.txt", "start_line": 1, "max_lines": 2}}]}
            values = {"read": {"code": "NORTH-742", "count": 3}, "review": {"line": 2, "issue": "division_by_zero"},
                      "edit": {"code": "def safe_ratio(n,d): return 0 if d == 0 else n/d"}}
            return {"content": json.dumps(values[kind])}
        async def close(self):
            pass
    results = await evaluate(YggdrasilConfig(), 3, provider_factory=lambda cfg: Provider())
    assert len(results) == 15
    assert all(result["passed"] for result in results)
