import re
from collections.abc import Mapping, Sequence
from statistics import median

from backend.agents.acceptance_performance import MAX_COUNT, MAX_DURATION_SECONDS
from backend.agents.acceptance_performance_v2 import (
    MAX_SPEEDUP_METRIC as _MAX_SPEEDUP_METRIC,
    validate_parallel_run_for_artifact, _valid_failure_code,
    _is_duration, _is_speedup_metric, _is_sha, _safe_counts, _valid_evidence_fingerprint, _all_sha, _invalid_run, _population_valid, _lineage_error, _coverage_error, _validate_run_shape,
)
from backend.agents.evidence_models import canonical_fingerprint


SCHEMA_VERSION = "acceptance-parallel-rollout-v1"
AUTHORITY = "diagnostic"
ROLLOUT_MODE = "parallel_candidate"
POPULATION_KIND = "full-pytest-nodeid"
ALLOWED_SHARD_COUNTS = frozenset({2, 4, 8, 16})
MEASURED_RUN_COUNT = 3
MAX_SPEED_RATIO = .80
MIN_MEDIAN_SPEEDUP = 1.25
MAX_SPEEDUP_METRIC = _MAX_SPEEDUP_METRIC

LINEAGE_FIELDS = ("contractFingerprint", "baselineFamilyId", "manifestFingerprint", "testPopulationFingerprint", "runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint", "selectionMode")
FINGERPRINT_FIELDS = ("serialArtifactFingerprint", "parallelArtifactFingerprint", "shardAggregateFingerprint")
TOP_LEVEL_KEYS = frozenset("schemaVersion authority formalReleaseEnabled status rolloutMode commitSha sourceFingerprint contractFingerprint baselineFamilyId manifestFingerprint testPopulationFingerprint runnerFingerprint environmentFingerprint datasetSnapshotFingerprint selectionMode populationKind shardCount repeatCount measuredRuns parity speedup stability blockers rollback rolloutCandidate evidenceFingerprint".split())
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROLLBACK_COMMAND = "gh workflow run release-gates.yml -f enable_acceptance_parallel_rollout=false"


def _failure(code, *, detail=None, run_count=0):
    blockers = [code]
    if detail and detail != code:
        blockers.append(detail)
    return {"status": "BLOCKED", "failureCode": code, "rolloutCandidate": "ineligible", "speedup": None,
            "stability": {"measuredRunCount": run_count, "status": "BLOCKED"}, "blockers": blockers}


def compute_speedup_metrics(serial_wall_seconds, parallel_wall_seconds):
    if len(serial_wall_seconds) != len(parallel_wall_seconds) or not serial_wall_seconds:
        raise ValueError("wall-clock measurements must have equal non-empty lengths")
    ratios, multiples = [], []
    for serial, parallel in zip(serial_wall_seconds, parallel_wall_seconds):
        if not _is_duration(serial) or not _is_duration(parallel):
            raise ValueError("wall-clock duration is invalid")
        ratio, multiple = float(parallel) / float(serial), float(serial) / float(parallel)
        if not _is_speedup_metric(ratio) or not _is_speedup_metric(multiple):
            raise ValueError("speedup metric is invalid")
        ratios.append(round(ratio, 6))
        multiples.append(round(multiple, 6))
    return {"speedRatio": ratios, "speedupMultiple": multiples,
            "medianSpeedupMultiple": round(float(median(
                float(serial) / float(parallel)
                for serial, parallel in zip(serial_wall_seconds, parallel_wall_seconds)
            )), 6)}


def _validate_lineage(expected_lineage):
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


def evaluate_rollout_eligibility(measured_runs, expected_lineage, *, shard_count, expected_commit_sha=None, expected_source_fingerprint=None):
    if isinstance(shard_count, bool) or shard_count not in ALLOWED_SHARD_COUNTS:
        return _failure("shard_count_invalid")
    if not isinstance(measured_runs, Sequence) or isinstance(measured_runs, (str, bytes)):
        return _failure("run_count_invalid")
    if len(measured_runs) != MEASURED_RUN_COUNT:
        return _failure("run_count_invalid", run_count=len(measured_runs))
    if not _validate_lineage(expected_lineage):
        return _failure("expected_lineage_invalid", run_count=len(measured_runs))

    for run in measured_runs:
        if not isinstance(run, Mapping):
            return _failure("duration_invalid", run_count=len(measured_runs))
        if run.get("serialStatus") == "PASS" and run.get("parallelStatus") == "PASS" and (
            not _is_duration(run.get("serialWallSeconds"))
            or not _is_duration(run.get("parallelWallSeconds"))
        ):
            return _failure("duration_invalid", run_count=len(measured_runs))
    indexes = []
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
        failure = _validate_run_shape(
            run, expected_lineage, shard_count,
            expected_commit_sha=expected_commit_sha,
            expected_source_fingerprint=expected_source_fingerprint,
        )
        if failure:
            return _failure(failure, run_count=len(measured_runs))

    ordered_runs = sorted(measured_runs, key=lambda run: run["runIndex"])
    serial = [float(run["serialWallSeconds"]) for run in ordered_runs]
    parallel = [float(run["parallelWallSeconds"]) for run in ordered_runs]
    try:
        metrics = compute_speedup_metrics(serial, parallel)
    except ValueError:
        return _failure("speedup_metric_invalid", run_count=len(measured_runs))
    if any(p / s > MAX_SPEED_RATIO for s, p in zip(serial, parallel)):
        return _failure("speedup_threshold_not_met", run_count=len(measured_runs))
    if median(s / p for s, p in zip(serial, parallel)) < MIN_MEDIAN_SPEEDUP:
        return _failure("speedup_threshold_not_met", run_count=len(measured_runs))

    return {"status": "PASS", "failureCode": None, "rolloutCandidate": "eligible", "speedup": metrics,
            "stability": {"status": "PASS", "measuredRunCount": MEASURED_RUN_COUNT,
                          "allRunsWithinSpeedRatio": True, "maxSpeedRatio": MAX_SPEED_RATIO,
                          "minMedianSpeedupMultiple": MIN_MEDIAN_SPEEDUP}, "blockers": []}


def build_parallel_rollout_evidence(*, commit_sha, source_fingerprint, contract_fingerprint, baseline_family_id,
                                    manifest_fingerprint, test_population_fingerprint, runner_fingerprint,
                                    environment_fingerprint, dataset_snapshot_fingerprint, selection_mode,
                                    shard_count, measured_runs):
    if not _is_sha(commit_sha, _SHA40):
        raise ValueError("commitSha is invalid")
    if not _is_sha(source_fingerprint, _SHA64):
        raise ValueError("sourceFingerprint is invalid")
    lineage = dict(zip(LINEAGE_FIELDS, (contract_fingerprint, baseline_family_id, manifest_fingerprint,
                                       test_population_fingerprint, runner_fingerprint, environment_fingerprint,
                                       dataset_snapshot_fingerprint, selection_mode)))
    normalized_runs = []
    for run in measured_runs:
        item = dict(run) if isinstance(run, Mapping) else {"runIndex": None}
        normalized_runs.append(item)
    parity_status = "PASS" if normalized_runs and all(
        isinstance(run.get("parity"), Mapping) and run["parity"].get("status") == "PASS"
        for run in normalized_runs
    ) else "BLOCKED"
    eligibility = evaluate_rollout_eligibility(
        normalized_runs, lineage, shard_count=shard_count,
        expected_commit_sha=commit_sha, expected_source_fingerprint=source_fingerprint,
    )
    discovered_blockers = []
    if parity_status != "PASS":
        discovered_blockers.append("parity_mismatch")
    for run in normalized_runs:
        for candidate in (
            run.get("failureCode"),
            run.get("parity", {}).get("failureCode") if isinstance(run.get("parity"), Mapping) else None,
            run.get("shardAggregate", {}).get("failureCode") if isinstance(run.get("shardAggregate"), Mapping) else None,
        ):
            if isinstance(candidate, str) and candidate and candidate not in discovered_blockers:
                discovered_blockers.append(candidate)
    if discovered_blockers and eligibility["status"] == "PASS":
        eligibility = _failure(discovered_blockers[0], run_count=len(normalized_runs))
    for item in normalized_runs:
        if eligibility["status"] == "PASS":
            index = int(item["runIndex"])
            item["speedRatio"] = eligibility["speedup"]["speedRatio"][index]
            item["speedupMultiple"] = eligibility["speedup"]["speedupMultiple"][index]
        else:
            item.pop("speedRatio", None)
            item.pop("speedupMultiple", None)
    blockers = list(eligibility["blockers"])
    for candidate in discovered_blockers:
        if candidate not in blockers:
            blockers.append(candidate)
    unsigned = {
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


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _validate_source_lineage(expected_source_lineage):
    if not isinstance(expected_source_lineage, Mapping):
        return False
    required = {"commitSha", "sourceFingerprint", *LINEAGE_FIELDS}
    return required.issubset(expected_source_lineage) and _is_sha(expected_source_lineage.get("commitSha"), _SHA40) and _is_sha(expected_source_lineage.get("sourceFingerprint"), _SHA64) and _validate_lineage(expected_source_lineage)


def validate_parallel_rollout_evidence(payload, *, expected_source_lineage):
    _require(isinstance(payload, Mapping) and set(payload) == TOP_LEVEL_KEYS, "parallel rollout schema is invalid")
    _require(_validate_source_lineage(expected_source_lineage), "expected source lineage is invalid")
    _require(payload["schemaVersion"] == SCHEMA_VERSION and payload["authority"] == AUTHORITY, "parallel rollout authority is invalid")
    _require(payload["formalReleaseEnabled"] is False and payload["rolloutMode"] == ROLLOUT_MODE, "parallel rollout cannot promote formal release")
    _require(payload["populationKind"] == POPULATION_KIND, "population kind is invalid")
    _require(_is_sha(payload["commitSha"], _SHA40) and _is_sha(payload["sourceFingerprint"], _SHA64), "source identity is invalid")
    for field in ("commitSha", "sourceFingerprint", *LINEAGE_FIELDS):
        _require(payload[field] == expected_source_lineage[field], "source lineage mismatch")
    lineage = {field: payload[field] for field in LINEAGE_FIELDS}
    _require(_validate_lineage(lineage), "parallel rollout lineage is invalid")
    shard_count = payload["shardCount"]
    _require(not isinstance(shard_count, bool) and shard_count in ALLOWED_SHARD_COUNTS, "shard count is invalid")
    _require(payload["repeatCount"] == MEASURED_RUN_COUNT, "repeat count is invalid")
    runs = payload["measuredRuns"]
    _require(isinstance(runs, list) and len(runs) == MEASURED_RUN_COUNT, "measured runs are invalid")
    blocked = payload["status"] == "BLOCKED"
    _require(payload["status"] in {"PASS", "BLOCKED"}, "parallel rollout status is invalid")
    for run in runs:
        validate_parallel_run_for_artifact(
            run, expected_lineage=lineage, commit_sha=payload["commitSha"],
            source_fingerprint=payload["sourceFingerprint"], shard_count=shard_count, blocked=blocked,
        )
    parity, stability = payload["parity"], payload["stability"]
    _require(isinstance(parity, Mapping) and parity.get("status") in {"PASS", "BLOCKED"}, "parallel rollout parity is invalid")
    _require(parity.get("measuredRunCount") == MEASURED_RUN_COUNT, "parallel rollout parity run count is invalid")
    _require(isinstance(stability, Mapping) and stability.get("measuredRunCount") == MEASURED_RUN_COUNT, "parallel rollout stability is invalid")
    _require(stability.get("status") in {"PASS", "BLOCKED"}, "parallel rollout stability status is invalid")
    expected_parity = "PASS" if all(isinstance(run.get("parity"), Mapping) and run["parity"].get("status") == "PASS" for run in runs) else "BLOCKED"
    _require(parity.get("status") == expected_parity, "parallel rollout parity state is inconsistent")
    _require(isinstance(payload["blockers"], list) and all(_valid_failure_code(item) for item in payload["blockers"]), "parallel rollout blockers are invalid")
    if blocked:
        _require(
            payload["speedup"] is None and payload["rolloutCandidate"] == "ineligible"
            and payload["blockers"] and stability.get("status") == "BLOCKED",
            "blocked rollout speedup state is invalid",
        )
    else:
        _require(
            payload["rolloutCandidate"] == "eligible" and payload["speedup"] is not None
            and not payload["blockers"],
            "passing rollout speedup state is invalid",
        )
    recomputed = evaluate_rollout_eligibility(
        runs, lineage, shard_count=shard_count,
        expected_commit_sha=payload["commitSha"],
        expected_source_fingerprint=payload["sourceFingerprint"],
    )
    discovered = []
    if expected_parity != "PASS":
        discovered.append("parity_mismatch")
    for run in runs:
        for candidate in (
            run.get("failureCode"),
            run.get("parity", {}).get("failureCode") if isinstance(run.get("parity"), Mapping) else None,
            run.get("shardAggregate", {}).get("failureCode") if isinstance(run.get("shardAggregate"), Mapping) else None,
        ):
            if isinstance(candidate, str) and candidate and candidate not in discovered:
                discovered.append(candidate)
    if discovered and recomputed["status"] == "PASS":
        recomputed = _failure(discovered[0], run_count=len(runs))
    expected_blockers = list(recomputed["blockers"])
    expected_blockers.extend(item for item in discovered if item not in expected_blockers)
    consistency_message = (
        "blocked rollout failure is inconsistent" if blocked
        else "passing rollout speedup is inconsistent"
    )
    _require(
        payload["status"] == recomputed["status"]
        and payload["rolloutCandidate"] == recomputed["rolloutCandidate"]
        and payload["speedup"] == recomputed["speedup"]
        and stability == recomputed["stability"]
        and payload["blockers"] == expected_blockers,
        consistency_message,
    )
    _require(payload["rollback"] == {"mode": "workflow_dispatch", "command": _ROLLBACK_COMMAND}, "parallel rollout rollback is invalid")
    _require(_is_sha(payload["evidenceFingerprint"], _SHA64), "parallel rollout evidence fingerprint is invalid")
    _require(canonical_fingerprint({key: payload[key] for key in payload if key != "evidenceFingerprint"}) == payload["evidenceFingerprint"], "parallel rollout evidence fingerprint mismatch")
