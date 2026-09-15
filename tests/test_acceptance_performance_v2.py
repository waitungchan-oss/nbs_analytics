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
