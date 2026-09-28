from __future__ import annotations

import pytest
from backend.agents.evidence_models import canonical_fingerprint


_POPULATION_NODEIDS = sorted(f"tests/test_parallel.py::test_case_{index:02d}" for index in range(16))


_LINEAGE = {
    "contractFingerprint": "c" * 64,
    "baselineFamilyId": "acceptance-full-serial-ci-v1",
    "manifestFingerprint": "d" * 64,
    "testPopulationFingerprint": canonical_fingerprint({"nodeids": _POPULATION_NODEIDS}),
    "runnerFingerprint": "f" * 64,
    "environmentFingerprint": "0" * 64,
    "datasetSnapshotFingerprint": "1" * 64,
    "selectionMode": "full",
}

_SOURCE_LINEAGE = {"commitSha": "a" * 40, "sourceFingerprint": "b" * 64, **_LINEAGE}


def _runner_capability(fingerprint: str) -> dict[str, object]:
    unsigned = {
        "schemaVersion": "acceptance-runner-capability-v1",
        "runnerFingerprint": fingerprint,
        "maxWorkers": 4,
    }
    return {**unsigned, "capabilityFingerprint": canonical_fingerprint(unsigned)}


def _runtime_lineage_record(canonical: dict[str, object], runtime: dict[str, str], artifact_fp: str) -> dict[str, object]:
    return {
        "canonical": dict(canonical),
        "runtimeExecutionLineage": dict(runtime),
        "runtimeArtifactFingerprint": artifact_fp,
    }


def test_threshold_is_applied_before_display_rounding():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(i, parallel=80.000001) for i in range(3)]
    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "speedup_threshold_not_met"
    assert result["speedup"] is None


def test_speedup_metrics_follow_run_index_not_input_order():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(2, parallel=70.0), _passing_run(0, parallel=50.0), _passing_run(1, parallel=60.0)]
    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)

    assert result["status"] == "PASS"
    assert result["speedup"]["speedRatio"] == [0.5, 0.6, 0.7]


def test_eligibility_rejects_invalid_runner_capability_receipt():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(index) for index in range(3)]
    runs[0]["runnerCapability"]["maxWorkers"] = 1

    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "runner_capability_receipt_invalid"
    assert result["speedup"] is None


def test_pass_run_requires_shard_aggregate_fingerprint_binding():
    from backend.agents.acceptance_performance_v2 import validate_parallel_run_for_artifact

    run = _passing_run(0)
    run["shardAggregateFingerprint"] = "9" * 64

    with pytest.raises(ValueError, match="shard_aggregate_evidence_invalid"):
        validate_parallel_run_for_artifact(
            run,
            expected_lineage=_LINEAGE,
            commit_sha=_SOURCE_LINEAGE["commitSha"],
            source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
            shard_count=4,
            blocked=False,
        )


def test_unbounded_speedup_is_rejected_before_eligibility():
    from backend.agents.acceptance_parallel_rollout import (
        MAX_DURATION_SECONDS, compute_speedup_metrics, evaluate_rollout_eligibility,
    )

    with pytest.raises(ValueError, match="speedup"):
        compute_speedup_metrics([MAX_DURATION_SECONDS], [1e-20])
    runs = [_passing_run(i, parallel=1e-20) for i in range(3)]
    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)
    assert result["status"] == "BLOCKED"
    assert result["speedup"] is None


def _passing_run(index: int, parallel: float = 75.0) -> dict[str, object]:
    serial_result = {"passed": 16, "failed": 0, "skipped": 0}
    aggregate_unsigned = {
        "schemaVersion": "full-pytest-shard-aggregate-v1",
        "status": "PASS",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "manifestFingerprint": _LINEAGE["manifestFingerprint"],
        "shardCount": 4,
        "coveredNodeids": 16,
        "shards": {str(shard): (str(shard) + "9") * 32 for shard in range(4)},
        "result": serial_result,
    }
    from backend.agents.evidence_models import canonical_fingerprint

    aggregate = {
        **aggregate_unsigned,
        "evidenceFingerprint": canonical_fingerprint(aggregate_unsigned),
    }
    parity_unsigned = {
        "schemaVersion": "serial-shard-parity-v1",
        "status": "PASS",
        "failureCode": None,
        "serial": serial_result,
        "shard": serial_result,
    }
    parity = {
        **parity_unsigned,
        "evidenceFingerprint": canonical_fingerprint(parity_unsigned),
    }
    return {
        "runIndex": index,
        "populationKind": "full-pytest-nodeid",
        "commitSha": _SOURCE_LINEAGE["commitSha"],
        "sourceFingerprint": _SOURCE_LINEAGE["sourceFingerprint"],
        **_LINEAGE,
        "testPopulationCount": 16,
        "populationNodeids": list(_POPULATION_NODEIDS),
        "shardCoverage": [
            {
                "shardIndex": shard,
                "assignedNodeids": [
                    nodeid for position, nodeid in enumerate(_POPULATION_NODEIDS)
                    if position % 4 == shard
                ],
                "executedNodeids": [
                    nodeid for position, nodeid in enumerate(_POPULATION_NODEIDS)
                    if position % 4 == shard
                ],
            }
            for shard in range(4)
        ],
        "serialResult": serial_result,
        "serialLineage": _runtime_lineage_record(
            _LINEAGE,
            {key: _LINEAGE[key] for key in ("runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint")},
            "7" * 64,
        ),
        "parallelLineage": {
            "canonical": dict(_LINEAGE),
            "runtimeExecutionLineage": [
                {key: _LINEAGE[key] for key in ("runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint")}
                for _ in range(4)
            ],
            "runtimeArtifactFingerprint": "8" * 64,
        },
        "serialRuntimeArtifactFingerprint": "7" * 64,
        "parallelRuntimeArtifactFingerprint": "8" * 64,
        "runnerCapability": _runner_capability(_LINEAGE["runnerFingerprint"]),
        "serialWallSeconds": 100.0,
        "parallelWallSeconds": parallel,
        "serialStatus": "PASS",
        "parallelStatus": "PASS",
        "cleanupEvidence": {
            "serial": {"status": "PASS", "allProcessGroupsTerminated": True},
            "parallel": {
                "status": "PASS",
                "allProcessGroupsTerminated": True,
                "runtimeCleanupConfirmed": True,
                "fixtureRootsRemoved": True,
            },
        },
        "parity": parity,
        "shardAggregate": aggregate,
        "serialArtifactFingerprint": "a" * 64,
        "parallelArtifactFingerprint": "b" * 64,
        "shardAggregateFingerprint": aggregate["evidenceFingerprint"],
    }


def _blocked_parity() -> dict[str, object]:
    unsigned = {
        "schemaVersion": "serial-shard-parity-v1",
        "status": "BLOCKED",
        "failureCode": "serial_or_shard_status_invalid",
        "serial": {},
        "shard": {},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _blocked_aggregate(*, covered: int = 0, result: dict[str, int] | None = None) -> dict[str, object]:
    unsigned = {
        "schemaVersion": "full-pytest-shard-aggregate-v1",
        "status": "BLOCKED",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": _SOURCE_LINEAGE["commitSha"],
        "sourceFingerprint": _SOURCE_LINEAGE["sourceFingerprint"],
        "manifestFingerprint": _LINEAGE["manifestFingerprint"],
        "shardCount": 4,
        "coveredNodeids": covered,
        "shards": {} if not covered else {"0": "9" * 64},
        "result": result or {"passed": 0, "failed": 0, "skipped": 0},
        "failureCode": "shard_set_incomplete",
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _blocked_run(run: dict[str, object], *, failure_code: str, max_workers: int | None = None) -> dict[str, object]:
    run["parallelStatus"] = "BLOCKED"
    run["parity"] = _blocked_parity()
    run["shardCoverage"] = []
    aggregate = _blocked_aggregate()
    run["shardAggregate"] = aggregate
    run["shardAggregateFingerprint"] = canonical_fingerprint(
        {key: value for key, value in aggregate.items() if key != "evidenceFingerprint"}
    )
    run["failureCode"] = failure_code
    run["cleanupEvidence"] = {
        "serial": {"status": "PASS", "allProcessGroupsTerminated": True},
        "parallel": {
            "status": "BLOCKED",
            "allProcessGroupsTerminated": False,
            "runtimeCleanupConfirmed": False,
            "fixtureRootsRemoved": False,
        },
    }
    if max_workers is not None:
        capability = dict(run["runnerCapability"])
        capability["maxWorkers"] = max_workers
        unsigned_capability = {
            key: capability[key]
            for key in ("schemaVersion", "runnerFingerprint", "maxWorkers")
        }
        capability["capabilityFingerprint"] = canonical_fingerprint(unsigned_capability)
        run["runnerCapability"] = capability
    return run


def _payload(measured_runs: list[dict[str, object]] | None = None) -> dict[str, object]:
    from backend.agents.acceptance_parallel_rollout import build_parallel_rollout_evidence

    return build_parallel_rollout_evidence(
        commit_sha=_SOURCE_LINEAGE["commitSha"],
        source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=(
            [_passing_run(0), _passing_run(1), _passing_run(2)]
            if measured_runs is None else measured_runs
        ),
    )


def test_valid_rollout_evidence_has_exact_keys_and_diagnostic_authority():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    payload = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)
    assert payload["schemaVersion"] == "acceptance-parallel-rollout-v1"
    assert payload["authority"] == "diagnostic"
    assert payload["formalReleaseEnabled"] is False
    assert payload["rolloutMode"] == "parallel_candidate"
    assert payload["populationKind"] == "full-pytest-nodeid"


@pytest.mark.parametrize("field", ["status", "rolloutCandidate", "speedup", "stability", "blockers"])
def test_validator_rejects_tampered_top_level_eligibility_fields(field):
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    payload = _payload()
    if field == "status":
        payload[field] = "BLOCKED"
    elif field == "rolloutCandidate":
        payload[field] = "ineligible"
    elif field == "speedup":
        payload[field]["speedRatio"][0] += 0.01
    elif field == "stability":
        payload[field]["maxSpeedRatio"] += 0.01
    else:
        payload[field] = ["speedup_threshold_not_met"]
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_builder_never_promotes_when_parity_is_blocked(monkeypatch):
    from backend.agents import acceptance_parallel_rollout as subject

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[0]["parity"] = _blocked_parity()

    monkeypatch.setattr(
        subject,
        "evaluate_rollout_eligibility",
        lambda *args, **kwargs: {
            "status": "PASS",
            "failureCode": None,
            "rolloutCandidate": "eligible",
            "speedup": {
                "speedRatio": [0.75, 0.75, 0.75],
                "speedupMultiple": [1.333333, 1.333333, 1.333333],
                "medianSpeedupMultiple": 1.333333,
            },
            "stability": {"status": "PASS", "measuredRunCount": 3},
            "blockers": [],
        },
    )

    payload = _payload(runs)

    assert payload["status"] == "BLOCKED"
    assert payload["speedup"] is None
    assert payload["rolloutCandidate"] == "ineligible"
    assert payload["parity"]["status"] == "BLOCKED"
    assert "parity_mismatch" in payload["blockers"]


def test_validator_requires_external_source_bound_lineage():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    payload = _payload()

    with pytest.raises(TypeError, match="expected_source_lineage"):
        validate_parallel_rollout_evidence(payload)

    wrong_lineage = dict(_SOURCE_LINEAGE)
    wrong_lineage["sourceFingerprint"] = "e" * 64
    with pytest.raises(ValueError, match="source lineage mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=wrong_lineage)


def test_validator_rejects_cross_source_shard_aggregate():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    payload = _payload()
    aggregate = payload["measuredRuns"][0]["shardAggregate"]
    aggregate["sourceFingerprint"] = "e" * 64
    aggregate["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in aggregate.items() if key != "evidenceFingerprint"}
    )
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )
    with pytest.raises(ValueError, match="shard_aggregate_source_identity_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_validates_bounded_partial_metadata():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = runs[0]
    run["parallelStatus"] = "BLOCKED"
    run["parity"] = _blocked_parity()
    coverage = run["shardCoverage"][0]
    coverage["executedNodeids"] = coverage["assignedNodeids"][:2]
    run["shardCoverage"] = [coverage]
    aggregate = _blocked_aggregate(covered=len(coverage["assignedNodeids"]), result={"passed": 2, "failed": 0, "skipped": 0})
    run["shardAggregate"] = aggregate
    run["shardAggregateFingerprint"] = canonical_fingerprint(
        {key: value for key, value in aggregate.items() if key != "evidenceFingerprint"}
    )
    run["failureCode"] = "shard_set_incomplete"
    run["cleanupEvidence"] = {
        "serial": {"status": "PASS", "allProcessGroupsTerminated": True},
        "parallel": {
            "status": "BLOCKED",
            "allProcessGroupsTerminated": False,
            "runtimeCleanupConfirmed": False,
            "fixtureRootsRemoved": False,
        },
    }

    payload = _payload(runs)

    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_with_null_durations_suppresses_speedup_without_arithmetic_error():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(index) for index in range(3)]
    blocked = _blocked_run(runs[0], failure_code="parallel_runner_error")
    blocked["serialStatus"] = "BLOCKED"
    blocked["serialWallSeconds"] = None
    blocked["parallelWallSeconds"] = None

    payload = _payload([blocked, runs[1], runs[2]])

    assert payload["status"] == "BLOCKED"
    assert payload["speedup"] is None
    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_rejects_empty_runtime_execution_lineage():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="parallel_runner_error")
    run["serialLineage"]["runtimeExecutionLineage"] = {}
    run["parallelLineage"]["runtimeExecutionLineage"] = []

    payload = _payload(runs)

    with pytest.raises(ValueError, match="artifact_lineage_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_accepts_explicit_source_bound_unobserved_parallel_lineage():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="parallel_runner_error")
    run["parallelLineage"]["runtimeExecutionLineage"] = {
        "observation": "unobserved",
        "failureCode": "parallel_runner_error",
        "commitSha": _SOURCE_LINEAGE["commitSha"],
        "sourceFingerprint": _SOURCE_LINEAGE["sourceFingerprint"],
    }

    payload = _payload(runs)

    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_rejects_parallel_runtime_lineage_above_shard_count():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="parallel_runner_error")
    runtime_lineage = run["parallelLineage"]["runtimeExecutionLineage"]
    runtime_lineage.append(dict(runtime_lineage[0]))
    payload = _payload(runs)

    with pytest.raises(ValueError, match="artifact_lineage_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_overall_blocked_flag_does_not_skip_passing_run_speedup_validation():
    from backend.agents.acceptance_performance_v2 import validate_parallel_run_for_artifact

    run = _passing_run(0)
    run["speedRatio"] = 0.123

    with pytest.raises(ValueError, match="measured run speedup is inconsistent"):
        validate_parallel_run_for_artifact(
            run,
            expected_lineage=_LINEAGE,
            commit_sha=_SOURCE_LINEAGE["commitSha"],
            source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
            shard_count=4,
            blocked=True,
        )


def test_blocked_rollout_can_omit_only_unrepresentable_run_speedup():
    from backend.agents.acceptance_performance_v2 import validate_parallel_run_for_artifact

    run = _passing_run(0, parallel=1e-20)

    validate_parallel_run_for_artifact(
        run,
        expected_lineage=_LINEAGE,
        commit_sha=_SOURCE_LINEAGE["commitSha"],
        source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
        shard_count=4,
        blocked=True,
    )

    run["speedRatio"] = 1e-22
    with pytest.raises(ValueError, match="measured run speedup is invalid"):
        validate_parallel_run_for_artifact(
            run,
            expected_lineage=_LINEAGE,
            commit_sha=_SOURCE_LINEAGE["commitSha"],
            source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
            shard_count=4,
            blocked=True,
        )


@pytest.mark.parametrize("field", ["failureCode", "cleanupEvidence"])
def test_blocked_run_requires_failure_and_cleanup_evidence(field):
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="parallel_runner_error")
    run.pop(field)
    payload = _payload(runs)

    with pytest.raises(ValueError, match="blocked_run_evidence_invalid"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_rejects_unobserved_lineage_from_another_source():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="parallel_runner_error")
    run["parallelLineage"]["runtimeExecutionLineage"] = {
        "observation": "unobserved",
        "failureCode": "parallel_runner_error",
        "commitSha": _SOURCE_LINEAGE["commitSha"],
        "sourceFingerprint": "9" * 64,
    }

    payload = _payload(runs)

    with pytest.raises(ValueError, match="artifact_lineage_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_rejects_partial_parallel_lineage_from_another_environment():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="parallel_runner_error")
    partial = dict(run["parallelLineage"]["runtimeExecutionLineage"][0])
    partial["environmentFingerprint"] = "9" * 64
    run["parallelLineage"]["runtimeExecutionLineage"] = [partial]

    payload = _payload(runs)

    with pytest.raises(ValueError, match="artifact_lineage_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_aggregate_requires_its_own_evidence_fingerprint():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="shard_set_incomplete")
    aggregate = run["shardAggregate"]
    aggregate.pop("evidenceFingerprint")
    run["shardAggregateFingerprint"] = canonical_fingerprint(aggregate)
    payload = _payload(runs)

    with pytest.raises(ValueError, match="shard_aggregate_evidence_invalid"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_accepts_valid_runner_receipt_below_required_capacity():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[0] = _blocked_run(
        runs[0], failure_code="runner_capability_insufficient", max_workers=3
    )
    payload = _payload(runs)

    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_rollout_requires_full_lineage_for_a_passing_parallel_stage():
    from backend.agents.acceptance_performance_v2 import validate_parallel_run_for_artifact

    run = _passing_run(0)
    run["parallelLineage"]["runtimeExecutionLineage"].pop()

    with pytest.raises(ValueError, match="artifact_lineage_mismatch"):
        validate_parallel_run_for_artifact(
            run,
            expected_lineage=_LINEAGE,
            commit_sha=_SOURCE_LINEAGE["commitSha"],
            source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
            shard_count=4,
            blocked=True,
        )


def test_blocked_rollout_enforces_capacity_for_a_passing_parallel_stage():
    from backend.agents.acceptance_performance_v2 import validate_parallel_run_for_artifact

    run = _passing_run(0)
    capability = dict(run["runnerCapability"])
    capability["maxWorkers"] = 3
    unsigned_capability = {
        key: capability[key]
        for key in ("schemaVersion", "runnerFingerprint", "maxWorkers")
    }
    capability["capabilityFingerprint"] = canonical_fingerprint(unsigned_capability)
    run["runnerCapability"] = capability

    with pytest.raises(ValueError, match="runner_capability_insufficient"):
        validate_parallel_run_for_artifact(
            run,
            expected_lineage=_LINEAGE,
            commit_sha=_SOURCE_LINEAGE["commitSha"],
            source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
            shard_count=4,
            blocked=True,
        )


def test_blocked_parallel_stage_cannot_claim_pass_parity():
    from backend.agents.acceptance_performance_v2 import validate_parallel_run_for_artifact

    run = _blocked_run(_passing_run(0), failure_code="shard_set_incomplete")
    run["parity"] = _passing_run(0)["parity"]

    with pytest.raises(ValueError, match="parity_status_inconsistent"):
        validate_parallel_run_for_artifact(
            run,
            expected_lineage=_LINEAGE,
            commit_sha=_SOURCE_LINEAGE["commitSha"],
            source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
            shard_count=4,
            blocked=True,
        )


def test_blocked_aggregate_must_match_expected_manifest_lineage():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = _blocked_run(runs[0], failure_code="shard_set_incomplete")
    aggregate = run["shardAggregate"]
    aggregate["manifestFingerprint"] = "2" * 64
    run["shardAggregateFingerprint"] = canonical_fingerprint(
        {key: value for key, value in aggregate.items() if key != "evidenceFingerprint"}
    )
    payload = _payload(runs)

    with pytest.raises(ValueError, match="shard_aggregate_manifest_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_artifact_rejects_contradictory_partial_metadata():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    run = runs[0]
    run["parallelStatus"] = "BLOCKED"
    run["parity"] = _blocked_parity()
    run["failureCode"] = "shard_set_incomplete"
    run["shardAggregate"] = _blocked_aggregate(covered=0, result={"passed": 1, "failed": 0, "skipped": 0})
    run["shardAggregateFingerprint"] = canonical_fingerprint(
        {key: value for key, value in run["shardAggregate"].items() if key != "evidenceFingerprint"}
    )
    payload = _payload(runs)

    with pytest.raises(ValueError, match="blocked aggregate metadata is inconsistent"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_builder_rejects_missing_runtime_artifact_lineage():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    for run in runs:
        run.pop("serialLineage")
        run.pop("parallelLineage")
    with pytest.raises(ValueError, match="measured run is invalid: artifact_lineage_mismatch"):
        validate_parallel_rollout_evidence(
            _payload(runs),
            expected_source_lineage=_SOURCE_LINEAGE,
        )


def test_speedup_uses_wall_clock_critical_path():
    from backend.agents.acceptance_parallel_rollout import compute_speedup_metrics

    metrics = compute_speedup_metrics([100.0, 110.0, 90.0], [75.0, 80.0, 70.0])
    assert metrics["speedRatio"] == [0.75, 0.727273, 0.777778]
    assert metrics["speedupMultiple"] == [1.333333, 1.375, 1.285714]
    assert metrics["medianSpeedupMultiple"] == 1.333333


def test_lineage_or_parity_mismatch_blocks_and_suppresses_speedup():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["manifestFingerprint"] = "9" * 64
    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "lineage_mismatch"
    assert result["speedup"] is None


def test_run_source_identity_mismatch_blocks_eligibility():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["sourceFingerprint"] = "e" * 64
    result = evaluate_rollout_eligibility(
        runs, _LINEAGE, shard_count=4,
        expected_commit_sha=_SOURCE_LINEAGE["commitSha"],
        expected_source_fingerprint=_SOURCE_LINEAGE["sourceFingerprint"],
    )
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "run_source_identity_mismatch"
    assert result["speedup"] is None


def test_72_slot_population_is_not_a_full_pytest_candidate():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[0]["populationKind"] = "agent-eval-72-slot"
    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "population_kind_mismatch"


def test_three_clean_runs_require_each_ratio_at_or_below_point_eight():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    result = evaluate_rollout_eligibility(
        [_passing_run(0, parallel=75), _passing_run(1, parallel=80), _passing_run(2, parallel=79)],
        _LINEAGE,
        shard_count=4,
    )
    assert result["status"] == "PASS"
    assert result["rolloutCandidate"] == "eligible"
    assert result["stability"]["measuredRunCount"] == 3


def test_slow_run_is_ineligible_even_when_other_runs_are_fast():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    result = evaluate_rollout_eligibility(
        [_passing_run(0, parallel=75), _passing_run(1, parallel=81), _passing_run(2, parallel=75)],
        _LINEAGE,
        shard_count=4,
    )
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "speedup_threshold_not_met"


def test_blocked_payload_cannot_carry_usable_speedup():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["parity"] = _blocked_parity()
    runs[1]["failureCode"] = "serial_or_shard_status_invalid"
    payload = _payload(runs)
    assert payload["status"] == "BLOCKED"
    assert payload["speedup"] is None
    assert all(
        {"speedRatio", "speedupMultiple"}.issubset(run)
        for run in (payload["measuredRuns"][0], payload["measuredRuns"][2])
    )
    assert not ({"speedRatio", "speedupMultiple"} & payload["measuredRuns"][1].keys())
    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_builder_blocks_invalid_per_run_speedup_without_losing_artifact():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    payload = _payload([
        _passing_run(0, parallel=1e-20),
        _passing_run(1),
        _passing_run(2),
    ])

    assert payload["status"] == "BLOCKED"
    assert payload["speedup"] is None
    assert "speedup_metric_invalid" in payload["blockers"]
    assert not ({"speedRatio", "speedupMultiple"} & payload["measuredRuns"][0].keys())
    assert payload["measuredRuns"][1]["speedupMultiple"] == 1.333333
    validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_builder_recomputes_eligibility_when_run_failure_code_is_present():
    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["failureCode"] = "serial_or_shard_status_invalid"

    payload = _payload(runs)

    assert payload["status"] == "BLOCKED"
    assert payload["rolloutCandidate"] == "ineligible"
    assert payload["speedup"] is None
    assert payload["stability"]["status"] == "BLOCKED"
    assert "serial_or_shard_status_invalid" in payload["blockers"]
    assert not ({"speedRatio", "speedupMultiple"} & payload["measuredRuns"][1].keys())
    assert all(
        {"speedRatio", "speedupMultiple"}.issubset(run)
        for run in (payload["measuredRuns"][0], payload["measuredRuns"][2])
    )


def test_blocked_payload_still_requires_common_run_identity_and_durations():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["parity"] = _blocked_parity()
    runs[1]["failureCode"] = "serial_or_shard_status_invalid"
    payload = _payload(runs)
    del payload["measuredRuns"][1]["serialArtifactFingerprint"]
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="artifact_missing"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_payload_requires_consistent_parity_and_blocked_stability():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    payload = _payload([_passing_run(0, 81.0), _passing_run(1), _passing_run(2)])
    assert payload["status"] == "BLOCKED"
    assert payload["parity"]["status"] == "PASS"
    payload["stability"]["status"] = "PASS"
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="blocked rollout speedup state"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_blocked_payload_must_recompute_to_a_real_failure():
    from backend.agents.acceptance_parallel_rollout import validate_parallel_rollout_evidence

    payload = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    payload["status"] = "BLOCKED"
    payload["speedup"] = None
    payload["stability"] = {"status": "BLOCKED", "measuredRunCount": 3}
    payload["rolloutCandidate"] = "ineligible"
    payload["blockers"] = ["fabricated_blocker"]
    for run in payload["measuredRuns"]:
        run.pop("speedRatio", None)
        run.pop("speedupMultiple", None)
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="measured run speedup is inconsistent"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_validator_rejects_pass_parity_counts_that_do_not_match_run_results():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    payload = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    parity = payload["measuredRuns"][0]["parity"]
    parity["serial"] = {"passed": 15, "failed": 0, "skipped": 0}
    parity_unsigned = {key: value for key, value in parity.items() if key != "evidenceFingerprint"}
    parity["evidenceFingerprint"] = canonical_fingerprint(parity_unsigned)
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="serial_parity_evidence_mismatch"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_validator_requires_exact_parity_and_stability_run_counts():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    payload = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    payload["parity"]["measuredRunCount"] = 2
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="parity run count"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_validator_rejects_duplicate_or_unknown_population_nodeids():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    payload = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    payload["measuredRuns"][0]["shardCoverage"][0]["assignedNodeids"][-1] = "tests/test_parallel.py::unknown"
    payload["measuredRuns"][0]["shardCoverage"][0]["executedNodeids"][-1] = "tests/test_parallel.py::unknown"
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="shard_coverage_invalid"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)

    duplicate = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    duplicate["measuredRuns"][0]["shardCoverage"][0]["assignedNodeids"][-1] = duplicate["measuredRuns"][0]["shardCoverage"][0]["assignedNodeids"][0]
    duplicate["measuredRuns"][0]["shardCoverage"][0]["executedNodeids"][-1] = duplicate["measuredRuns"][0]["shardCoverage"][0]["executedNodeids"][0]
    duplicate["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in duplicate.items() if key != "evidenceFingerprint"}
    )
    with pytest.raises(ValueError, match="shard_coverage_invalid"):
        validate_parallel_rollout_evidence(duplicate, expected_source_lineage=_SOURCE_LINEAGE)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("runIndex", [], "run_index_invalid"),
        ("populationNodeids", [["not-a-nodeid"]], "population_nodeids_invalid"),
        ("shardCoverage", "not-a-list", "shard_coverage_invalid"),
    ],
)
def test_validator_fails_closed_for_malformed_untrusted_collections(field, value, message):
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    payload = _payload([_passing_run(0), _passing_run(1), _passing_run(2)])
    payload["measuredRuns"][0][field] = value
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match=message):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_invalid_duration_is_blocked_before_speedup_calculation():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    run = _passing_run(0)
    run["serialWallSeconds"] = 0
    result = evaluate_rollout_eligibility([run, _passing_run(1), _passing_run(2)], _LINEAGE, shard_count=4)
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "duration_invalid"
    assert result["speedup"] is None


def test_blocked_payload_rejects_partial_execution_for_pass_aggregate():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["parity"] = _blocked_parity()
    payload = _payload(runs)
    coverage = payload["measuredRuns"][0]["shardCoverage"][0]
    coverage["executedNodeids"] = coverage["executedNodeids"][:-1]
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="shard_coverage_invalid"):
        validate_parallel_rollout_evidence(payload, expected_source_lineage=_SOURCE_LINEAGE)


def test_eligibility_fails_closed_for_malformed_run_index_types():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["runIndex"] = "1"

    result = evaluate_rollout_eligibility(runs, _LINEAGE, shard_count=4)

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "run_index_invalid"


def test_speedup_multiple_uses_metric_bound_not_duration_bound():
    from backend.agents.acceptance_parallel_rollout import (
        MAX_SPEEDUP_METRIC,
        _is_speedup_metric,
    )

    assert _is_speedup_metric(MAX_SPEEDUP_METRIC)
    assert not _is_speedup_metric(MAX_SPEEDUP_METRIC + 1)
