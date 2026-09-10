"""Deterministic statistics that keep missing measurements visible."""

from __future__ import annotations

import math
from typing import Any


def _valid_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def latency_summary(values: list[float]) -> dict:
    if not isinstance(values, list) or any(not _valid_number(value) for value in values):
        raise ValueError("invalid_latency")
    ordered = sorted(values)
    n = len(ordered)
    p50 = ordered[math.ceil(0.50 * n) - 1] if n else None
    p95 = ordered[math.ceil(0.95 * n) - 1] if n >= 20 else None
    return {
        "n": n,
        "p50Ms": p50,
        "p95Ms": p95,
        "status": "available" if n >= 20 else "insufficient_samples",
    }


def task_usage(calls: list[dict], *, expected_call_ids: list[str] | None) -> dict:
    if not isinstance(calls, list):
        raise ValueError("invalid_calls")
    if expected_call_ids is not None and (not isinstance(expected_call_ids, list) or len(set(expected_call_ids)) != len(expected_call_ids)):
        raise ValueError("invalid_expected_call_ids")
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    unexpected_ids: list[str] = []
    observed_tokens = 0
    measured_call_ids: set[str] = set()
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("callId"), str):
            raise ValueError("invalid_call")
        call_id = call["callId"]
        if call_id in seen:
            duplicate_ids.append(call_id)
        seen.add(call_id)
        if expected_call_ids is not None and call_id not in expected_call_ids:
            unexpected_ids.append(call_id)
        usage = call.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get("measuredInputTokens")
        output_tokens = usage.get("measuredOutputTokens")
        input_known = input_tokens is not None and _valid_number(input_tokens) and isinstance(input_tokens, int)
        output_known = output_tokens is not None and _valid_number(output_tokens) and isinstance(output_tokens, int)
        if input_known:
            observed_tokens += input_tokens
        if output_known:
            observed_tokens += output_tokens
        if input_known and output_known:
            measured_call_ids.add(call_id)
    missing = [] if expected_call_ids is None else [call_id for call_id in expected_call_ids if call_id not in seen]
    invalid = bool(duplicate_ids or unexpected_ids)
    inventory_complete = expected_call_ids is not None and not missing and not unexpected_ids and not duplicate_ids
    if expected_call_ids is None:
        total = None
        coverage = None
        status = "unknown_inventory"
    elif invalid:
        total = None
        coverage = len(measured_call_ids) / len(expected_call_ids) if expected_call_ids else None
        status = "invalid"
    elif not expected_call_ids:
        total = 0
        coverage = 1.0
        status = "available"
    elif inventory_complete and len(measured_call_ids) == len(expected_call_ids):
        total = observed_tokens
        coverage = 1.0
        status = "available"
    else:
        total = None
        coverage = len(measured_call_ids) / len(expected_call_ids)
        status = "partial"
    return {
        "taskTotalTokens": total,
        "observedTokens": observed_tokens,
        "coverage": coverage,
        "missingCallIds": missing,
        "duplicateCallIds": duplicate_ids,
        "unexpectedCallIds": unexpected_ids,
        "status": status,
    }
