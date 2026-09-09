from __future__ import annotations

import math

import pytest

from backend.agents.agent_eval_statistics import latency_summary, task_usage


def _call(call_id: str, input_tokens: int | None, output_tokens: int | None) -> dict:
    return {"callId": call_id, "usage": {"measuredInputTokens": input_tokens, "measuredOutputTokens": output_tokens}}


def test_p95_needs_twenty_samples():
    assert latency_summary([1.0] * 19)["p95Ms"] is None
    assert latency_summary(list(range(1, 21)))["p95Ms"] == 19


def test_unknown_call_inventory_is_not_complete():
    result = task_usage([], expected_call_ids=None)
    assert result["taskTotalTokens"] is None
    assert result["coverage"] is None


def test_missing_call_is_visible():
    result = task_usage([], expected_call_ids=["call-1"])
    assert result["missingCallIds"] == ["call-1"]
    assert result["taskTotalTokens"] is None


def test_zero_is_measured_and_partial_usage_is_not_total():
    result = task_usage([_call("call-1", 0, 4), _call("call-2", 2, None)], expected_call_ids=["call-1", "call-2"])
    assert result["observedTokens"] == 6
    assert result["taskTotalTokens"] is None
    assert result["status"] == "partial"


def test_duplicate_and_unexpected_calls_are_invalid_not_deduplicated():
    result = task_usage([_call("call-1", 1, 1), _call("call-1", 1, 1), _call("other", 1, 1)], expected_call_ids=["call-1"])
    assert result["status"] == "invalid"
    assert result["duplicateCallIds"] == ["call-1"]
    assert result["unexpectedCallIds"] == ["other"]
    assert result["taskTotalTokens"] is None


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf")])
def test_latency_rejects_nonfinite_bool_and_negative_values(bad):
    with pytest.raises(ValueError, match="invalid_latency"):
        latency_summary([bad])
