"""Bounded, non-authoritative telemetry for acceptance gate artifacts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


TELEMETRY_KEYS = {
    "durationSeconds",
    "attemptNumber",
    "retryGroup",
    "cacheHit",
    "failureCode",
    "blockedReason",
}
_MAX_DURATION_SECONDS = 7 * 24 * 60 * 60
_MAX_TEXT_LENGTH = 128


def _validate_text(value: Any, field: str) -> None:
    if value is not None and (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_TEXT_LENGTH
        or "\n" in value
        or "\r" in value
        or value.startswith(("/", "file://"))
    ):
        raise ValueError(f"{field} is invalid")


def validate_gate_telemetry(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != TELEMETRY_KEYS:
        raise ValueError("telemetry keys are invalid")

    duration = value["durationSeconds"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("durationSeconds is invalid")
    if not math.isfinite(float(duration)) or not 0 <= float(duration) <= _MAX_DURATION_SECONDS:
        raise ValueError("durationSeconds is out of range")

    attempt = value["attemptNumber"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 128:
        raise ValueError("attemptNumber is invalid")
    if not isinstance(value["cacheHit"], bool):
        raise ValueError("cacheHit is invalid")
    _validate_text(value["retryGroup"], "retryGroup")
    _validate_text(value["failureCode"], "failureCode")
    _validate_text(value["blockedReason"], "blockedReason")


def build_gate_telemetry(
    *,
    duration_seconds: float,
    attempt_number: int = 1,
    retry_group: str | None = None,
    cache_hit: bool = False,
    failure_code: str | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    value = {
        "durationSeconds": round(float(duration_seconds), 6),
        "attemptNumber": attempt_number,
        "retryGroup": retry_group,
        "cacheHit": cache_hit,
        "failureCode": failure_code,
        "blockedReason": blocked_reason,
    }
    validate_gate_telemetry(value)
    return value
