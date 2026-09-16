"""Bounded, diagnostic-only contract for parallel acceptance rollout evidence."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from backend.agents.acceptance_performance import MAX_DURATION_SECONDS
from backend.agents.acceptance_performance import MAX_COUNT
from backend.agents.evidence_models import canonical_fingerprint


SCHEMA_VERSION = "acceptance-parallel-rollout-v1"
AUTHORITY = "diagnostic"
ROLLOUT_MODE = "parallel_candidate"
POPULATION_KIND = "full-pytest-nodeid"
ALLOWED_SHARD_COUNTS = frozenset({2, 4, 8, 16})
MEASURED_RUN_COUNT = 3
MAX_SPEED_RATIO = 0.80
MIN_MEDIAN_SPEEDUP = 1.25
MAX_SPEEDUP_METRIC = MAX_DURATION_SECONDS / 0.001

LINEAGE_FIELDS = (
    "contractFingerprint",
    "baselineFamilyId",
    "manifestFingerprint",
    "testPopulationFingerprint",
    "runnerFingerprint",
    "environmentFingerprint",
    "datasetSnapshotFingerprint",
    "selectionMode",
)
FINGERPRINT_FIELDS = (
    "serialArtifactFingerprint",
    "parallelArtifactFingerprint",
    "shardAggregateFingerprint",
)
TOP_LEVEL_KEYS = frozenset(
    {
        "schemaVersion",
        "authority",
        "formalReleaseEnabled",
        "status",
        "rolloutMode",
        "commitSha",
        "sourceFingerprint",
        "contractFingerprint",
        "baselineFamilyId",
        "manifestFingerprint",
        "testPopulationFingerprint",
        "runnerFingerprint",
        "environmentFingerprint",
        "datasetSnapshotFingerprint",
        "selectionMode",
        "populationKind",
        "shardCount",
        "repeatCount",
        "measuredRuns",
        "parity",
        "speedup",
        "stability",
        "blockers",
        "rollback",
        "rolloutCandidate",
        "evidenceFingerprint",
    }
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROLLBACK_COMMAND = "gh workflow run release-gates.yml -f enable_acceptance_parallel_rollout=false"


def _is_duration(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 < numeric <= MAX_DURATION_SECONDS


def _is_speedup_metric(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 < numeric <= MAX_SPEEDUP_METRIC


def _is_sha(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _safe_counts(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    counts: dict[str, int] = {}
    for field in ("passed", "failed", "skipped"):
        item = value.get(field, 0)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_COUNT:
            return None
        counts[field] = item
    return counts


def _valid_evidence_fingerprint(value: Mapping[str, Any]) -> bool:
    fingerprint = value.get("evidenceFingerprint")
    unsigned = {key: item for key, item in value.items() if key != "evidenceFingerprint"}
    return _is_sha(fingerprint, _SHA64) and canonical_fingerprint(unsigned) == fingerprint


def _failure(code: str, *, detail: str | None = None, run_count: int = 0) -> dict[str, Any]:
    blockers = [code]
    if detail and detail != code:
        blockers.append(detail)
    return {
        "status": "BLOCKED",
        "failureCode": code,
        "rolloutCandidate": "ineligible",
        "speedup": None,
        "stability": {"measuredRunCount": run_count, "status": "BLOCKED"},
        "blockers": blockers,
    }


def compute_speedup_metrics(
    serial_wall_seconds: Sequence[float],
    parallel_wall_seconds: Sequence[float],
) -> dict[str, Any]:
    """Compute wall-clock ratio and its reciprocal for matched measured runs."""
    if len(serial_wall_seconds) != len(parallel_wall_seconds) or not serial_wall_seconds:
        raise ValueError("wall-clock measurements must have equal non-empty lengths")
    ratios: list[float] = []
    multiples: list[float] = []
    for serial, parallel in zip(serial_wall_seconds, parallel_wall_seconds):
        if not _is_duration(serial) or not _is_duration(parallel):
            raise ValueError("wall-clock duration is invalid")
        ratios.append(round(float(parallel) / float(serial), 6))
        multiples.append(round(float(serial) / float(parallel), 6))
    return {
        "speedRatio": ratios,
        "speedupMultiple": multiples,
        "medianSpeedupMultiple": round(float(median(multiples)), 6),
    }


def _validate_lineage(expected_lineage: Mapping[str, Any]) -> bool:
    if not isinstance(expected_lineage, Mapping):
        return False
    for field in LINEAGE_FIELDS:
        value = expected_lineage.get(field)
        if field == "baselineFamilyId":
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                return False
        elif field == "selectionMode":
            if value not in {"full", "fast", "shard"}:
                return False
        elif not _is_sha(value, _SHA64):
            return False
    return True


def _validate_run_shape(run: Mapping[str, Any], expected_lineage: Mapping[str, Any], shard_count: int) -> str | None:
    if not isinstance(run, Mapping):
        return "run_invalid"
    if run.get("populationKind") != POPULATION_KIND:
        return "population_kind_mismatch"
    run_index = run.get("runIndex")
    if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index not in {0, 1, 2}:
        return "run_index_invalid"
    for field in LINEAGE_FIELDS:
        if run.get(field) != expected_lineage[field]:
            return "lineage_mismatch"
    for field in ("serialLineage", "parallelLineage"):
        if run.get(field) != dict(expected_lineage):
            return "artifact_lineage_mismatch"
    population_count = run.get("testPopulationCount")
    if isinstance(population_count, bool) or not isinstance(population_count, int) or not 0 < population_count <= MAX_COUNT:
        return "population_count_invalid"
    if run.get("serialStatus") != "PASS" or run.get("parallelStatus") != "PASS":
        return "run_status_failed"
    serial_counts = _safe_counts(run.get("serialResult"))
    if serial_counts is None or sum(serial_counts.values()) != population_count:
        return "serial_counts_invalid"
    population_nodeids = run.get("populationNodeids")
    if (
        not isinstance(population_nodeids, list)
        or any(not isinstance(nodeid, str) or not nodeid for nodeid in population_nodeids)
    ):
        return "population_nodeids_invalid"
    if (
        population_nodeids != sorted(population_nodeids)
        or len(population_nodeids) != len(set(population_nodeids))
        or len(population_nodeids) != population_count
        or canonical_fingerprint({"nodeids": population_nodeids}) != expected_lineage["testPopulationFingerprint"]
    ):
        return "population_nodeids_invalid"
    shard_coverage = run.get("shardCoverage")
    if not isinstance(shard_coverage, list) or len(shard_coverage) != shard_count:
        return "shard_coverage_invalid"
    coverage_by_index: dict[int, list[str]] = {}
    for item in shard_coverage:
        if not isinstance(item, Mapping):
            return "shard_coverage_invalid"
        index = item.get("shardIndex")
        assigned = item.get("assignedNodeids")
        executed = item.get("executedNodeids")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index not in range(shard_count)
            or index in coverage_by_index
            or not isinstance(assigned, list)
            or not isinstance(executed, list)
            or assigned != executed
            or any(not isinstance(nodeid, str) or not nodeid for nodeid in assigned)
        ):
            return "shard_coverage_invalid"
        if assigned != sorted(assigned):
            return "shard_coverage_invalid"
        expected_assigned = [
            nodeid for position, nodeid in enumerate(population_nodeids)
            if position % shard_count == index
        ]
        if assigned != expected_assigned:
            return "shard_coverage_invalid"
        coverage_by_index[index] = assigned
    if set(coverage_by_index) != set(range(shard_count)):
        return "shard_coverage_invalid"
    covered = [nodeid for index in range(shard_count) for nodeid in coverage_by_index[index]]
    if sorted(covered) != population_nodeids or len(covered) != len(set(covered)):
        return "shard_coverage_invalid"
    parity = run.get("parity")
    if not isinstance(parity, Mapping) or parity.get("status") != "PASS" or not _valid_evidence_fingerprint(parity):
        return "parity_mismatch"
    if parity.get("serial") != serial_counts:
        return "serial_parity_evidence_mismatch"
    aggregate = run.get("shardAggregate")
    if not isinstance(aggregate, Mapping) or aggregate.get("status") != "PASS":
        return "shard_aggregate_failed"
    if not _valid_evidence_fingerprint(aggregate):
        return "shard_aggregate_evidence_invalid"
    aggregate_counts = _safe_counts(aggregate.get("result"))
    if aggregate_counts is None or sum(aggregate_counts.values()) != population_count:
        return "shard_counts_invalid"
    if parity.get("shard") != aggregate_counts:
        return "parallel_parity_evidence_mismatch"
    if (
        aggregate.get("manifestFingerprint") != expected_lineage["manifestFingerprint"]
        or aggregate.get("shardCount") != shard_count
        or aggregate.get("coveredNodeids") != population_count
    ):
        return "shard_count_mismatch"
    shard_fingerprints = aggregate.get("shards")
    if not isinstance(shard_fingerprints, Mapping) or set(shard_fingerprints) != {str(index) for index in range(shard_count)}:
        return "shard_coverage_invalid"
    if any(not _is_sha(value, _SHA64) for value in shard_fingerprints.values()):
        return "shard_coverage_invalid"
    for field in FINGERPRINT_FIELDS:
        if not _is_sha(run.get(field), _SHA64):
            return "artifact_missing"
    if run.get("blockers"):
        return "run_blocked"
    return None


def evaluate_rollout_eligibility(
    measured_runs: Sequence[Mapping[str, Any]],
    expected_lineage: Mapping[str, str],
    *,
    shard_count: int,
) -> dict[str, Any]:
    """Return eligible only after three exact-lineage, parity-clean runs pass."""
    if isinstance(shard_count, bool) or shard_count not in ALLOWED_SHARD_COUNTS:
        return _failure("shard_count_invalid")
    if not isinstance(measured_runs, Sequence) or isinstance(measured_runs, (str, bytes)):
        return _failure("run_count_invalid")
    if len(measured_runs) != MEASURED_RUN_COUNT:
        return _failure("run_count_invalid", run_count=len(measured_runs))
    if not _validate_lineage(expected_lineage):
        return _failure("expected_lineage_invalid", run_count=len(measured_runs))

    # Validate all durations before any ratio is calculated.
    for run in measured_runs:
        if not isinstance(run, Mapping) or not _is_duration(run.get("serialWallSeconds")) or not _is_duration(run.get("parallelWallSeconds")):
            return _failure("duration_invalid", run_count=len(measured_runs))
    indexes: list[int] = []
    for run in measured_runs:
        if not isinstance(run, Mapping):
            return _failure("run_index_invalid", run_count=len(measured_runs))
        run_index = run.get("runIndex")
        if isinstance(run_index, bool) or not isinstance(run_index, int):
            return _failure("run_index_invalid", run_count=len(measured_runs))
        indexes.append(run_index)
    if sorted(indexes) != list(range(MEASURED_RUN_COUNT)):
        return _failure("run_index_invalid", run_count=len(measured_runs))

    for run in measured_runs:
        failure = _validate_run_shape(run, expected_lineage, shard_count)
        if failure:
            return _failure(failure, run_count=len(measured_runs))

    metrics = compute_speedup_metrics(
        [float(run["serialWallSeconds"]) for run in measured_runs],
        [float(run["parallelWallSeconds"]) for run in measured_runs],
    )
    if any(ratio > MAX_SPEED_RATIO for ratio in metrics["speedRatio"]):
        return _failure("speedup_threshold_not_met", run_count=len(measured_runs))
    if metrics["medianSpeedupMultiple"] < MIN_MEDIAN_SPEEDUP:
        return _failure("speedup_threshold_not_met", run_count=len(measured_runs))

    return {
        "status": "PASS",
        "failureCode": None,
        "rolloutCandidate": "eligible",
        "speedup": metrics,
        "stability": {
            "status": "PASS",
            "measuredRunCount": MEASURED_RUN_COUNT,
            "allRunsWithinSpeedRatio": True,
            "maxSpeedRatio": MAX_SPEED_RATIO,
            "minMedianSpeedupMultiple": MIN_MEDIAN_SPEEDUP,
        },
        "blockers": [],
    }


def build_parallel_rollout_evidence(
    *,
    commit_sha: str,
    source_fingerprint: str,
    contract_fingerprint: str,
    baseline_family_id: str,
    manifest_fingerprint: str,
    test_population_fingerprint: str,
    runner_fingerprint: str,
    environment_fingerprint: str,
    dataset_snapshot_fingerprint: str,
    selection_mode: str,
    shard_count: int,
    measured_runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble exact diagnostic evidence without I/O or lifecycle mutation."""
    if not _is_sha(commit_sha, _SHA40):
        raise ValueError("commitSha is invalid")
    if not _is_sha(source_fingerprint, _SHA64):
        raise ValueError("sourceFingerprint is invalid")
    lineage = {
        "contractFingerprint": contract_fingerprint,
        "baselineFamilyId": baseline_family_id,
        "manifestFingerprint": manifest_fingerprint,
        "testPopulationFingerprint": test_population_fingerprint,
        "runnerFingerprint": runner_fingerprint,
        "environmentFingerprint": environment_fingerprint,
        "datasetSnapshotFingerprint": dataset_snapshot_fingerprint,
        "selectionMode": selection_mode,
    }
    eligibility = evaluate_rollout_eligibility(measured_runs, lineage, shard_count=shard_count)
    normalized_runs: list[dict[str, Any]] = []
    for run in measured_runs:
        item = dict(run) if isinstance(run, Mapping) else {"runIndex": None}
        if eligibility["status"] == "PASS":
            index = int(item["runIndex"])
            item["speedRatio"] = eligibility["speedup"]["speedRatio"][index]
            item["speedupMultiple"] = eligibility["speedup"]["speedupMultiple"][index]
        else:
            item.pop("speedRatio", None)
            item.pop("speedupMultiple", None)
        normalized_runs.append(item)
    parity_status = "PASS" if normalized_runs and all(
        isinstance(run.get("parity"), Mapping) and run["parity"].get("status") == "PASS"
        for run in normalized_runs
    ) else "BLOCKED"
    blockers = list(eligibility["blockers"])
    for run in normalized_runs:
        for candidate in (
            run.get("failureCode"),
            run.get("parity", {}).get("failureCode") if isinstance(run.get("parity"), Mapping) else None,
            run.get("shardAggregate", {}).get("failureCode") if isinstance(run.get("shardAggregate"), Mapping) else None,
        ):
            if isinstance(candidate, str) and candidate and candidate not in blockers:
                blockers.append(candidate)
    unsigned: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "formalReleaseEnabled": False,
        "status": eligibility["status"],
        "rolloutMode": ROLLOUT_MODE,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        **lineage,
        "populationKind": POPULATION_KIND,
        "shardCount": shard_count,
        "repeatCount": len(normalized_runs),
        "measuredRuns": normalized_runs,
        "parity": {"status": parity_status, "measuredRunCount": len(normalized_runs)},
        "speedup": eligibility["speedup"],
        "stability": eligibility["stability"],
        "blockers": blockers,
        "rollback": {"mode": "workflow_dispatch", "command": _ROLLBACK_COMMAND},
        "rolloutCandidate": eligibility["rolloutCandidate"],
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _validate_run_for_artifact(run: Any, *, expected_lineage: Mapping[str, Any], shard_count: int, blocked: bool) -> None:
    if not isinstance(run, Mapping):
        raise ValueError("measured run is invalid")
    if run.get("populationKind") != POPULATION_KIND:
        raise ValueError("measured run is invalid: population_kind_mismatch")
    run_index = run.get("runIndex")
    if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index not in {0, 1, 2}:
        raise ValueError("measured run is invalid: run_index_invalid")
    for field in LINEAGE_FIELDS:
        if run.get(field) != expected_lineage[field]:
            raise ValueError("measured run is invalid: lineage_mismatch")
    for field in ("serialLineage", "parallelLineage"):
        if run.get(field) != dict(expected_lineage):
            raise ValueError("measured run is invalid: artifact_lineage_mismatch")
    population_count = run.get("testPopulationCount")
    if isinstance(population_count, bool) or not isinstance(population_count, int) or not 0 < population_count <= MAX_COUNT:
        raise ValueError("measured run is invalid: population_count_invalid")
    population_nodeids = run.get("populationNodeids")
    if (
        not isinstance(population_nodeids, list)
        or any(not isinstance(nodeid, str) or not nodeid for nodeid in population_nodeids)
        or population_nodeids != sorted(population_nodeids)
        or len(population_nodeids) != len(set(population_nodeids))
        or len(population_nodeids) != population_count
        or canonical_fingerprint({"nodeids": population_nodeids}) != expected_lineage["testPopulationFingerprint"]
    ):
        raise ValueError("measured run is invalid: population_nodeids_invalid")
    serial_counts = _safe_counts(run.get("serialResult"))
    if serial_counts is None or sum(serial_counts.values()) > population_count or (
        run.get("serialStatus") == "PASS" and sum(serial_counts.values()) != population_count
    ):
        raise ValueError("measured run is invalid: serial_counts_invalid")
    for field in ("serialWallSeconds", "parallelWallSeconds"):
        if not _is_duration(run.get(field)):
            raise ValueError("measured run is invalid: duration_invalid")
    for field in ("serialStatus", "parallelStatus"):
        if run.get(field) not in {"PASS", "FAIL", "BLOCKED"}:
            raise ValueError("measured run is invalid: run_status_invalid")
    parity = run.get("parity")
    if not isinstance(parity, Mapping) or parity.get("status") not in {"PASS", "BLOCKED"}:
        raise ValueError("measured run is invalid: parity_mismatch")
    aggregate = run.get("shardAggregate")
    if not isinstance(aggregate, Mapping) or aggregate.get("status") not in {"PASS", "BLOCKED"}:
        raise ValueError("measured run is invalid: shard_aggregate_failed")
    if aggregate.get("shardCount", shard_count) != shard_count:
        raise ValueError("measured run is invalid: shard_count_mismatch")
    shard_coverage = run.get("shardCoverage")
    if not isinstance(shard_coverage, list):
        raise ValueError("measured run is invalid: shard_coverage_invalid")
    if shard_coverage:
        coverage_by_index: dict[int, tuple[list[str], list[str]]] = {}
        for item in shard_coverage:
            if not isinstance(item, Mapping):
                raise ValueError("measured run is invalid: shard_coverage_invalid")
            index = item.get("shardIndex")
            assigned = item.get("assignedNodeids")
            executed = item.get("executedNodeids")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index not in range(shard_count)
                or index in coverage_by_index
                or not isinstance(assigned, list)
                or not isinstance(executed, list)
                or assigned != sorted(assigned)
                or executed != sorted(executed)
                or len(executed) != len(set(executed))
                or any(not isinstance(nodeid, str) or not nodeid for nodeid in assigned + executed)
                or any(nodeid not in population_nodeids for nodeid in assigned + executed)
                or any(nodeid not in assigned for nodeid in executed)
            ):
                raise ValueError("measured run is invalid: shard_coverage_invalid")
            if aggregate.get("status") == "PASS" and executed != assigned:
                raise ValueError("measured run is invalid: shard_coverage_invalid")
            coverage_by_index[index] = (assigned, executed)
        if set(coverage_by_index) != set(range(shard_count)):
            raise ValueError("measured run is invalid: shard_coverage_invalid")
        assigned_all = [nodeid for index in range(shard_count) for nodeid in coverage_by_index[index][0]]
        if len(assigned_all) != len(set(assigned_all)) or sorted(assigned_all) != population_nodeids:
            raise ValueError("measured run is invalid: shard_coverage_invalid")
    elif aggregate.get("status") != "BLOCKED":
        raise ValueError("measured run is invalid: shard_coverage_invalid")
    parity = run.get("parity")
    if not isinstance(parity, Mapping) or not _valid_evidence_fingerprint(parity):
        raise ValueError("measured run is invalid: parity_evidence_invalid")
    aggregate_result = _safe_counts(aggregate.get("result"))
    if aggregate_result is None or sum(aggregate_result.values()) > population_count:
        raise ValueError("measured run is invalid: shard_counts_invalid")
    if aggregate.get("status") == "PASS" and sum(aggregate_result.values()) != population_count:
        raise ValueError("measured run is invalid: shard_counts_invalid")
    if aggregate.get("status") == "PASS":
        if not _valid_evidence_fingerprint(aggregate):
            raise ValueError("measured run is invalid: shard_aggregate_evidence_invalid")
    elif run.get("shardAggregateFingerprint") != canonical_fingerprint(
        {key: value for key, value in aggregate.items() if key != "evidenceFingerprint"}
    ):
        raise ValueError("measured run is invalid: shard_aggregate_evidence_invalid")
    for field in FINGERPRINT_FIELDS:
        if not _is_sha(run.get(field), _SHA64):
            raise ValueError("measured run is invalid: artifact_missing")
    if "blockers" in run and (
        not isinstance(run["blockers"], list)
        or any(not isinstance(item, str) or not item for item in run["blockers"])
    ):
        raise ValueError("measured run is invalid: blockers_invalid")
    if blocked and ("speedRatio" in run or "speedupMultiple" in run):
        raise ValueError("blocked rollout cannot contain speedup values")
    if not blocked:
        failure = _validate_run_shape(run, expected_lineage, shard_count)
        if failure:
            raise ValueError(f"measured run is invalid: {failure}")
        expected_ratio = round(float(run["parallelWallSeconds"]) / float(run["serialWallSeconds"]), 6)
        expected_multiple = round(float(run["serialWallSeconds"]) / float(run["parallelWallSeconds"]), 6)
        if run.get("speedRatio") != expected_ratio or run.get("speedupMultiple") != expected_multiple:
            raise ValueError("measured run speedup is inconsistent")
        for field in ("speedRatio", "speedupMultiple"):
            if not _is_speedup_metric(run.get(field)):
                raise ValueError("measured run speedup is invalid")


def validate_parallel_rollout_evidence(payload: Mapping[str, Any]) -> None:
    """Validate exact schema, lineage and fail-closed speedup semantics."""
    if not isinstance(payload, Mapping) or set(payload) != TOP_LEVEL_KEYS:
        raise ValueError("parallel rollout schema is invalid")
    if payload["schemaVersion"] != SCHEMA_VERSION or payload["authority"] != AUTHORITY:
        raise ValueError("parallel rollout authority is invalid")
    if payload["formalReleaseEnabled"] is not False or payload["rolloutMode"] != ROLLOUT_MODE:
        raise ValueError("parallel rollout cannot promote formal release")
    if payload["populationKind"] != POPULATION_KIND:
        raise ValueError("population kind is invalid")
    if not _is_sha(payload["commitSha"], _SHA40) or not _is_sha(payload["sourceFingerprint"], _SHA64):
        raise ValueError("source identity is invalid")
    lineage = {field: payload[field] for field in LINEAGE_FIELDS}
    if not _validate_lineage(lineage):
        raise ValueError("parallel rollout lineage is invalid")
    shard_count = payload["shardCount"]
    if isinstance(shard_count, bool) or shard_count not in ALLOWED_SHARD_COUNTS:
        raise ValueError("shard count is invalid")
    if payload["repeatCount"] != MEASURED_RUN_COUNT:
        raise ValueError("repeat count is invalid")
    runs = payload["measuredRuns"]
    if not isinstance(runs, list) or len(runs) != MEASURED_RUN_COUNT:
        raise ValueError("measured runs are invalid")
    blocked = payload["status"] == "BLOCKED"
    if payload["status"] not in {"PASS", "BLOCKED"}:
        raise ValueError("parallel rollout status is invalid")
    for run in runs:
        _validate_run_for_artifact(run, expected_lineage=lineage, shard_count=shard_count, blocked=blocked)
    parity = payload["parity"]
    if not isinstance(parity, Mapping) or parity.get("status") not in {"PASS", "BLOCKED"}:
        raise ValueError("parallel rollout parity is invalid")
    if parity.get("measuredRunCount") != MEASURED_RUN_COUNT:
        raise ValueError("parallel rollout parity run count is invalid")
    stability = payload["stability"]
    if not isinstance(stability, Mapping) or stability.get("measuredRunCount") != MEASURED_RUN_COUNT:
        raise ValueError("parallel rollout stability is invalid")
    if stability.get("status") not in {"PASS", "BLOCKED"}:
        raise ValueError("parallel rollout stability status is invalid")
    expected_parity_status = "PASS" if all(
        isinstance(run.get("parity"), Mapping) and run["parity"].get("status") == "PASS"
        for run in runs
    ) else "BLOCKED"
    if parity.get("status") != expected_parity_status:
        raise ValueError("parallel rollout parity state is inconsistent")
    if not isinstance(payload["blockers"], list) or any(not isinstance(item, str) or not item for item in payload["blockers"]):
        raise ValueError("parallel rollout blockers are invalid")
    if blocked:
        if (
            payload["speedup"] is not None
            or payload["rolloutCandidate"] != "ineligible"
            or not payload["blockers"]
            or stability.get("status") != "BLOCKED"
        ):
            raise ValueError("blocked rollout speedup state is invalid")
    else:
        if payload["rolloutCandidate"] != "eligible" or payload["speedup"] is None or payload["blockers"]:
            raise ValueError("passing rollout speedup state is invalid")
        expected = evaluate_rollout_eligibility(runs, lineage, shard_count=shard_count)
        if (
            expected["status"] != "PASS"
            or payload["speedup"] != expected["speedup"]
            or stability != expected["stability"]
        ):
            raise ValueError("passing rollout speedup is inconsistent")
    rollback = payload["rollback"]
    if rollback != {"mode": "workflow_dispatch", "command": _ROLLBACK_COMMAND}:
        raise ValueError("parallel rollout rollback is invalid")
    if not _is_sha(payload["evidenceFingerprint"], _SHA64):
        raise ValueError("parallel rollout evidence fingerprint is invalid")
    if canonical_fingerprint({key: payload[key] for key in payload if key != "evidenceFingerprint"}) != payload["evidenceFingerprint"]:
        raise ValueError("parallel rollout evidence fingerprint mismatch")
