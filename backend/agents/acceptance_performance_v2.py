"""Versioned diagnostic performance evidence with safe cross-commit comparison."""

from __future__ import annotations

import re
import math
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
        "candidate_total_non_positive",
        "speedup_metric_precision_lost",
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
        "totalSpeedupMultiple",
        "cacheHit",
        "cacheMiss",
        "failureRate",
    }
)
# Duration stages are rounded to six decimal places by the shared contract;
# one microsecond is therefore the smallest positive total eligible for a ratio.
MIN_POSITIVE_DURATION_SECONDS = 0.000001
MAX_SPEEDUP_METRIC = MAX_DURATION_SECONDS / MIN_POSITIVE_DURATION_SECONDS
SPEEDUP_ROUNDING_HALF_UNIT = 0.5e-6
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
        result["totalSpeedRatio"] = _speedup_metric(
            value["totalSpeedRatio"], "comparison.totalSpeedRatio"
        )
    if "totalSpeedupMultiple" in value:
        result["totalSpeedupMultiple"] = _speedup_metric(
            value["totalSpeedupMultiple"], "comparison.totalSpeedupMultiple"
        )
    if {"totalSpeedRatio", "totalSpeedupMultiple"} <= set(result):
        ratio = result["totalSpeedRatio"]
        if ratio <= 0:
            raise ValueError("comparison.totalSpeedRatio must be positive when speedup is supplied")
        if not _rounded_reciprocals_are_consistent(
            ratio, result["totalSpeedupMultiple"]
        ):
            raise ValueError("comparison speedup multiple is inconsistent with total speed ratio")
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
            "totalSpeedupMultiple",
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
            if "totalSpeedupMultiple" in result:
                raise ValueError("not_compared comparison cannot contain a speedup multiple")
    else:
        if "reason" not in result or not has_fingerprints:
            raise ValueError("ineligible comparison fields are incomplete")
        if "totalSpeedRatio" in result:
            raise ValueError("ineligible comparison cannot contain a ratio")
        if "totalSpeedupMultiple" in result:
            raise ValueError("ineligible comparison cannot contain a speedup multiple")
    return result


def _speedup_metric(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} speedup metric is invalid")
    result = float(value)
    if not math.isfinite(result) or not 0.0 < result <= MAX_SPEEDUP_METRIC:
        raise ValueError(f"{field} speedup metric is out of range")
    return result


def _rounded_reciprocals_are_consistent(ratio: float, multiple: float) -> bool:
    if abs((ratio * multiple) - 1.0) <= 1e-12:
        return True
    half_unit = SPEEDUP_ROUNDING_HALF_UNIT
    ratio_low = max(0.0, ratio - half_unit)
    ratio_high = ratio + half_unit
    multiple_low = max(0.0, multiple - half_unit)
    multiple_high = multiple + half_unit
    reciprocal_low = 1.0 / ratio_high
    reciprocal_high = math.inf if ratio_low == 0.0 else 1.0 / ratio_low
    return multiple_high >= reciprocal_low and multiple_low <= reciprocal_high


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
    candidate_total = float(candidate["stages"]["totalWallSeconds"])
    if candidate_total <= 0:
        return _not_compared(baseline, candidate, "candidate_total_non_positive")
    raw_total_speed_ratio = round(candidate_total / baseline_total, 6)
    raw_total_speedup_multiple = round(baseline_total / candidate_total, 6)
    if raw_total_speed_ratio <= 0 or raw_total_speedup_multiple <= 0:
        return _not_compared(baseline, candidate, "speedup_metric_precision_lost")
    comparison = {
        "status": "compared",
        "baselineFingerprint": baseline["evidenceFingerprint"],
        "candidateFingerprint": candidate["evidenceFingerprint"],
        "stageDeltasSeconds": stage_deltas,
        "totalSpeedRatio": raw_total_speed_ratio,
        **metrics,
    }
    if raw_total_speed_ratio > 0:
        comparison["totalSpeedupMultiple"] = raw_total_speedup_multiple
    return comparison


# Shared measured-evidence validation; rollout policy depends on these predicates.
POPULATION_KIND = "full-pytest-nodeid"
LINEAGE_FIELDS = ("contractFingerprint", "baselineFamilyId", "manifestFingerprint", "testPopulationFingerprint", "runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint", "selectionMode")
FINGERPRINT_FIELDS = (
    "serialArtifactFingerprint", "parallelArtifactFingerprint", "shardAggregateFingerprint",
    "serialRuntimeArtifactFingerprint", "parallelRuntimeArtifactFingerprint",
)


def _valid_failure_code(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _is_duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 < numeric <= MAX_DURATION_SECONDS


def _is_speedup_metric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 < numeric <= MAX_SPEEDUP_METRIC


def _is_sha(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _safe_counts(value):
    if not isinstance(value, Mapping):
        return None
    counts = {}
    for field in ("passed", "failed", "skipped"):
        item = value.get(field, 0)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_COUNT:
            return None
        counts[field] = item
    return counts


def _valid_evidence_fingerprint(value):
    fingerprint = value.get("evidenceFingerprint")
    unsigned = {key: item for key, item in value.items() if key != "evidenceFingerprint"}
    return _is_sha(fingerprint, _SHA64) and canonical_fingerprint(unsigned) == fingerprint


def _all_sha(value, fields):
    return all(_is_sha(value.get(field), _SHA64) for field in fields)


def _invalid_run(code=None):
    raise ValueError("measured run is invalid" + (f": {code}" if code else ""))


_UNOBSERVED_RUNTIME_LINEAGE_KEYS = frozenset({
    "observation", "failureCode", "commitSha", "sourceFingerprint",
})


def build_unobserved_runtime_lineage(*, failure_code, commit_sha, source_fingerprint):
    """Describe a blocked stage with no observed runtime lineage, bound to its source."""
    if not _valid_failure_code(failure_code) or not _is_sha(commit_sha, _SHA40) or not _is_sha(source_fingerprint, _SHA64):
        raise ValueError("unobserved runtime lineage identity is invalid")
    return {
        "observation": "unobserved",
        "failureCode": failure_code,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
    }


def _is_unobserved_runtime_lineage(value, run):
    return (
        isinstance(value, Mapping)
        and set(value) == _UNOBSERVED_RUNTIME_LINEAGE_KEYS
        and value.get("observation") == "unobserved"
        and _valid_failure_code(value.get("failureCode"))
        and value.get("failureCode") == run.get("failureCode")
        and value.get("commitSha") == run.get("commitSha")
        and value.get("sourceFingerprint") == run.get("sourceFingerprint")
    )


def _population_valid(nodeids, count, fingerprint):
    return (isinstance(nodeids, list) and all(isinstance(nodeid, str) and nodeid for nodeid in nodeids)
            and nodeids == sorted(nodeids) and len(nodeids) == len(set(nodeids)) == count
            and canonical_fingerprint({"nodeids": nodeids}) == fingerprint)


def _lineage_error(run, expected, *, blocked=False, shard_count=None):
    if any(run.get(field) != expected[field] for field in LINEAGE_FIELDS): return "lineage_mismatch"
    execution_expected = {
        field: expected[field]
        for field in ("runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint")
    }
    serial = run.get("serialLineage")
    parallel = run.get("parallelLineage")
    required = {"canonical", "runtimeExecutionLineage", "runtimeArtifactFingerprint"}
    if not isinstance(serial, Mapping) or set(serial) != required or serial.get("canonical") != dict(expected):
        return "artifact_lineage_mismatch"
    if serial.get("runtimeArtifactFingerprint") != run.get("serialRuntimeArtifactFingerprint"):
        return "artifact_lineage_mismatch"
    serial_runtime = serial.get("runtimeExecutionLineage")
    serial_observed = (
        isinstance(serial_runtime, Mapping)
        and set(serial_runtime) == set(execution_expected)
        and all(_is_sha(value, _SHA64) for value in serial_runtime.values())
        and dict(serial_runtime) == execution_expected
    )
    serial_unobserved = (
        blocked and run.get("serialStatus") != "PASS"
        and _is_unobserved_runtime_lineage(serial_runtime, run)
    )
    if not serial_observed and not serial_unobserved:
        return "artifact_lineage_mismatch"
    if not isinstance(parallel, Mapping) or set(parallel) != required or parallel.get("canonical") != dict(expected):
        return "artifact_lineage_mismatch"
    if parallel.get("runtimeArtifactFingerprint") != run.get("parallelRuntimeArtifactFingerprint"):
        return "artifact_lineage_mismatch"
    parallel_runtime = parallel.get("runtimeExecutionLineage")
    parallel_unobserved = (
        blocked and run.get("parallelStatus") != "PASS"
        and _is_unobserved_runtime_lineage(parallel_runtime, run)
    )
    if parallel_unobserved:
        return None
    if not isinstance(parallel_runtime, list) or not parallel_runtime:
        return "artifact_lineage_mismatch"
    if blocked and run.get("parallelStatus") != "PASS" and len(parallel_runtime) > shard_count:
        return "artifact_lineage_mismatch"
    for item in parallel_runtime:
        if (
            not isinstance(item, Mapping)
            or set(item) != set(execution_expected)
            or not all(_is_sha(value, _SHA64) for value in item.values())
            or dict(item) != execution_expected
        ):
            return "artifact_lineage_mismatch"
    if (not blocked or run.get("parallelStatus") == "PASS") and (
        len(parallel_runtime) != shard_count
        or any(dict(item) != execution_expected for item in parallel_runtime)
    ):
        return "artifact_lineage_mismatch"
    return None


def _run_is_blocked(run):
    parity = run.get("parity")
    aggregate = run.get("shardAggregate")
    return (
        _valid_failure_code(run.get("failureCode"))
        or bool(run.get("blockers"))
        or run.get("serialStatus") != "PASS"
        or run.get("parallelStatus") != "PASS"
        or not isinstance(parity, Mapping)
        or parity.get("status") != "PASS"
        or not isinstance(aggregate, Mapping)
        or aggregate.get("status") != "PASS"
    )


def _cleanup_evidence_error(value):
    if not isinstance(value, Mapping) or set(value) != {"serial", "parallel"}:
        return "blocked_run_evidence_invalid"
    serial = value.get("serial")
    parallel = value.get("parallel")
    if not isinstance(serial, Mapping) or set(serial) != {"status", "allProcessGroupsTerminated"}:
        return "blocked_run_evidence_invalid"
    if not isinstance(parallel, Mapping) or set(parallel) != {
        "status", "allProcessGroupsTerminated", "runtimeCleanupConfirmed", "fixtureRootsRemoved",
    }:
        return "blocked_run_evidence_invalid"
    if serial.get("status") not in {"PASS", "BLOCKED"} or not isinstance(
        serial.get("allProcessGroupsTerminated"), bool
    ):
        return "blocked_run_evidence_invalid"
    if parallel.get("status") not in {"PASS", "BLOCKED"} or any(
        not isinstance(parallel.get(field), bool)
        for field in ("allProcessGroupsTerminated", "runtimeCleanupConfirmed", "fixtureRootsRemoved")
    ):
        return "blocked_run_evidence_invalid"
    if serial["status"] == "PASS" and not serial["allProcessGroupsTerminated"]:
        return "blocked_run_evidence_invalid"
    parallel_clean = all(
        parallel[field]
        for field in ("allProcessGroupsTerminated", "runtimeCleanupConfirmed", "fixtureRootsRemoved")
    )
    if (parallel["status"] == "PASS") != parallel_clean:
        return "blocked_run_evidence_invalid"
    return None


def _runner_capability_error(run, expected_lineage, shard_count, *, blocked=False):
    receipt = run.get("runnerCapability")
    required = {"schemaVersion", "runnerFingerprint", "maxWorkers", "capabilityFingerprint"}
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        return "runner_capability_receipt_invalid"
    if receipt.get("schemaVersion") != "acceptance-runner-capability-v1":
        return "runner_capability_receipt_invalid"
    if receipt.get("runnerFingerprint") != expected_lineage.get("runnerFingerprint"):
        return "runner_capability_fingerprint_mismatch"
    workers = receipt.get("maxWorkers")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        return "runner_capability_insufficient"
    unsigned = {key: receipt[key] for key in ("schemaVersion", "runnerFingerprint", "maxWorkers")}
    if receipt.get("capabilityFingerprint") != canonical_fingerprint(unsigned):
        return "runner_capability_receipt_invalid"
    if (not blocked or run.get("parallelStatus") == "PASS") and workers < shard_count:
        return "runner_capability_insufficient"
    return None


def _coverage_error(population, coverage, shard_count, *, strict, allow_empty=False, aggregate_pass=False, allow_partial=False):
    if not isinstance(coverage, list): return "shard_coverage_invalid"
    if not coverage:
        return None if allow_empty else "shard_coverage_invalid"
    if (strict or aggregate_pass) and len(coverage) != shard_count: return "shard_coverage_invalid"
    if allow_partial and len(coverage) > shard_count: return "shard_coverage_invalid"
    by_index = {}
    for item in coverage:
        if not isinstance(item, Mapping): return "shard_coverage_invalid"
        index, assigned, executed = item.get("shardIndex"), item.get("assignedNodeids"), item.get("executedNodeids")
        values = assigned + executed if isinstance(assigned, list) and isinstance(executed, list) else []
        if (isinstance(index, bool) or not isinstance(index, int) or index not in range(shard_count) or index in by_index
                or not isinstance(assigned, list) or not isinstance(executed, list) or assigned != sorted(assigned)
                or executed != sorted(executed) or len(assigned) != len(set(assigned))
                or len(executed) != len(set(executed))
                or any(not isinstance(nodeid, str) or not nodeid for nodeid in values)
                or any(nodeid not in population for nodeid in values) or any(nodeid not in assigned for nodeid in executed)):
            return "shard_coverage_invalid"
        if strict:
            expected = [nodeid for position, nodeid in enumerate(population) if position % shard_count == index]
            if assigned != expected or executed != assigned:
                return "shard_coverage_invalid"
        elif aggregate_pass and executed != assigned:
            return "shard_coverage_invalid"
        by_index[index] = (assigned, executed)
    if not allow_partial and set(by_index) != set(range(shard_count)):
        return "shard_coverage_invalid"
    assigned_all = [nodeid for index in sorted(by_index) for nodeid in by_index[index][0]]
    if len(assigned_all) != len(set(assigned_all)):
        return "shard_coverage_invalid"
    if strict or aggregate_pass:
        return None if sorted(assigned_all) == population else "shard_coverage_invalid"
    return None


def _validate_run_shape(run, expected_lineage, shard_count, *, expected_commit_sha=None, expected_source_fingerprint=None):
    if not isinstance(run, Mapping): return "run_invalid"
    if run.get("populationKind") != POPULATION_KIND: return "population_kind_mismatch"
    run_index = run.get("runIndex")
    if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index not in {0, 1, 2}: return "run_index_invalid"
    if expected_commit_sha is not None and run.get("commitSha") != expected_commit_sha: return "run_source_identity_mismatch"
    if expected_source_fingerprint is not None and run.get("sourceFingerprint") != expected_source_fingerprint: return "run_source_identity_mismatch"
    if failure := _lineage_error(run, expected_lineage, shard_count=shard_count): return failure
    if failure := _runner_capability_error(run, expected_lineage, shard_count): return failure
    population_count = run.get("testPopulationCount")
    if isinstance(population_count, bool) or not isinstance(population_count, int) or not 0 < population_count <= MAX_COUNT: return "population_count_invalid"
    if run.get("serialStatus") != "PASS" or run.get("parallelStatus") != "PASS": return "run_status_failed"
    serial_counts = _safe_counts(run.get("serialResult"))
    if serial_counts is None or sum(serial_counts.values()) != population_count: return "serial_counts_invalid"
    population_nodeids = run.get("populationNodeids")
    if not _population_valid(population_nodeids, population_count, expected_lineage["testPopulationFingerprint"]): return "population_nodeids_invalid"
    failure = _coverage_error(population_nodeids, run.get("shardCoverage"), shard_count, strict=True)
    if failure: return failure
    parity = run.get("parity")
    if not isinstance(parity, Mapping) or parity.get("status") != "PASS" or not _valid_evidence_fingerprint(parity): return "parity_mismatch"
    if parity.get("serial") != serial_counts: return "serial_parity_evidence_mismatch"
    aggregate = run.get("shardAggregate")
    if not isinstance(aggregate, Mapping) or aggregate.get("status") != "PASS": return "shard_aggregate_failed"
    if not _valid_evidence_fingerprint(aggregate): return "shard_aggregate_evidence_invalid"
    aggregate_counts = _safe_counts(aggregate.get("result"))
    if aggregate_counts is None or sum(aggregate_counts.values()) != population_count: return "shard_counts_invalid"
    if parity.get("shard") != aggregate_counts: return "parallel_parity_evidence_mismatch"
    if (
        aggregate.get("manifestFingerprint") != expected_lineage["manifestFingerprint"]
        or aggregate.get("shardCount") != shard_count
        or aggregate.get("coveredNodeids") != population_count
    ):
        return "shard_count_mismatch"
    shard_fingerprints = aggregate.get("shards")
    if not isinstance(shard_fingerprints, Mapping) or set(shard_fingerprints) != {str(index) for index in range(shard_count)}: return "shard_coverage_invalid"
    if any(not _is_sha(value, _SHA64) for value in shard_fingerprints.values()): return "shard_coverage_invalid"
    if not _all_sha(run, FINGERPRINT_FIELDS): return "artifact_missing"
    if run.get("failureCode"): return "run_failed"
    if run.get("blockers"): return "run_blocked"
    return None


def validate_parallel_run_for_artifact(
    run: Mapping[str, Any], *, expected_lineage: Mapping[str, Any],
    commit_sha: str, source_fingerprint: str, shard_count: int, blocked: bool,
) -> None:
    """Validate bounded measured evidence, including failed partial runs."""
    if not isinstance(run, Mapping):
        _invalid_run()
    if run.get("populationKind") != POPULATION_KIND:
        _invalid_run("population_kind_mismatch")
    run_index = run.get("runIndex")
    if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index not in {0, 1, 2}:
        _invalid_run("run_index_invalid")
    if run.get("commitSha") != commit_sha or run.get("sourceFingerprint") != source_fingerprint:
        _invalid_run("run_source_identity_mismatch")
    run_blocked = _run_is_blocked(run)
    if not blocked and run_blocked:
        _invalid_run("blocked_run_evidence_invalid")
    if failure := _lineage_error(run, expected_lineage, blocked=run_blocked, shard_count=shard_count):
        _invalid_run(failure)
    if failure := _runner_capability_error(
        run, expected_lineage, shard_count, blocked=run_blocked
    ):
        _invalid_run(failure)
    population_count = run.get("testPopulationCount")
    if isinstance(population_count, bool) or not isinstance(population_count, int) or not 0 < population_count <= MAX_COUNT:
        _invalid_run("population_count_invalid")
    population_nodeids = run.get("populationNodeids")
    if not _population_valid(population_nodeids, population_count, expected_lineage["testPopulationFingerprint"]):
        _invalid_run("population_nodeids_invalid")
    serial_counts = _safe_counts(run.get("serialResult"))
    if serial_counts is None or sum(serial_counts.values()) > population_count or (
        run.get("serialStatus") == "PASS" and sum(serial_counts.values()) != population_count
    ):
        _invalid_run("serial_counts_invalid")
    durations = (run.get("serialWallSeconds"), run.get("parallelWallSeconds"))
    if run_blocked:
        if any(value is not None and not _is_duration(value) for value in durations):
            _invalid_run("duration_invalid")
    elif any(not _is_duration(value) for value in durations):
        _invalid_run("duration_invalid")
    if any(run.get(field) not in {"PASS", "FAIL", "BLOCKED"} for field in ("serialStatus", "parallelStatus")):
        _invalid_run("run_status_invalid")
    parity = run.get("parity")
    if not isinstance(parity, Mapping) or parity.get("status") not in {"PASS", "BLOCKED"}:
        _invalid_run("parity_mismatch")
    aggregate = run.get("shardAggregate")
    if not isinstance(aggregate, Mapping) or aggregate.get("status") not in {"PASS", "BLOCKED"}:
        _invalid_run("shard_aggregate_failed")
    if parity.get("status") == "PASS" and (
        run.get("serialStatus") != "PASS"
        or run.get("parallelStatus") != "PASS"
        or aggregate.get("status") != "PASS"
    ):
        _invalid_run("parity_status_inconsistent")
    if aggregate.get("commitSha") != commit_sha or aggregate.get("sourceFingerprint") != source_fingerprint:
        _invalid_run("shard_aggregate_source_identity_mismatch")
    if aggregate.get("manifestFingerprint") != expected_lineage.get("manifestFingerprint"):
        _invalid_run("shard_aggregate_manifest_mismatch")
    if aggregate.get("shardCount", shard_count) != shard_count:
        _invalid_run("shard_count_mismatch")
    if run_blocked:
        if not _valid_failure_code(run.get("failureCode")):
            _invalid_run("blocked_run_evidence_invalid")
        if failure := _cleanup_evidence_error(run.get("cleanupEvidence")):
            _invalid_run(failure)
    failure = _coverage_error(
        population_nodeids, run.get("shardCoverage"), shard_count, strict=False,
        allow_empty=aggregate.get("status") == "BLOCKED",
        aggregate_pass=aggregate.get("status") == "PASS",
        allow_partial=aggregate.get("status") == "BLOCKED",
    )
    if failure:
        _invalid_run(failure)
    if not isinstance(parity, Mapping) or not _valid_evidence_fingerprint(parity):
        _invalid_run("parity_evidence_invalid")
    aggregate_result = _safe_counts(aggregate.get("result"))
    if aggregate_result is None or sum(aggregate_result.values()) > population_count or (
        aggregate.get("status") == "PASS" and sum(aggregate_result.values()) != population_count
    ):
        _invalid_run("shard_counts_invalid")
    if aggregate.get("status") == "BLOCKED" and "coveredNodeids" in aggregate:
        covered = aggregate.get("coveredNodeids")
        assigned = sum(
            len(item.get("assignedNodeids", []))
            for item in (run.get("shardCoverage") or [])
            if isinstance(item, Mapping) and isinstance(item.get("assignedNodeids"), list)
        )
        if (
            isinstance(covered, bool) or not isinstance(covered, int)
            or not 0 <= covered <= population_count
            or covered != assigned
            or sum(aggregate_result.values()) > covered
        ):
            raise ValueError("blocked aggregate metadata is inconsistent")
    if not (
        _valid_evidence_fingerprint(aggregate)
        and run.get("shardAggregateFingerprint") == aggregate.get("evidenceFingerprint")
    ):
        _invalid_run("shard_aggregate_evidence_invalid")
    if not _all_sha(run, FINGERPRINT_FIELDS):
        _invalid_run("artifact_missing")
    if "blockers" in run and (
        not isinstance(run["blockers"], list)
        or any(not isinstance(item, str) or not item for item in run["blockers"])
    ):
        _invalid_run("blockers_invalid")
    if run_blocked and {"speedRatio", "speedupMultiple"} & run.keys():
        raise ValueError("blocked rollout cannot contain speedup values")
    if not run_blocked:
        failure = _validate_run_shape(
            run, expected_lineage, shard_count,
            expected_commit_sha=commit_sha, expected_source_fingerprint=source_fingerprint,
        )
        if failure:
            _invalid_run(failure)
        expected_ratio = round(float(run["parallelWallSeconds"]) / float(run["serialWallSeconds"]), 6)
        expected_multiple = round(float(run["serialWallSeconds"]) / float(run["parallelWallSeconds"]), 6)
        if not _is_speedup_metric(expected_ratio) or not _is_speedup_metric(expected_multiple):
            if blocked and not ({"speedRatio", "speedupMultiple"} & run.keys()):
                return
            raise ValueError("measured run speedup is invalid")
        if run.get("speedRatio") != expected_ratio or run.get("speedupMultiple") != expected_multiple:
            raise ValueError("measured run speedup is inconsistent")
        if not _is_speedup_metric(run.get("speedRatio")) or not _is_speedup_metric(run.get("speedupMultiple")):
            raise ValueError("measured run speedup is invalid")


def build_execution_performance_v2(*, role, total_seconds, result, commit_sha, source_fingerprint, baseline_family_id, lineage):
    counts = _safe_counts(result)
    if counts is None:
        raise ValueError("pytest counts are invalid")
    collected = sum(counts.values())
    total = float(total_seconds)
    if not math.isfinite(total) or not 0.0 < total <= MAX_DURATION_SECONDS:
        raise ValueError("performance duration is invalid")
    return build_performance_baseline_v2(
        baseline_family_id=baseline_family_id,
        baseline_role=role,
        lifecycle="draft",
        contract_fingerprint=lineage["contractFingerprint"],
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        manifest_fingerprint=lineage["manifestFingerprint"],
        test_population_fingerprint=lineage["testPopulationFingerprint"],
        runner_fingerprint=lineage["runnerFingerprint"],
        environment_fingerprint=lineage["environmentFingerprint"],
        dataset_snapshot_fingerprint=lineage["datasetSnapshotFingerprint"],
        selection_mode=lineage["selectionMode"],
        stages={"collectionSeconds": 0.0, "fixturePreparationSeconds": 0.0,
                "pytestExecutionSeconds": total, "aggregateSeconds": 0.0,
                "totalWallSeconds": total},
        test_count={"collected": collected, **counts},
    )
