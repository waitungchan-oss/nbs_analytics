from __future__ import annotations

from pathlib import Path

from backend.agents.acceptance_performance import (
    build_performance_baseline,
    validate_performance_baseline,
)
from backend.agents.acceptance_performance_v2 import (
    build_performance_baseline_v2,
    compare_performance_baselines_v2,
)


def _v2(**overrides):
    values = {
        "baseline_family_id": "acceptance-full-2026-09",
        "baseline_role": "serial_control",
        "lifecycle": "qualified",
        "contract_fingerprint": "a" * 64,
        "commit_sha": "b" * 40,
        "source_fingerprint": "c" * 64,
        "manifest_fingerprint": "d" * 64,
        "test_population_fingerprint": "e" * 64,
        "runner_fingerprint": "f" * 64,
        "environment_fingerprint": "0" * 64,
        "dataset_snapshot_fingerprint": "1" * 64,
        "selection_mode": "full",
        "stages": {
            "collectionSeconds": 2.0,
            "fixturePreparationSeconds": 4.0,
            "pytestExecutionSeconds": 80.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": 87.0,
        },
        "test_count": {"collected": 100, "passed": 100, "failed": 0, "skipped": 0},
    }
    values.update(overrides)
    return build_performance_baseline_v2(**values)


def test_legacy_v1_artifact_remains_readable_during_migration():
    legacy = build_performance_baseline(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        runner_fingerprint="c" * 64,
        stages={
            "collectionSeconds": 1.0,
            "fixturePreparationSeconds": 2.0,
            "pytestExecutionSeconds": 3.0,
            "aggregateSeconds": 0.5,
            "totalWallSeconds": 6.5,
        },
        test_count={"collected": 1, "passed": 1, "failed": 0, "skipped": 0},
        selection_mode="full",
    )

    validate_performance_baseline(legacy)
    assert set(legacy) == {
        "schemaVersion",
        "authority",
        "fullGateRequired",
        "commitSha",
        "sourceFingerprint",
        "runnerFingerprint",
        "selectionMode",
        "stages",
        "testCount",
        "comparison",
        "evidenceFingerprint",
    }


def test_same_contract_allows_cross_commit_comparison():
    result = compare_performance_baselines_v2(
        _v2(),
        _v2(
            baseline_role="parallel_candidate",
            commit_sha="9" * 40,
            source_fingerprint="8" * 64,
        ),
    )

    assert result["status"] == "compared"
    assert result["totalSpeedRatio"] == 1.0


def test_scope_change_creates_a_new_lineage_and_suppresses_ratio():
    result = compare_performance_baselines_v2(
        _v2(),
        _v2(
            baseline_role="parallel_candidate",
            contract_fingerprint="2" * 64,
            baseline_family_id="acceptance-full-2026-10",
            commit_sha="9" * 40,
            source_fingerprint="8" * 64,
        ),
    )

    assert result["status"] == "not_compared"
    assert result["reason"] in {"contract_mismatch", "baseline_family_mismatch"}
    assert "totalSpeedRatio" not in result


def test_migration_rehearsal_has_no_production_write_dependencies():
    source = Path("scripts/acceptance_baseline.py").read_text(encoding="utf-8").lower()

    assert "import sqlite3" not in source
    assert "subprocess" not in source
    assert "nbs_analytics_db_file" not in source
