"""Read-only adapters from known runtime receipts to eval observations."""

from __future__ import annotations

import copy
import math
from typing import Any

from .agent_eval_models import validate_observation, validate_usage


_NULL_USAGE = {
    "measuredInputTokens": None,
    "measuredOutputTokens": None,
    "estimatedInputTokens": None,
    "estimatedOutputTokens": None,
    "cachedInputTokens": None,
    "reasoningTokens": None,
    "estimatorVersion": None,
    "source": "missing",
    "cachedIsInputSubset": None,
    "reasoningIsOutputSubset": None,
}


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _optional_nonnegative_int(payload: dict, key: str) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    result = _nonnegative_int(value)
    if result is None:
        raise ValueError(f"invalid_{key}")
    return result


def normalize_usage(payload: dict, *, producer_id: str) -> dict:
    """Map one known producer without guessing meanings for unknown fields."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    if producer_id == "agent-runtime-legacy-v1":
        result = copy.deepcopy(_NULL_USAGE)
        result["estimatedInputTokens"] = _optional_nonnegative_int(payload, "estimatedInputTokens")
        result["estimatedOutputTokens"] = _optional_nonnegative_int(payload, "outputTokens")
        result["estimatorVersion"] = "agent-runtime-legacy-v1"
        result["source"] = "estimate"
    elif producer_id == "hermes-real-turn-v1":
        input_tokens = _nonnegative_int(payload.get("inputTokens"))
        output_tokens = _nonnegative_int(payload.get("outputTokens"))
        if input_tokens is None or output_tokens is None:
            raise ValueError("missing_usage")
        result = copy.deepcopy(_NULL_USAGE)
        result["measuredInputTokens"] = input_tokens
        result["measuredOutputTokens"] = output_tokens
        result["source"] = "provider_usage"
    else:
        raise ValueError("unknown_producer")
    return validate_usage(result)


def normalize_observation(payload: dict, *, binding: dict) -> dict:
    """Normalize a bounded legacy envelope after caller-provided binding checks."""
    if not isinstance(payload, dict) or not isinstance(binding, dict):
        raise ValueError("invalid_binding")
    producer_id = payload.get("producerId")
    if binding.get("producerId") != producer_id:
        raise ValueError("invalid_binding")
    if producer_id == "hermes-real-turn-v1":
        if "p95Ms" not in payload:
            raise ValueError("missing_latency")
        latency = payload["p95Ms"]
        if (isinstance(latency, bool) or not isinstance(latency, (int, float))
                or not math.isfinite(latency) or latency < 0 or latency > 604_800_000):
            raise ValueError("invalid_call_latency")
        return {
            "usage": normalize_usage(payload, producer_id=producer_id),
            "callLatencyMs": latency,
            "legacyField": "p95Ms",
            "producerId": producer_id,
        }
    raise ValueError("unknown_producer")
