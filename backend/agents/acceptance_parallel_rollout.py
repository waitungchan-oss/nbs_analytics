import math
import re
from collections.abc import Mapping, Sequence
from statistics import median

from backend.agents.acceptance_performance import MAX_DURATION_SECONDS, MAX_COUNT
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

LINEAGE_FIELDS = ("contractFingerprint", "baselineFamilyId", "manifestFingerprint", "testPopulationFingerprint", "runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint", "selectionMode")
FINGERPRINT_FIELDS = ("serialArtifactFingerprint", "parallelArtifactFingerprint", "shardAggregateFingerprint")
TOP_LEVEL_KEYS = frozenset("schemaVersion authority formalReleaseEnabled status rolloutMode commitSha sourceFingerprint contractFingerprint baselineFamilyId manifestFingerprint testPopulationFingerprint runnerFingerprint environmentFingerprint datasetSnapshotFingerprint selectionMode populationKind shardCount repeatCount measuredRuns parity speedup stability blockers rollback rolloutCandidate evidenceFingerprint".split())
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROLLBACK_COMMAND = "gh workflow run release-gates.yml -f enable_acceptance_parallel_rollout=false"


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
        ratios.append(round(float(parallel) / float(serial), 6))
        multiples.append(round(float(serial) / float(parallel), 6))
    return {"speedRatio": ratios, "speedupMultiple": multiples, "medianSpeedupMultiple": round(float(median(multiples)), 6)}


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


def _population_valid(nodeids, count, fingerprint):
    return (isinstance(nodeids, list) and all(isinstance(nodeid, str) and nodeid for nodeid in nodeids)
            and nodeids == sorted(nodeids) and len(nodeids) == len(set(nodeids)) == count
            and canonical_fingerprint({"nodeids": nodeids}) == fingerprint)


def _lineage_error(run, expected):
    if any(run.get(field) != expected[field] for field in LINEAGE_FIELDS):
        return "lineage_mismatch"
    if any(run.get(field) != dict(expected) for field in ("serialLineage", "parallelLineage")):
        return "artifact_lineage_mismatch"
    return None


def _coverage_error(population, coverage, shard_count, *, strict, allow_empty=False, aggregate_pass=False):
    if not isinstance(coverage, list):
        return "shard_coverage_invalid"
    if not coverage:
        return None if allow_empty else "shard_coverage_invalid"
    if len(coverage) != shard_count:
        return "shard_coverage_invalid"
    by_index = {}
    for item in coverage:
        if not isinstance(item, Mapping):
            return "shard_coverage_invalid"
        index, assigned, executed = item.get("shardIndex"), item.get("assignedNodeids"), item.get("executedNodeids")
        values = assigned + executed if isinstance(assigned, list) and isinstance(executed, list) else []
        if (isinstance(index, bool) or not isinstance(index, int) or index not in range(shard_count) or index in by_index
                or not isinstance(assigned, list) or not isinstance(executed, list) or assigned != sorted(assigned)
                or executed != sorted(executed) or len(executed) != len(set(executed))
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
    if set(by_index) != set(range(shard_count)):
        return "shard_coverage_invalid"
    assigned_all = [nodeid for index in range(shard_count) for nodeid in by_index[index][0]]
    return None if sorted(assigned_all) == population and len(assigned_all) == len(set(assigned_all)) else "shard_coverage_invalid"


def _validate_run_shape(run, expected_lineage, shard_count):
    if not isinstance(run, Mapping):
        return "run_invalid"
    if run.get("populationKind") != POPULATION_KIND:
        return "population_kind_mismatch"
    run_index = run.get("runIndex")
    if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index not in {0, 1, 2}:
        return "run_index_invalid"
    if (failure := _lineage_error(run, expected_lineage)):
        return failure
    population_count = run.get("testPopulationCount")
    if isinstance(population_count, bool) or not isinstance(population_count, int) or not 0 < population_count <= MAX_COUNT:
        return "population_count_invalid"
    if run.get("serialStatus") != "PASS" or run.get("parallelStatus") != "PASS":
        return "run_status_failed"
    serial_counts = _safe_counts(run.get("serialResult"))
    if serial_counts is None or sum(serial_counts.values()) != population_count:
        return "serial_counts_invalid"
    population_nodeids = run.get("populationNodeids")
    if not _population_valid(population_nodeids, population_count, expected_lineage["testPopulationFingerprint"]):
        return "population_nodeids_invalid"
    failure = _coverage_error(population_nodeids, run.get("shardCoverage"), shard_count, strict=True)
    if failure:
        return failure
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
    if not _all_sha(run, FINGERPRINT_FIELDS):
        return "artifact_missing"
    if run.get("blockers"):
        return "run_blocked"
    return None


def evaluate_rollout_eligibility(measured_runs, expected_lineage, *, shard_count):
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


def build_parallel_rollout_evidence(*, commit_sha, source_fingerprint, contract_fingerprint, baseline_family_id,
                                    manifest_fingerprint, test_population_fingerprint, runner_fingerprint,
                                    environment_fingerprint, dataset_snapshot_fingerprint, selection_mode,
                                    shard_count, measured_runs):
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
    normalized_runs = []
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


def _validate_run_for_artifact(run, *, expected_lineage, shard_count, blocked):
    if not isinstance(run, Mapping):
        _invalid_run()
    if run.get("populationKind") != POPULATION_KIND:
        _invalid_run("population_kind_mismatch")
    run_index = run.get("runIndex")
    if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index not in {0, 1, 2}:
        _invalid_run("run_index_invalid")
    if failure := _lineage_error(run, expected_lineage):
        _invalid_run(failure)
    population_count = run.get("testPopulationCount")
    if isinstance(population_count, bool) or not isinstance(population_count, int) or not 0 < population_count <= MAX_COUNT:
        _invalid_run("population_count_invalid")
    population_nodeids = run.get("populationNodeids")
    if not _population_valid(population_nodeids, population_count, expected_lineage["testPopulationFingerprint"]):
        _invalid_run("population_nodeids_invalid")
    serial_counts = _safe_counts(run.get("serialResult"))
    if serial_counts is None or sum(serial_counts.values()) > population_count or (run.get("serialStatus") == "PASS" and sum(serial_counts.values()) != population_count):
        _invalid_run("serial_counts_invalid")
    if any(not _is_duration(run.get(field)) for field in ("serialWallSeconds", "parallelWallSeconds")):
        _invalid_run("duration_invalid")
    if any(run.get(field) not in {"PASS", "FAIL", "BLOCKED"} for field in ("serialStatus", "parallelStatus")):
        _invalid_run("run_status_invalid")
    parity = run.get("parity")
    if not isinstance(parity, Mapping) or parity.get("status") not in {"PASS", "BLOCKED"}:
        _invalid_run("parity_mismatch")
    aggregate = run.get("shardAggregate")
    if not isinstance(aggregate, Mapping) or aggregate.get("status") not in {"PASS", "BLOCKED"}:
        _invalid_run("shard_aggregate_failed")
    if aggregate.get("shardCount", shard_count) != shard_count:
        _invalid_run("shard_count_mismatch")
    failure = _coverage_error(population_nodeids, run.get("shardCoverage"), shard_count, strict=False, allow_empty=aggregate.get("status") == "BLOCKED", aggregate_pass=aggregate.get("status") == "PASS")
    if failure:
        _invalid_run(failure)
    if not isinstance(parity, Mapping) or not _valid_evidence_fingerprint(parity):
        _invalid_run("parity_evidence_invalid")
    aggregate_result = _safe_counts(aggregate.get("result"))
    if aggregate_result is None or sum(aggregate_result.values()) > population_count or (aggregate.get("status") == "PASS" and sum(aggregate_result.values()) != population_count):
        _invalid_run("shard_counts_invalid")
    aggregate_unsigned = {key: value for key, value in aggregate.items() if key != "evidenceFingerprint"}
    if not ((aggregate.get("status") == "PASS" and _valid_evidence_fingerprint(aggregate))
            or (aggregate.get("status") == "BLOCKED" and run.get("shardAggregateFingerprint") == canonical_fingerprint(aggregate_unsigned))):
        _invalid_run("shard_aggregate_evidence_invalid")
    if not _all_sha(run, FINGERPRINT_FIELDS):
        _invalid_run("artifact_missing")
    if "blockers" in run and (not isinstance(run["blockers"], list)
                               or any(not isinstance(item, str) or not item for item in run["blockers"])):
        _invalid_run("blockers_invalid")
    if blocked and {"speedRatio", "speedupMultiple"} & run.keys():
        raise ValueError("blocked rollout cannot contain speedup values")
    if not blocked:
        failure = _validate_run_shape(run, expected_lineage, shard_count)
        if failure:
            _invalid_run(failure)
        expected_ratio = round(float(run["parallelWallSeconds"]) / float(run["serialWallSeconds"]), 6)
        expected_multiple = round(float(run["serialWallSeconds"]) / float(run["parallelWallSeconds"]), 6)
        if run.get("speedRatio") != expected_ratio or run.get("speedupMultiple") != expected_multiple:
            raise ValueError("measured run speedup is inconsistent")
        for field in ("speedRatio", "speedupMultiple"):
            if not _is_speedup_metric(run.get(field)):
                raise ValueError("measured run speedup is invalid")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_parallel_rollout_evidence(payload):
    _require(isinstance(payload, Mapping) and set(payload) == TOP_LEVEL_KEYS, "parallel rollout schema is invalid")
    _require(payload["schemaVersion"] == SCHEMA_VERSION and payload["authority"] == AUTHORITY, "parallel rollout authority is invalid")
    _require(payload["formalReleaseEnabled"] is False and payload["rolloutMode"] == ROLLOUT_MODE, "parallel rollout cannot promote formal release")
    _require(payload["populationKind"] == POPULATION_KIND, "population kind is invalid")
    _require(_is_sha(payload["commitSha"], _SHA40) and _is_sha(payload["sourceFingerprint"], _SHA64), "source identity is invalid")
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
        _validate_run_for_artifact(run, expected_lineage=lineage, shard_count=shard_count, blocked=blocked)
    parity, stability = payload["parity"], payload["stability"]
    _require(isinstance(parity, Mapping) and parity.get("status") in {"PASS", "BLOCKED"}, "parallel rollout parity is invalid")
    _require(parity.get("measuredRunCount") == MEASURED_RUN_COUNT, "parallel rollout parity run count is invalid")
    _require(isinstance(stability, Mapping) and stability.get("measuredRunCount") == MEASURED_RUN_COUNT, "parallel rollout stability is invalid")
    _require(stability.get("status") in {"PASS", "BLOCKED"}, "parallel rollout stability status is invalid")
    expected_parity = "PASS" if all(isinstance(run.get("parity"), Mapping) and run["parity"].get("status") == "PASS" for run in runs) else "BLOCKED"
    _require(parity.get("status") == expected_parity, "parallel rollout parity state is inconsistent")
    _require(isinstance(payload["blockers"], list) and all(isinstance(item, str) and item for item in payload["blockers"]), "parallel rollout blockers are invalid")
    if blocked:
        _require(payload["speedup"] is None and payload["rolloutCandidate"] == "ineligible" and payload["blockers"] and stability.get("status") == "BLOCKED", "blocked rollout speedup state is invalid")
    else:
        _require(payload["rolloutCandidate"] == "eligible" and payload["speedup"] is not None and not payload["blockers"], "passing rollout speedup state is invalid")
        expected = evaluate_rollout_eligibility(runs, lineage, shard_count=shard_count)
        _require(expected["status"] == "PASS" and payload["speedup"] == expected["speedup"] and stability == expected["stability"], "passing rollout speedup is inconsistent")
    _require(payload["rollback"] == {"mode": "workflow_dispatch", "command": _ROLLBACK_COMMAND}, "parallel rollout rollback is invalid")
    _require(_is_sha(payload["evidenceFingerprint"], _SHA64), "parallel rollout evidence fingerprint is invalid")
    _require(canonical_fingerprint({key: payload[key] for key in payload if key != "evidenceFingerprint"}) == payload["evidenceFingerprint"], "parallel rollout evidence fingerprint mismatch")
