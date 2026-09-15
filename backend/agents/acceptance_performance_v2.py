"""Versioned diagnostic performance evidence with safe cross-commit comparison."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from backend.agents.acceptance_performance import (
    COUNT_KEYS,
    MAX_COUNT,
    MAX_DURATION_SECONDS,
    SELECTION_MODES,
    STAGE_KEYS,
    _counts as _validate_counts,
    _duration,
)
from backend.agents.evidence_models import canonical_fingerprint


SCHEMA_VERSION = "acceptance-performance-baseline-v2"
AUTHORITY = "diagnostic"
FULL_GATE_REQUIRED = True
BASELINE_ROLES = frozenset({"serial_control", "parallel_candidate", "fast_diagnostic"})
CANDIDATE_ROLES = frozenset({"parallel_candidate", "fast_diagnostic"})
LIFECYCLES = frozenset({"draft", "qualified", "retired", "invalidated"})
COMPARABILITY_FIELDS = (
    "baselineFamilyId",
    "contractFingerprint",
    "manifestFingerprint",
    "testPopulationFingerprint",
    "runnerFingerprint",
    "environmentFingerprint",
    "datasetSnapshotFingerprint",
    "selectionMode",
)
COMPARISON_STATUSES = frozenset({"not_compared", "compared", "ineligible"})
COMPARISON_REASONS = frozenset(
    {
        "baseline_not_qualified",
        "candidate_not_qualified",
        "baseline_role_invalid",
        "candidate_role_invalid",
        "baseline_family_mismatch",
        "selection_mode_mismatch",
        "comparison_not_requested",
        "contract_mismatch",
        "manifest_mismatch",
        "population_mismatch",
        "runner_mismatch",
        "environment_mismatch",
        "dataset_mismatch",
        "baseline_total_non_positive",
    }
)
COMPARISON_KEYS = frozenset(
    {
        "status",
        "reason",
        "baselineFingerprint",
        "candidateFingerprint",
        "stageDeltasSeconds",
        "totalSpeedRatio",
        "cacheHit",
        "cacheMiss",
        "failureRate",
    }
)
V2_KEYS = frozenset(
    {
        "schemaVersion",
        "authority",
        "fullGateRequired",
        "baselineFamilyId",
        "baselineRole",
        "lifecycle",
        "contractFingerprint",
        "commitSha",
        "sourceFingerprint",
        "manifestFingerprint",
        "testPopulationFingerprint",
        "runnerFingerprint",
        "environmentFingerprint",
        "datasetSnapshotFingerprint",
        "selectionMode",
        "stages",
        "testCount",
        "comparison",
        "evidenceFingerprint",
    }
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _require_sha(value: Any, pattern: re.Pattern[str], field: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _require_identifier(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _unsigned(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in payload if key != "evidenceFingerprint"}


def _normalize_comparison(value: Any) -> dict[str, Any]:
    if value is None:
        return {"status": "not_compared"}
    if not isinstance(value, Mapping):
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
        if reason not in COMPARISON_REASONS:
            raise ValueError("comparison reason is invalid")
        result["reason"] = reason
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
        result["totalSpeedRatio"] = _duration(
            value["totalSpeedRatio"], "comparison.totalSpeedRatio"
        )
    for key in ("cacheHit", "cacheMiss"):
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"comparison.{key} is invalid")
            result[key] = value[key]
    if "failureRate" in value:
        failure_rate = value["failureRate"]
        if isinstance(failure_rate, bool) or not isinstance(failure_rate, (int, float)):
            raise ValueError("comparison.failureRate is invalid")
        if not 0 <= float(failure_rate) <= 1:
            raise ValueError("comparison.failureRate is out of range")
        result["failureRate"] = round(float(failure_rate), 6)

    has_fingerprints = {
        "baselineFingerprint", "candidateFingerprint"
    } <= set(result)
    if status == "compared":
        required = {
            "baselineFingerprint",
            "candidateFingerprint",
            "stageDeltasSeconds",
            "totalSpeedRatio",
        }
        if not required <= set(result) or "reason" in result:
            raise ValueError("compared comparison fields are incomplete")
    elif status == "not_compared":
        # A newly-created baseline has no candidate yet and is represented by
        # the minimal status-only marker shown in the v2 artifact contract.
        if set(value) != {"status"}:
            if "reason" not in result or not has_fingerprints:
                raise ValueError("not_compared comparison fields are incomplete")
            if "totalSpeedRatio" in result:
                raise ValueError("not_compared comparison cannot contain a ratio")
    else:
        if "reason" not in result or not has_fingerprints:
            raise ValueError("ineligible comparison fields are incomplete")
        if "totalSpeedRatio" in result:
            raise ValueError("ineligible comparison cannot contain a ratio")
    return result


def _validate_unsigned(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping) or set(payload) != V2_KEYS - {"evidenceFingerprint"}:
        raise ValueError("performance baseline v2 schema is invalid")
    if payload["schemaVersion"] != SCHEMA_VERSION or payload["authority"] != AUTHORITY:
        raise ValueError("performance baseline v2 authority is invalid")
    if payload["fullGateRequired"] is not True:
        raise ValueError("fullGateRequired must remain true")
    _require_identifier(payload["baselineFamilyId"], "baselineFamilyId")
    if payload["baselineRole"] not in BASELINE_ROLES:
        raise ValueError("baselineRole is invalid")
    if payload["lifecycle"] not in LIFECYCLES:
        raise ValueError("lifecycle is invalid")
    _require_sha(payload["contractFingerprint"], _SHA64, "contractFingerprint")
    _require_sha(payload["commitSha"], _SHA40, "commitSha")
    for field in (
        "sourceFingerprint",
        "manifestFingerprint",
        "testPopulationFingerprint",
        "runnerFingerprint",
        "environmentFingerprint",
        "datasetSnapshotFingerprint",
    ):
        _require_sha(payload[field], _SHA64, field)
    if payload["selectionMode"] not in SELECTION_MODES:
        raise ValueError("selectionMode is invalid")
    stages = payload["stages"]
    if not isinstance(stages, Mapping) or set(stages) != set(STAGE_KEYS):
        raise ValueError("stages keys are invalid")
    for key in STAGE_KEYS:
        _duration(stages[key], key)
    _validate_counts(payload["testCount"])
    _normalize_comparison(payload["comparison"])


def build_performance_baseline_v2(
    *,
    baseline_family_id: str,
    baseline_role: str,
    lifecycle: str,
    contract_fingerprint: str,
    commit_sha: str,
    source_fingerprint: str,
    manifest_fingerprint: str,
    test_population_fingerprint: str,
    runner_fingerprint: str,
    environment_fingerprint: str,
    dataset_snapshot_fingerprint: str,
    selection_mode: str,
    stages: Mapping[str, float],
    test_count: Mapping[str, int],
    comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build bounded v2 diagnostic evidence without side effects."""
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "fullGateRequired": FULL_GATE_REQUIRED,
        "baselineFamilyId": baseline_family_id,
        "baselineRole": baseline_role,
        "lifecycle": lifecycle,
        "contractFingerprint": contract_fingerprint,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "testPopulationFingerprint": test_population_fingerprint,
        "runnerFingerprint": runner_fingerprint,
        "environmentFingerprint": environment_fingerprint,
        "datasetSnapshotFingerprint": dataset_snapshot_fingerprint,
        "selectionMode": selection_mode,
        "stages": {
            key: _duration(stages[key], key) for key in STAGE_KEYS
        } if isinstance(stages, Mapping) and set(stages) == set(STAGE_KEYS) else stages,
        "testCount": _validate_counts(test_count),
        "comparison": _normalize_comparison(comparison),
    }
    _validate_unsigned(payload)
    return {**payload, "evidenceFingerprint": canonical_fingerprint(payload)}


def validate_performance_baseline_v2(payload: Mapping[str, Any]) -> None:
    """Validate exact v2 schema and canonical evidence identity."""
    if not isinstance(payload, Mapping) or set(payload) != V2_KEYS:
        raise ValueError("performance baseline v2 schema is invalid")
    _validate_unsigned(_unsigned(payload))
    _require_sha(payload["evidenceFingerprint"], _SHA64, "evidenceFingerprint")
    if canonical_fingerprint(_unsigned(payload)) != payload["evidenceFingerprint"]:
        raise ValueError("performance baseline v2 evidence fingerprint mismatch")


def build_comparability_key(payload: Mapping[str, Any]) -> str:
    """Return the canonical key that defines a comparable experiment."""
    validate_performance_baseline_v2(payload)
    return canonical_fingerprint({field: payload[field] for field in COMPARABILITY_FIELDS})


def _comparison_metrics(candidate: Mapping[str, Any]) -> dict[str, Any]:
    candidate_comparison = candidate["comparison"]
    collected = candidate["testCount"]["collected"]
    failure_rate = min(1.0, candidate["testCount"]["failed"] / max(collected, 1))
    return {
        "cacheHit": candidate_comparison.get("cacheHit", False),
        "cacheMiss": candidate_comparison.get("cacheMiss", False),
        "failureRate": round(failure_rate, 6),
    }


def _not_compared(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    return {
        "status": "not_compared",
        "reason": reason,
        "baselineFingerprint": baseline["evidenceFingerprint"],
        "candidateFingerprint": candidate["evidenceFingerprint"],
    }


def compare_performance_baselines_v2(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare v2 diagnostics only when their semantic lineage is identical."""
    validate_performance_baseline_v2(baseline)
    validate_performance_baseline_v2(candidate)
    if baseline["lifecycle"] != "qualified":
        return _not_compared(baseline, candidate, "baseline_not_qualified")
    if candidate["lifecycle"] != "qualified":
        return _not_compared(baseline, candidate, "candidate_not_qualified")
    if baseline["baselineRole"] != "serial_control":
        return _not_compared(baseline, candidate, "baseline_role_invalid")
    if candidate["baselineRole"] not in CANDIDATE_ROLES:
        return _not_compared(baseline, candidate, "candidate_role_invalid")

    mismatch_reasons = {
        "baselineFamilyId": "baseline_family_mismatch",
        "contractFingerprint": "contract_mismatch",
        "manifestFingerprint": "manifest_mismatch",
        "testPopulationFingerprint": "population_mismatch",
        "runnerFingerprint": "runner_mismatch",
        "environmentFingerprint": "environment_mismatch",
        "datasetSnapshotFingerprint": "dataset_mismatch",
        "selectionMode": "selection_mode_mismatch",
    }
    for field in COMPARABILITY_FIELDS:
        if baseline[field] != candidate[field]:
            return _not_compared(baseline, candidate, mismatch_reasons[field])
    stage_deltas = {
        key: round(float(candidate["stages"][key]) - float(baseline["stages"][key]), 6)
        for key in STAGE_KEYS
    }
    metrics = _comparison_metrics(candidate)
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
        "totalSpeedRatio": round(
            float(candidate["stages"]["totalWallSeconds"]) / baseline_total, 6
        ),
        **metrics,
    }
