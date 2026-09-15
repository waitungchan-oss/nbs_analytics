"""Bounded, source-bound performance evidence for acceptance diagnostics."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from backend.agents.evidence_models import canonical_fingerprint


SCHEMA_VERSION = "acceptance-performance-baseline-v1"
AUTHORITY = "diagnostic"
FULL_GATE_REQUIRED = True
SELECTION_MODES = frozenset({"full", "fast", "shard"})
STAGE_KEYS = (
    "collectionSeconds",
    "fixturePreparationSeconds",
    "pytestExecutionSeconds",
    "aggregateSeconds",
    "totalWallSeconds",
)
COUNT_KEYS = ("collected", "passed", "failed", "skipped")
COMPARISON_KEYS = frozenset(
    {
        "baselineArtifact",
        "status",
        "stageDeltasSeconds",
        "totalSpeedRatio",
        "baselineFingerprint",
        "candidateFingerprint",
        "cacheHit",
        "cacheMiss",
        "failureRate",
        "reason",
    }
)
COMPARISON_STATUSES = frozenset({"not_compared", "compared", "ineligible"})
MAX_DURATION_SECONDS = 7 * 24 * 60 * 60
MAX_COUNT = 10_000_000
MAX_COMPARISON_KEYS = 16
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _require_sha(value: Any, pattern: re.Pattern[str], field: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _duration(value: Any, field: str, *, allow_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} duration is invalid")
    result = float(value)
    lower_bound = -MAX_DURATION_SECONDS if allow_negative else 0.0
    if not math.isfinite(result) or not lower_bound <= result <= MAX_DURATION_SECONDS:
        raise ValueError(f"{field} duration is out of range")
    return round(result, 6)


def _counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(COUNT_KEYS):
        raise ValueError("testCount keys are invalid")
    result: dict[str, int] = {}
    for key in COUNT_KEYS:
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_COUNT:
            raise ValueError(f"testCount.{key} is invalid")
        result[key] = item
    if sum(result[key] for key in ("passed", "failed", "skipped")) > result["collected"]:
        raise ValueError("test counts exceed collected")
    return result


def _comparison(value: Any) -> dict[str, Any]:
    if value is None:
        return {"baselineArtifact": None, "status": "not_compared"}
    if not isinstance(value, Mapping) or len(value) > MAX_COMPARISON_KEYS:
        raise ValueError("comparison is invalid")
    unknown = set(value) - COMPARISON_KEYS
    if unknown or "status" not in value:
        raise ValueError("comparison keys are invalid")
    status = value["status"]
    if not isinstance(status, str) or status not in COMPARISON_STATUSES:
        raise ValueError("comparison status is invalid")

    result: dict[str, Any] = {"status": status}
    if "reason" in value:
        reason = value["reason"]
        if (
            not isinstance(reason, str)
            or not 1 <= len(reason) <= 128
            or not re.fullmatch(r"[a-z0-9_]+", reason)
        ):
            raise ValueError("comparison.reason is invalid")
        result["reason"] = reason
    baseline_artifact = value.get("baselineArtifact")
    if baseline_artifact is not None:
        _require_sha(baseline_artifact, _SHA64, "comparison.baselineArtifact")
        result["baselineArtifact"] = baseline_artifact
    else:
        result["baselineArtifact"] = None

    for key in ("baselineFingerprint", "candidateFingerprint"):
        if key in value:
            _require_sha(value[key], _SHA64, f"comparison.{key}")
            result[key] = value[key]

    if "stageDeltasSeconds" in value:
        deltas = value["stageDeltasSeconds"]
        if not isinstance(deltas, Mapping) or set(deltas) != set(STAGE_KEYS):
            raise ValueError("comparison stage deltas are invalid")
        result["stageDeltasSeconds"] = {
            key: _duration(deltas[key], f"comparison.{key}", allow_negative=True)
            for key in STAGE_KEYS
        }

    if "totalSpeedRatio" in value:
        result["totalSpeedRatio"] = _duration(value["totalSpeedRatio"], "comparison.totalSpeedRatio")
    for key in ("cacheHit", "cacheMiss"):
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"comparison.{key} is invalid")
            result[key] = value[key]
    if "failureRate" in value:
        failure_rate = value["failureRate"]
        if isinstance(failure_rate, bool) or not isinstance(failure_rate, (int, float)):
            raise ValueError("comparison.failureRate is invalid")
        if not math.isfinite(float(failure_rate)) or not 0 <= float(failure_rate) <= 1:
            raise ValueError("comparison.failureRate is out of range")
        result["failureRate"] = round(float(failure_rate), 6)
    return result


def _unsigned(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in payload if key != "evidenceFingerprint"}


def build_performance_baseline(
    *,
    commit_sha: str,
    source_fingerprint: str,
    runner_fingerprint: str,
    stages: Mapping[str, float],
    test_count: Mapping[str, int],
    selection_mode: str,
    comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded diagnostic performance artifact without side effects."""
    _require_sha(commit_sha, _SHA40, "commitSha")
    _require_sha(source_fingerprint, _SHA64, "sourceFingerprint")
    _require_sha(runner_fingerprint, _SHA64, "runnerFingerprint")
    if not isinstance(selection_mode, str) or selection_mode not in SELECTION_MODES:
        raise ValueError("selectionMode is invalid")
    if not isinstance(stages, Mapping) or set(stages) != set(STAGE_KEYS):
        raise ValueError("stages keys are invalid")
    normalized_stages = {key: _duration(stages[key], key) for key in STAGE_KEYS}
    normalized_counts = _counts(test_count)
    normalized_comparison = _comparison(comparison)
    unsigned = {
        "schemaVersion": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "fullGateRequired": FULL_GATE_REQUIRED,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "runnerFingerprint": runner_fingerprint,
        "selectionMode": selection_mode,
        "stages": normalized_stages,
        "testCount": normalized_counts,
        "comparison": normalized_comparison,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def validate_performance_baseline(payload: Mapping[str, Any]) -> None:
    """Strictly validate diagnostic performance evidence."""
    required = {
        "schemaVersion",
        "authority",
        "fullGateRequired",
        "commitSha",
        "sourceFingerprint",
        "runnerFingerprint",
        "selectionMode",
        "stages",
        "testCount",
        "comparison",
        "evidenceFingerprint",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise ValueError("performance baseline schema is invalid")
    if payload["schemaVersion"] != SCHEMA_VERSION or payload["authority"] != AUTHORITY:
        raise ValueError("performance baseline authority is invalid")
    if payload["fullGateRequired"] is not True:
        raise ValueError("fullGateRequired must remain true")
    _require_sha(payload["commitSha"], _SHA40, "commitSha")
    _require_sha(payload["sourceFingerprint"], _SHA64, "sourceFingerprint")
    _require_sha(payload["runnerFingerprint"], _SHA64, "runnerFingerprint")
    if not isinstance(payload["selectionMode"], str) or payload["selectionMode"] not in SELECTION_MODES:
        raise ValueError("selectionMode is invalid")
    stages = payload["stages"]
    if not isinstance(stages, Mapping) or set(stages) != set(STAGE_KEYS):
        raise ValueError("stages keys are invalid")
    for key in STAGE_KEYS:
        _duration(stages[key], key)
    _counts(payload["testCount"])
    _comparison(payload["comparison"])
    _require_sha(payload["evidenceFingerprint"], _SHA64, "evidenceFingerprint")
    if canonical_fingerprint(_unsigned(payload)) != payload["evidenceFingerprint"]:
        raise ValueError("evidence fingerprint mismatch")


def compare_performance_baselines(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare same-runner, same-source diagnostic artifacts without mutation."""
    validate_performance_baseline(baseline)
    validate_performance_baseline(candidate)
    if baseline["runnerFingerprint"] != candidate["runnerFingerprint"]:
        raise ValueError("runner fingerprint mismatch")
    if baseline["sourceFingerprint"] != candidate["sourceFingerprint"]:
        raise ValueError("source fingerprint mismatch")
    if baseline["selectionMode"] != candidate["selectionMode"]:
        raise ValueError("selection mode mismatch")
    if baseline["commitSha"] != candidate["commitSha"]:
        raise ValueError("commit identity mismatch")

    baseline_count = baseline["testCount"]["collected"]
    candidate_count = candidate["testCount"]["collected"]
    if baseline_count != candidate_count:
        candidate_comparison = candidate["comparison"]
        failure_rate = min(1.0, candidate["testCount"]["failed"] / max(candidate_count, 1))
        return {
            "status": "ineligible",
            "reason": "test_population_mismatch",
            "baselineFingerprint": baseline["evidenceFingerprint"],
            "candidateFingerprint": candidate["evidenceFingerprint"],
            "cacheHit": candidate_comparison.get("cacheHit", False),
            "cacheMiss": candidate_comparison.get("cacheMiss", False),
            "failureRate": round(failure_rate, 6),
        }

    stage_deltas = {
        key: round(float(candidate["stages"][key]) - float(baseline["stages"][key]), 6)
        for key in STAGE_KEYS
    }
    candidate_comparison = candidate["comparison"]
    collected = candidate["testCount"]["collected"]
    failure_rate = min(1.0, candidate["testCount"]["failed"] / max(collected, 1))
    metrics = {
        "cacheHit": candidate_comparison.get("cacheHit", False),
        "cacheMiss": candidate_comparison.get("cacheMiss", False),
        "failureRate": round(failure_rate, 6),
    }
    baseline_total = float(baseline["stages"]["totalWallSeconds"])
    if baseline_total <= 0:
        return {
            "status": "not_compared",
            "reason": "baseline_total_non_positive",
            "baselineFingerprint": baseline["evidenceFingerprint"],
            "candidateFingerprint": candidate["evidenceFingerprint"],
            "stageDeltasSeconds": stage_deltas,
            **metrics,
        }
    return {
        "status": "compared",
        "baselineFingerprint": baseline["evidenceFingerprint"],
        "candidateFingerprint": candidate["evidenceFingerprint"],
        "stageDeltasSeconds": stage_deltas,
        "totalSpeedRatio": round(float(candidate["stages"]["totalWallSeconds"]) / baseline_total, 6),
        **metrics,
    }
