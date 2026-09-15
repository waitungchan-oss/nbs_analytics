from __future__ import annotations

from copy import deepcopy
import json

import pytest


COMMIT = "a" * 40
SOURCE = "b" * 64
RUNNER = "c" * 64


def _baseline(**overrides):
    from backend.agents.acceptance_performance import build_performance_baseline

    values = {
        "commit_sha": COMMIT,
        "source_fingerprint": SOURCE,
        "runner_fingerprint": RUNNER,
        "stages": {
            "collectionSeconds": 2.0,
            "fixturePreparationSeconds": 4.0,
            "pytestExecutionSeconds": 80.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": 87.0,
        },
        "test_count": {"collected": 100, "passed": 99, "failed": 0, "skipped": 1},
        "selection_mode": "full",
    }
    values.update(overrides)
    return build_performance_baseline(**values)


def test_performance_baseline_is_bounded_and_diagnostic():
    from backend.agents.acceptance_performance import validate_performance_baseline

    result = _baseline()
    validate_performance_baseline(result)
    assert result["schemaVersion"] == "acceptance-performance-baseline-v1"
    assert result["authority"] == "diagnostic"
    assert result["fullGateRequired"] is True
    assert set(result["stages"]) == {
        "collectionSeconds",
        "fixturePreparationSeconds",
        "pytestExecutionSeconds",
        "aggregateSeconds",
        "totalWallSeconds",
    }


def test_performance_comparison_rejects_cross_runner_or_source():
    from backend.agents.acceptance_performance import compare_performance_baselines

    with pytest.raises(ValueError, match="runner"):
        compare_performance_baselines(_baseline(), _baseline(runner_fingerprint="d" * 64))
    with pytest.raises(ValueError, match="source"):
        compare_performance_baselines(_baseline(), _baseline(source_fingerprint="e" * 64))
    with pytest.raises(ValueError, match="commit"):
        compare_performance_baselines(_baseline(), _baseline(commit_sha="d" * 40))


def test_performance_comparison_rejects_incompatible_test_population():
    from backend.agents.acceptance_performance import compare_performance_baselines

    result = compare_performance_baselines(
        _baseline(),
        _baseline(test_count={"collected": 101, "passed": 100, "failed": 0, "skipped": 1}),
    )

    assert result["status"] == "ineligible"
    assert result["reason"] == "test_population_mismatch"
    assert "stageDeltasSeconds" not in result


def test_performance_validation_rejects_negative_or_non_finite_duration():
    from backend.agents.acceptance_performance import validate_performance_baseline

    for value in (-1.0, float("nan"), float("inf")):
        payload = _baseline()
        payload["stages"]["pytestExecutionSeconds"] = value
        with pytest.raises(ValueError, match="duration"):
            validate_performance_baseline(payload)


def test_performance_validation_rejects_invalid_identity_counts_and_mode():
    from backend.agents.acceptance_performance import validate_performance_baseline

    invalid_payloads = []
    for field, value in (
        ("commitSha", "not-a-sha"),
        ("sourceFingerprint", "d" * 63),
        ("runnerFingerprint", "e" * 65),
        ("selectionMode", "unknown"),
        ("selectionMode", ["full"]),
    ):
        payload = _baseline()
        payload[field] = value
        invalid_payloads.append(payload)
    payload = _baseline()
    payload["testCount"]["collected"] = True
    invalid_payloads.append(payload)
    payload = _baseline()
    payload["testCount"]["passed"] = -1
    invalid_payloads.append(payload)
    payload = _baseline()
    payload["comparison"]["status"] = []
    invalid_payloads.append(payload)
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            validate_performance_baseline(payload)


def test_performance_comparison_returns_deltas_without_mutating_inputs():
    from backend.agents.acceptance_performance import compare_performance_baselines

    baseline = _baseline()
    candidate = _baseline(
        stages={
            "collectionSeconds": 1.0,
            "fixturePreparationSeconds": 3.0,
            "pytestExecutionSeconds": 60.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": 65.0,
        }
    )
    baseline_before = deepcopy(baseline)
    candidate_before = deepcopy(candidate)

    result = compare_performance_baselines(baseline, candidate)

    assert result["status"] == "compared"
    assert result["stageDeltasSeconds"]["pytestExecutionSeconds"] == -20.0
    assert result["totalSpeedRatio"] == pytest.approx(65.0 / 87.0)
    assert baseline == baseline_before
    assert candidate == candidate_before


def test_performance_comparison_reports_bounded_cache_and_failure_metrics():
    from backend.agents.acceptance_performance import compare_performance_baselines

    result = compare_performance_baselines(_baseline(), _baseline())

    assert result["cacheHit"] is False
    assert result["cacheMiss"] is False
    assert result["failureRate"] == 0.0
    assert result["status"] == "compared"


def test_performance_comparison_covers_cache_hit_miss_and_identity_safety():
    from backend.agents.acceptance_performance import compare_performance_baselines

    cache_hit = compare_performance_baselines(
        _baseline(),
        _baseline(comparison={"status": "compared", "cacheHit": True, "cacheMiss": False}),
    )
    cache_miss = compare_performance_baselines(
        _baseline(),
        _baseline(comparison={"status": "compared", "cacheHit": False, "cacheMiss": True}),
    )

    assert cache_hit["cacheHit"] is True
    assert cache_hit["cacheMiss"] is False
    assert cache_miss["cacheHit"] is False
    assert cache_miss["cacheMiss"] is True

    with pytest.raises(ValueError, match="selection"):
        compare_performance_baselines(
            _baseline(),
            _baseline(
                selection_mode="fast",
                comparison={"status": "compared", "cacheHit": True, "cacheMiss": False},
            ),
        )


def test_non_comparable_comparison_can_be_embedded_as_bounded_evidence():
    from backend.agents.acceptance_performance import (
        compare_performance_baselines,
        validate_performance_baseline,
    )

    baseline = _baseline(
        stages={
            "collectionSeconds": 0.0,
            "fixturePreparationSeconds": 0.0,
            "pytestExecutionSeconds": 0.0,
            "aggregateSeconds": 0.0,
            "totalWallSeconds": 0.0,
        }
    )
    candidate = _baseline()
    comparison = compare_performance_baselines(baseline, candidate)
    embedded = _baseline(comparison=comparison)

    validate_performance_baseline(embedded)
    assert embedded["comparison"]["status"] == "not_compared"
    assert embedded["comparison"]["reason"] == "baseline_total_non_positive"


def test_performance_artifact_is_not_formal_release_gate_evidence():
    from backend.agents.release_gate_models import ReleaseGateValidationError, validate_release_gate_evidence

    payload = _baseline()
    with pytest.raises(ReleaseGateValidationError, match="evidence schema"):
        validate_release_gate_evidence(payload, COMMIT, SOURCE)


def test_performance_composer_uses_existing_manifest_and_gate_timings():
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    result = build_performance_from_artifacts(
        manifest={
            "schemaVersion": "pytest-test-manifest-v1",
            "status": "PASS",
            "commitSha": COMMIT,
            "sourceFingerprint": SOURCE,
            "metadata": {"telemetry": {"durationSeconds": 2.0}},
            "nodeids": ["tests/test_one.py::test_one"],
            "manifestFingerprint": "c" * 64,
        },
        full_pytest={
            "schemaVersion": "full-pytest-gate-v1",
            "status": "PASS",
            "commitSha": COMMIT,
            "sourceFingerprint": SOURCE,
            "result": {"passed": 1, "failed": 0, "skipped": 0},
            "metadata": {
                "telemetry": {"durationSeconds": 81.0},
                "performanceTiming": {"pytestExecutionSeconds": 80.0},
            },
        },
        fixture_timing={
            "schemaVersion": "acceptance-fixture-timing-v1",
            "commitSha": COMMIT,
            "sourceFingerprint": SOURCE,
            "durationSeconds": 4.0,
        },
        aggregate=None,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        runner_fingerprint=RUNNER,
        selection_mode="full",
    )
    assert result["stages"]["collectionSeconds"] == 2.0
    assert result["stages"]["fixturePreparationSeconds"] == 4.0
    assert result["stages"]["pytestExecutionSeconds"] == 80.0
    assert result["testCount"] == {"collected": 1, "passed": 1, "failed": 0, "skipped": 0}
    assert result["comparison"]["status"] == "not_compared"
    assert result["comparison"]["cacheHit"] is False
    assert result["comparison"]["cacheMiss"] is False
    assert result["comparison"]["failureRate"] == 0.0
    assert result["authority"] == "diagnostic"


def test_performance_composer_separates_aggregate_duration_from_total_wall_time():
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    result = build_performance_from_artifacts(
        manifest={
            "schemaVersion": "pytest-test-manifest-v1", "status": "PASS",
            "commitSha": COMMIT, "sourceFingerprint": SOURCE,
            "metadata": {"telemetry": {"durationSeconds": 2.0}},
            "nodeids": ["tests/test_one.py::test_one"],
        },
        full_pytest={
            "schemaVersion": "full-pytest-gate-v1", "status": "PASS",
            "commitSha": COMMIT, "sourceFingerprint": SOURCE,
            "result": {"passed": 1, "failed": 0, "skipped": 0},
            "metadata": {"performanceTiming": {"pytestExecutionSeconds": 5.0}},
        },
        fixture_timing={
            "schemaVersion": "acceptance-fixture-timing-v1",
            "commitSha": COMMIT, "sourceFingerprint": SOURCE,
            "durationSeconds": 1.0,
        },
        aggregate={
            "schemaVersion": "release-gate-result-v1", "status": "PASS",
            "commitSha": COMMIT, "sourceFingerprint": SOURCE,
            "freshness": {"telemetry": {
                "childDurationsSeconds": {"full_pytest": 5.0},
                "totalWallDurationSeconds": 12.0,
                "slowestGate": "full_pytest",
                "aggregationDurationSeconds": 0.25,
            }},
        },
        commit_sha=COMMIT, source_fingerprint=SOURCE,
        runner_fingerprint=RUNNER, selection_mode="full",
    )

    assert result["stages"]["aggregateSeconds"] == 0.25
    assert result["stages"]["totalWallSeconds"] == 12.0
    assert result["comparison"]["status"] == "not_compared"


def test_manifest_reader_allows_only_bounded_large_manifest(tmp_path):
    from scripts.acceptance_performance_benchmark import (
        MAX_JSON_BYTES,
        MAX_MANIFEST_JSON_BYTES,
        read_bounded_json,
    )

    payload = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": [
            f"tests/test_{index:05d}.py::test_{index:05d}::{'x' * 32}"
            for index in range(5_000)
        ],
    }
    path = tmp_path / "large-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert MAX_JSON_BYTES < path.stat().st_size <= MAX_MANIFEST_JSON_BYTES
    with pytest.raises(ValueError, match="size cap"):
        read_bounded_json(path)
    assert read_bounded_json(path, max_bytes=MAX_MANIFEST_JSON_BYTES) == payload


def test_performance_composer_rejects_fixture_timing_identity_mismatch():
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    with pytest.raises(ValueError, match="fixture timing.*source"):
        build_performance_from_artifacts(
            manifest={
                "schemaVersion": "pytest-test-manifest-v1", "status": "PASS",
                "commitSha": COMMIT, "sourceFingerprint": SOURCE,
                "metadata": {"telemetry": {"durationSeconds": 2.0}},
                "nodeids": ["tests/test_one.py::test_one"],
            },
            full_pytest={
                "schemaVersion": "full-pytest-gate-v1", "status": "PASS",
                "commitSha": COMMIT, "sourceFingerprint": SOURCE,
                "result": {"passed": 1, "failed": 0, "skipped": 0},
                "metadata": {"telemetry": {"durationSeconds": 3.0}},
            },
            fixture_timing={
                "schemaVersion": "acceptance-fixture-timing-v1",
                "commitSha": COMMIT,
                "sourceFingerprint": "d" * 64,
                "durationSeconds": 4.0,
            },
            aggregate=None,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            selection_mode="full",
        )


def test_performance_composer_rejects_gate_identity_mismatch():
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    with pytest.raises(ValueError, match="source"):
        build_performance_from_artifacts(
            manifest={
                "schemaVersion": "pytest-test-manifest-v1",
                "status": "PASS",
                "commitSha": COMMIT,
                "sourceFingerprint": "d" * 64,
                "metadata": {"telemetry": {"durationSeconds": 1.0}},
                "nodeids": [],
                "manifestFingerprint": "c" * 64,
            },
            full_pytest={
                "schemaVersion": "full-pytest-gate-v1",
                "status": "PASS",
                "commitSha": COMMIT,
                "sourceFingerprint": SOURCE,
                "result": {"passed": 0, "failed": 0, "skipped": 0},
                "metadata": {"telemetry": {"durationSeconds": 1.0}},
            },
            fixture_timing=None,
            aggregate=None,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            selection_mode="full",
        )


def test_performance_composer_requires_contract_for_v2():
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    with pytest.raises(ValueError, match="contract"):
        build_performance_from_artifacts(
            manifest={
                "schemaVersion": "pytest-test-manifest-v1", "status": "PASS",
                "commitSha": COMMIT, "sourceFingerprint": SOURCE,
                "manifestFingerprint": "d" * 64,
                "metadata": {"telemetry": {"durationSeconds": 1.0}},
                "nodeids": ["tests/test_one.py::test_one"],
            },
            full_pytest={
                "schemaVersion": "full-pytest-gate-v1", "status": "PASS",
                "commitSha": COMMIT, "sourceFingerprint": SOURCE,
                "result": {"passed": 1, "failed": 0, "skipped": 0},
                "metadata": {"performanceTiming": {"pytestExecutionSeconds": 1.0}},
            },
            fixture_timing=None,
            aggregate=None,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            selection_mode="full",
            output_schema="v2",
        )


def test_performance_composer_emits_v2_lineage_without_changing_v1_default():
    from backend.agents.acceptance_contract import build_acceptance_contract
    from backend.agents.evidence_models import canonical_fingerprint
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    nodeids = ["tests/test_one.py::test_one", "tests/test_two.py::test_two"]
    contract = build_acceptance_contract(
        contract_id="nbs-acceptance",
        contract_version="formal-scope-v1",
        scope_fingerprint="a" * 64,
        semantic_rules_fingerprint="b" * 64,
        dataset_snapshot_fingerprint="c" * 64,
        supersedes_contract_fingerprint=None,
        status="active",
    )
    manifest_unsigned = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "manifestFingerprint": "d" * 64,
        "metadata": {"telemetry": {"durationSeconds": 1.0}},
        "nodeids": nodeids,
    }
    result = build_performance_from_artifacts(
        manifest=manifest_unsigned,
        full_pytest={
            "schemaVersion": "full-pytest-gate-v1", "status": "PASS",
            "commitSha": COMMIT, "sourceFingerprint": SOURCE,
            "result": {"passed": 2, "failed": 0, "skipped": 0},
            "metadata": {"performanceTiming": {"pytestExecutionSeconds": 2.0}},
        },
        fixture_timing={
            "schemaVersion": "acceptance-fixture-timing-v1",
            "commitSha": COMMIT, "sourceFingerprint": SOURCE,
            "durationSeconds": 1.0,
        },
        aggregate=None,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        runner_fingerprint=RUNNER,
        selection_mode="full",
        contract=contract,
        output_schema="v2",
        baseline_family_id="acceptance-full-2026-09",
        baseline_role="serial_control",
        lifecycle="draft",
        environment_fingerprint="e" * 64,
    )

    assert result["schemaVersion"] == "acceptance-performance-baseline-v2"
    assert result["contractFingerprint"] == contract["evidenceFingerprint"]
    assert result["datasetSnapshotFingerprint"] == "c" * 64
    assert result["manifestFingerprint"] == "d" * 64
    assert result["testPopulationFingerprint"] == canonical_fingerprint({"nodeids": sorted(nodeids)})
    assert result["environmentFingerprint"] == "e" * 64


def test_performance_composer_rejects_inconsistent_test_counts():
    from scripts.acceptance_performance_benchmark import build_performance_from_artifacts

    with pytest.raises(ValueError, match="collected"):
        build_performance_from_artifacts(
            manifest={
                "schemaVersion": "pytest-test-manifest-v1",
                "status": "PASS",
                "commitSha": COMMIT,
                "sourceFingerprint": SOURCE,
                "metadata": {"telemetry": {"durationSeconds": 2.0}},
                "nodeids": ["tests/test_one.py::test_one"],
            },
            full_pytest={
                "schemaVersion": "full-pytest-gate-v1",
                "status": "PASS",
                "commitSha": COMMIT,
                "sourceFingerprint": SOURCE,
                "result": {"passed": 2, "failed": 0, "skipped": 0},
                "metadata": {"telemetry": {"durationSeconds": 3.0}},
            },
            fixture_timing=None,
            aggregate=None,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            selection_mode="full",
        )


def test_performance_cli_writes_bounded_failure_artifact(tmp_path, monkeypatch):
    from scripts import acceptance_performance_benchmark as benchmark

    monkeypatch.setattr(benchmark, "PROJECT_ROOT", tmp_path)
    output = tmp_path / ".nbs_agent_runtime" / "acceptance-optimization" / "failure.json"
    exit_code = benchmark.main([
        "--manifest", str(tmp_path / "missing-manifest.json"),
        "--full-pytest", str(tmp_path / "missing-full-pytest.json"),
        "--commit-sha", COMMIT,
        "--source-fingerprint", SOURCE,
        "--runner-fingerprint", RUNNER,
        "--selection-mode", "full",
        "--output", str(output),
    ])

    assert exit_code == 2
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["schemaVersion"] == "acceptance-performance-failure-v1"
    assert failure["authority"] == "diagnostic"
    assert failure["fullGateRequired"] is True
    assert failure["status"] == "BLOCKED"
    assert len(failure["error"]) <= 256


def test_performance_cli_accepts_explicit_v2_contract_and_lineage(tmp_path, monkeypatch):
    from backend.agents.acceptance_contract import build_acceptance_contract
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2
    from scripts import acceptance_performance_benchmark as benchmark

    monkeypatch.setattr(benchmark, "PROJECT_ROOT", tmp_path)
    contract = build_acceptance_contract(
        contract_id="nbs-acceptance",
        contract_version="formal-scope-v1",
        scope_fingerprint="a" * 64,
        semantic_rules_fingerprint="b" * 64,
        dataset_snapshot_fingerprint="c" * 64,
        supersedes_contract_fingerprint=None,
        status="active",
    )
    contract_path = tmp_path / "contract.json"
    manifest_path = tmp_path / "manifest.json"
    full_pytest_path = tmp_path / "full-pytest.json"
    fixture_timing_path = tmp_path / "fixture-timing.json"
    output = tmp_path / ".nbs_agent_runtime" / "performance-v2.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    manifest_path.write_text(json.dumps({
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "manifestFingerprint": "d" * 64,
        "metadata": {"telemetry": {"durationSeconds": 1.0}},
        "nodeids": ["tests/test_one.py::test_one"],
    }), encoding="utf-8")
    full_pytest_path.write_text(json.dumps({
        "schemaVersion": "full-pytest-gate-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "result": {"passed": 1, "failed": 0, "skipped": 0},
        "metadata": {"performanceTiming": {"pytestExecutionSeconds": 2.0}},
    }), encoding="utf-8")
    fixture_timing_path.write_text(json.dumps({
        "schemaVersion": "acceptance-fixture-timing-v1",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "durationSeconds": 0.5,
    }), encoding="utf-8")

    assert benchmark.main([
        "--manifest", str(manifest_path),
        "--full-pytest", str(full_pytest_path),
        "--fixture-timing", str(fixture_timing_path),
        "--commit-sha", COMMIT,
        "--source-fingerprint", SOURCE,
        "--runner-fingerprint", RUNNER,
        "--selection-mode", "full",
        "--contract", str(contract_path),
        "--output-schema", "v2",
        "--environment-fingerprint", "e" * 64,
        "--baseline-family-id", "acceptance-full-2026-09",
        "--baseline-role", "serial_control",
        "--lifecycle", "draft",
        "--output", str(output),
    ]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    validate_performance_baseline_v2(payload)
    assert payload["contractFingerprint"] == contract["evidenceFingerprint"]


def test_bounded_json_reader_rejects_symlink(tmp_path):
    from scripts.acceptance_performance_benchmark import read_bounded_json

    target = tmp_path / "target.json"
    target.write_text(json.dumps({"ok": True}), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_bounded_json(link)


def test_performance_writer_rejects_runtime_output_symlink(tmp_path, monkeypatch):
    from scripts import acceptance_performance_benchmark as benchmark

    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("original", encoding="utf-8")
    link = runtime / "performance.json"
    link.symlink_to(target)
    monkeypatch.setattr(benchmark, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        benchmark._write_json(link, {"safe": True})
    assert target.read_text(encoding="utf-8") == "original"
