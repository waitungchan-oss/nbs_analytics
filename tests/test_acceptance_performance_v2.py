from __future__ import annotations

from copy import deepcopy

import pytest


COMMIT = "b" * 40
SOURCE = "c" * 64
CONTRACT = "a" * 64
MANIFEST = "d" * 64
POPULATION = "e" * 64
RUNNER = "f" * 64
ENVIRONMENT = "0" * 64
DATASET = "1" * 64


def _v2(**overrides):
    from backend.agents.acceptance_performance_v2 import build_performance_baseline_v2

    values = {
        "baseline_family_id": "acceptance-full-2026-09",
        "baseline_role": "serial_control",
        "lifecycle": "qualified",
        "contract_fingerprint": CONTRACT,
        "commit_sha": COMMIT,
        "source_fingerprint": SOURCE,
        "manifest_fingerprint": MANIFEST,
        "test_population_fingerprint": POPULATION,
        "runner_fingerprint": RUNNER,
        "environment_fingerprint": ENVIRONMENT,
        "dataset_snapshot_fingerprint": DATASET,
        "selection_mode": "full",
        "stages": {
            "collectionSeconds": 2.0,
            "fixturePreparationSeconds": 4.0,
            "pytestExecutionSeconds": 80.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": 87.0,
        },
        "test_count": {"collected": 100, "passed": 99, "failed": 0, "skipped": 1},
    }
    values.update(overrides)
    return build_performance_baseline_v2(**values)


def test_v2_comparison_allows_cross_commit_same_contract():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    result = compare_performance_baselines_v2(
        _v2(),
        _v2(
            baseline_role="parallel_candidate",
            commit_sha="9" * 40,
            source_fingerprint="8" * 64,
            stages={
                "collectionSeconds": 1.0,
                "fixturePreparationSeconds": 3.0,
                "pytestExecutionSeconds": 60.0,
                "aggregateSeconds": 1.0,
                "totalWallSeconds": 65.0,
            },
        ),
    )

    assert result["status"] == "compared"
    assert result["totalSpeedRatio"] == pytest.approx(65.0 / 87.0)
    assert "commit" not in result.get("reason", "")


def test_v2_comparison_rounds_total_speed_metrics_to_six_decimals():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    baseline = _v2()
    candidate = _v2(
        baseline_role="parallel_candidate",
        stages={
            "collectionSeconds": 1.0,
            "fixturePreparationSeconds": 3.0,
            "pytestExecutionSeconds": 60.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": 65.0,
        },
    )

    comparison = compare_performance_baselines_v2(baseline, candidate)

    assert comparison["totalSpeedRatio"] == round(65.0 / 87.0, 6) == 0.747126
    assert comparison["totalSpeedupMultiple"] == round(87.0 / 65.0, 6) == 1.338462


def test_v2_comparison_suppresses_metrics_lost_to_six_decimal_precision():
    from backend.agents.acceptance_performance import MAX_DURATION_SECONDS
    from backend.agents.acceptance_performance_v2 import (
        MAX_SPEEDUP_METRIC,
        compare_performance_baselines_v2,
        validate_performance_baseline_v2,
    )

    long_stages = {
        "collectionSeconds": 0.0,
        "fixturePreparationSeconds": 0.0,
        "pytestExecutionSeconds": 0.0,
        "aggregateSeconds": 0.0,
        "totalWallSeconds": MAX_DURATION_SECONDS,
    }
    shortest_positive_stages = {
        **long_stages,
        "totalWallSeconds": 0.000001,
    }
    baseline = _v2(stages=long_stages)
    candidate = _v2(baseline_role="parallel_candidate", stages=shortest_positive_stages)

    comparison = compare_performance_baselines_v2(baseline, candidate)
    expected_maximum = MAX_DURATION_SECONDS / 0.000001
    assert MAX_SPEEDUP_METRIC >= expected_maximum
    assert comparison["status"] == "not_compared"
    assert comparison["reason"] == "speedup_metric_precision_lost"
    assert "totalSpeedRatio" not in comparison
    assert "totalSpeedupMultiple" not in comparison

    compared_artifact = _v2(
        baseline_role="parallel_candidate",
        stages=shortest_positive_stages,
        comparison=comparison,
    )
    validate_performance_baseline_v2(compared_artifact)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("contract_fingerprint", "contract_mismatch"),
        ("manifest_fingerprint", "manifest_mismatch"),
        ("test_population_fingerprint", "population_mismatch"),
        ("runner_fingerprint", "runner_mismatch"),
        ("environment_fingerprint", "environment_mismatch"),
        ("dataset_snapshot_fingerprint", "dataset_mismatch"),
    ],
)
def test_v2_comparison_is_fail_closed_for_lineage_mismatch(field, reason):
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    result = compare_performance_baselines_v2(
        _v2(), _v2(baseline_role="parallel_candidate", **{field: "2" * 64})
    )

    assert result["status"] == "not_compared"
    assert result["reason"] == reason
    assert "totalSpeedRatio" not in result


def test_v2_comparison_is_fail_closed_for_baseline_family_mismatch():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    result = compare_performance_baselines_v2(
        _v2(),
        _v2(baseline_role="parallel_candidate", baseline_family_id="acceptance-full-2026-10"),
    )

    assert result["status"] == "not_compared"
    assert result["reason"] == "baseline_family_mismatch"
    assert "totalSpeedRatio" not in result


def test_v2_comparison_is_fail_closed_for_selection_mode_mismatch():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    result = compare_performance_baselines_v2(
        _v2(), _v2(baseline_role="parallel_candidate", selection_mode="shard")
    )

    assert result["status"] == "not_compared"
    assert result["reason"] == "selection_mode_mismatch"
    assert "totalSpeedRatio" not in result


def test_v2_result_count_changes_do_not_break_population_comparison():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    result = compare_performance_baselines_v2(
        _v2(),
        _v2(
            baseline_role="parallel_candidate",
            test_count={"collected": 100, "passed": 98, "failed": 1, "skipped": 1},
        ),
    )

    assert result["status"] == "compared"
    assert result["failureRate"] == 0.01
    assert result["totalSpeedRatio"] == 1.0


def test_v2_artifact_is_versioned_bounded_and_diagnostic():
    from backend.agents.acceptance_performance_v2 import (
        build_comparability_key,
        validate_performance_baseline_v2,
    )

    value = _v2()
    validate_performance_baseline_v2(value)

    assert value["schemaVersion"] == "acceptance-performance-baseline-v2"
    assert value["authority"] == "diagnostic"
    assert value["fullGateRequired"] is True
    assert len(build_comparability_key(value)) == 64


def test_execution_performance_preserves_selection_mode_lineage():
    from backend.agents.acceptance_performance_v2 import (
        build_execution_performance_v2,
        validate_performance_baseline_v2,
    )

    lineage = {
        "contractFingerprint": CONTRACT,
        "manifestFingerprint": MANIFEST,
        "testPopulationFingerprint": POPULATION,
        "runnerFingerprint": RUNNER,
        "environmentFingerprint": ENVIRONMENT,
        "datasetSnapshotFingerprint": DATASET,
        "selectionMode": "shard",
    }

    payload = build_execution_performance_v2(
        role="parallel_candidate",
        total_seconds=10.0,
        result={"passed": 10, "failed": 0, "skipped": 0},
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        baseline_family_id="acceptance-full-2026-09",
        lineage=lineage,
    )

    validate_performance_baseline_v2(payload)
    assert {key: payload[key] for key in lineage} == lineage


def test_v2_comparison_accepts_rounding_of_reciprocal_speedup_metrics():
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2

    value = _v2(
        comparison={
            "status": "compared",
            "baselineFingerprint": "1" * 64,
            "candidateFingerprint": "2" * 64,
            "stageDeltasSeconds": {
                "collectionSeconds": -1.0,
                "fixturePreparationSeconds": -1.0,
                "pytestExecutionSeconds": -1.0,
                "aggregateSeconds": 0.0,
                "totalWallSeconds": -2.0,
            },
            "totalSpeedRatio": 0.333333,
            "totalSpeedupMultiple": 3.0,
        }
    )

    validate_performance_baseline_v2(value)
    assert value["comparison"]["totalSpeedRatio"] == 0.333333
    assert value["comparison"]["totalSpeedupMultiple"] == 3.0


def test_v2_compared_artifact_requires_both_reciprocal_speedup_metrics():
    from backend.agents.evidence_models import canonical_fingerprint
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2

    value = _v2()
    unsigned = {key: item for key, item in value.items() if key != "evidenceFingerprint"}
    unsigned["comparison"] = {
        "status": "compared",
        "baselineFingerprint": "1" * 64,
        "candidateFingerprint": "2" * 64,
        "stageDeltasSeconds": {
            "collectionSeconds": -1.0,
            "fixturePreparationSeconds": -1.0,
            "pytestExecutionSeconds": -1.0,
            "aggregateSeconds": 0.0,
            "totalWallSeconds": -2.0,
        },
        "totalSpeedRatio": 0.5,
    }
    value = {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}

    with pytest.raises(ValueError, match="comparison fields are incomplete"):
        validate_performance_baseline_v2(value)


def test_v2_comparison_suppresses_unrepresentable_extreme_reciprocal_metrics():
    from backend.agents.acceptance_performance import MAX_DURATION_SECONDS
    from backend.agents.acceptance_performance_v2 import (
        compare_performance_baselines_v2,
        validate_performance_baseline_v2,
    )

    baseline = _v2(stages={
        "collectionSeconds": 0.001,
        "fixturePreparationSeconds": 0.001,
        "pytestExecutionSeconds": 0.001,
        "aggregateSeconds": 0.001,
        "totalWallSeconds": 0.001,
    })
    candidate = _v2(
        baseline_role="parallel_candidate",
        stages={
            "collectionSeconds": 1.0,
            "fixturePreparationSeconds": 1.0,
            "pytestExecutionSeconds": 1.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": float(MAX_DURATION_SECONDS),
        },
    )

    comparison = compare_performance_baselines_v2(baseline, candidate)
    assert comparison["status"] == "not_compared"
    assert comparison["reason"] == "speedup_metric_precision_lost"
    assert "totalSpeedRatio" not in comparison
    assert "totalSpeedupMultiple" not in comparison
    artifact = _v2(
        baseline_role="parallel_candidate",
        stages=candidate["stages"],
        comparison=comparison,
    )
    validate_performance_baseline_v2(artifact)


def test_v2_comparison_rejects_incompatible_rounded_reciprocal_metrics():
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2

    with pytest.raises(ValueError, match="inconsistent"):
        _v2(comparison={
            "status": "compared",
            "baselineFingerprint": "1" * 64,
            "candidateFingerprint": "2" * 64,
            "stageDeltasSeconds": {
                "collectionSeconds": 0.0,
                "fixturePreparationSeconds": 0.0,
                "pytestExecutionSeconds": 0.0,
                "aggregateSeconds": 0.0,
                "totalWallSeconds": 0.0,
            },
            "totalSpeedRatio": 0.5,
            "totalSpeedupMultiple": 1.9,
        })


def test_v2_non_qualified_reference_cannot_produce_ratio():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    for lifecycle in ("draft", "retired", "invalidated"):
        result = compare_performance_baselines_v2(_v2(lifecycle=lifecycle), _v2())
        assert result["status"] == "not_compared"
        assert result["reason"] == "baseline_not_qualified"
        assert "totalSpeedRatio" not in result


def test_v2_candidate_must_be_qualified():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    for lifecycle in ("draft", "retired", "invalidated"):
        result = compare_performance_baselines_v2(
            _v2(), _v2(baseline_role="parallel_candidate", lifecycle=lifecycle)
        )
        assert result["status"] == "not_compared"
        assert result["reason"] == "candidate_not_qualified"
        assert "totalSpeedRatio" not in result


def test_v2_comparison_enforces_serial_reference_and_candidate_roles():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    baseline_role_result = compare_performance_baselines_v2(
        _v2(baseline_role="parallel_candidate"),
        _v2(baseline_role="parallel_candidate"),
    )
    assert baseline_role_result["status"] == "not_compared"
    assert baseline_role_result["reason"] == "baseline_role_invalid"

    candidate_role_result = compare_performance_baselines_v2(
        _v2(), _v2(baseline_role="serial_control")
    )
    assert candidate_role_result["status"] == "not_compared"
    assert candidate_role_result["reason"] == "candidate_role_invalid"


def test_v2_validator_rejects_tampering_and_unknown_top_level_keys():
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2

    value = _v2()
    tampered = deepcopy(value)
    tampered["contractFingerprint"] = "2" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        validate_performance_baseline_v2(tampered)

    unknown = _v2()
    unknown["commitMessage"] = "not accepted"
    with pytest.raises(ValueError, match="schema"):
        validate_performance_baseline_v2(unknown)


def test_v2_validator_requires_state_consistent_comparison_fields():
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2

    compared_missing_metrics = _v2()
    compared_missing_metrics["comparison"] = {"status": "compared"}
    with pytest.raises(ValueError, match="comparison"):
        validate_performance_baseline_v2(compared_missing_metrics)

    not_compared_with_ratio = _v2()
    not_compared_with_ratio["comparison"] = {
        "status": "not_compared",
        "reason": "contract_mismatch",
        "baselineFingerprint": "2" * 64,
        "candidateFingerprint": "3" * 64,
        "totalSpeedRatio": 1.0,
    }
    with pytest.raises(ValueError, match="comparison"):
        validate_performance_baseline_v2(not_compared_with_ratio)


def test_v2_payload_is_rejected_by_formal_release_gate_validator():
    from backend.agents.acceptance_performance_v2 import build_performance_baseline_v2
    from backend.agents.release_gate_models import (
        ReleaseGateValidationError,
        validate_release_gate_evidence,
    )

    payload = build_performance_baseline_v2(
        baseline_family_id="acceptance-full-2026-09",
        baseline_role="serial_control",
        lifecycle="qualified",
        contract_fingerprint=CONTRACT,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest_fingerprint=MANIFEST,
        test_population_fingerprint=POPULATION,
        runner_fingerprint=RUNNER,
        environment_fingerprint=ENVIRONMENT,
        dataset_snapshot_fingerprint=DATASET,
        selection_mode="full",
        stages={
            "collectionSeconds": 2.0,
            "fixturePreparationSeconds": 4.0,
            "pytestExecutionSeconds": 80.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": 87.0,
        },
        test_count={"collected": 100, "passed": 99, "failed": 0, "skipped": 1},
    )

    with pytest.raises(ReleaseGateValidationError, match="evidence schema"):
        validate_release_gate_evidence(payload, COMMIT, SOURCE)
