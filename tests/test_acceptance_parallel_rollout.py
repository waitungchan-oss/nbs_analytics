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
        "serialLineage": dict(_LINEAGE),
        "parallelLineage": dict(_LINEAGE),
        "serialWallSeconds": 100.0,
        "parallelWallSeconds": parallel,
        "serialStatus": "PASS",
        "parallelStatus": "PASS",
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


def test_valid_rollout_evidence_has_exact_keys_and_diagnostic_authority():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=[_passing_run(0), _passing_run(1), _passing_run(2)],
    )
    validate_parallel_rollout_evidence(payload)
    assert payload["schemaVersion"] == "acceptance-parallel-rollout-v1"
    assert payload["authority"] == "diagnostic"
    assert payload["formalReleaseEnabled"] is False
    assert payload["rolloutMode"] == "parallel_candidate"
    assert payload["populationKind"] == "full-pytest-nodeid"


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
    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=runs,
    )
    assert payload["status"] == "BLOCKED"
    assert payload["speedup"] is None
    validate_parallel_rollout_evidence(payload)


def test_blocked_payload_still_requires_common_run_identity_and_durations():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    runs = [_passing_run(0), _passing_run(1), _passing_run(2)]
    runs[1]["parity"] = _blocked_parity()
    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=runs,
    )
    del payload["measuredRuns"][1]["serialArtifactFingerprint"]
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="artifact_missing"):
        validate_parallel_rollout_evidence(payload)


def test_blocked_payload_requires_consistent_parity_and_blocked_stability():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=[_passing_run(0, 81.0), _passing_run(1), _passing_run(2)],
    )
    assert payload["status"] == "BLOCKED"
    assert payload["parity"]["status"] == "PASS"
    payload["stability"]["status"] = "PASS"
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="blocked rollout speedup state"):
        validate_parallel_rollout_evidence(payload)


def test_validator_requires_exact_parity_and_stability_run_counts():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )
    from backend.agents.evidence_models import canonical_fingerprint

    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=[_passing_run(0), _passing_run(1), _passing_run(2)],
    )
    payload["parity"]["measuredRunCount"] = 2
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="parity run count"):
        validate_parallel_rollout_evidence(payload)


def test_validator_rejects_duplicate_or_unknown_population_nodeids():
    from backend.agents.acceptance_parallel_rollout import (
        build_parallel_rollout_evidence,
        validate_parallel_rollout_evidence,
    )

    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=[_passing_run(0), _passing_run(1), _passing_run(2)],
    )
    payload["measuredRuns"][0]["shardCoverage"][0]["assignedNodeids"][-1] = "tests/test_parallel.py::unknown"
    payload["measuredRuns"][0]["shardCoverage"][0]["executedNodeids"][-1] = "tests/test_parallel.py::unknown"
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="shard_coverage_invalid"):
        validate_parallel_rollout_evidence(payload)


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

    payload = build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        contract_fingerprint=_LINEAGE["contractFingerprint"],
        baseline_family_id=_LINEAGE["baselineFamilyId"],
        manifest_fingerprint=_LINEAGE["manifestFingerprint"],
        test_population_fingerprint=_LINEAGE["testPopulationFingerprint"],
        runner_fingerprint=_LINEAGE["runnerFingerprint"],
        environment_fingerprint=_LINEAGE["environmentFingerprint"],
        dataset_snapshot_fingerprint=_LINEAGE["datasetSnapshotFingerprint"],
        selection_mode=_LINEAGE["selectionMode"],
        shard_count=4,
        measured_runs=[_passing_run(0), _passing_run(1), _passing_run(2)],
    )
    payload["measuredRuns"][0][field] = value
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match=message):
        validate_parallel_rollout_evidence(payload)


def test_invalid_duration_is_blocked_before_speedup_calculation():
    from backend.agents.acceptance_parallel_rollout import evaluate_rollout_eligibility

    run = _passing_run(0)
    run["serialWallSeconds"] = 0
    result = evaluate_rollout_eligibility([run, _passing_run(1), _passing_run(2)], _LINEAGE, shard_count=4)
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "duration_invalid"
    assert result["speedup"] is None


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
