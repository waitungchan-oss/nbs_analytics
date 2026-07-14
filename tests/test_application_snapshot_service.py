import ast
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backend.services.application_snapshot_service import (
    ApplicationSnapshotService,
    SnapshotDependencies,
    SnapshotGenerationConflict,
    SnapshotPaths,
)
from backend.services.business_rules_service import BusinessRulesSnapshot


def _rules() -> BusinessRulesSnapshot:
    return BusinessRulesSnapshot(
        branch_mapping_items=(("01", "Branch A"),),
        target_branches=("Branch A",),
        cruise_departments=("Cruise",),
        sales_reps=("Amy",),
        fingerprint="rules-fingerprint",
    )


def _paths(tmp_path: Path) -> SnapshotPaths:
    return SnapshotPaths(
        db_path=tmp_path / "live.db",
        cache_dir=tmp_path / "cache",
        runtime_dir=tmp_path / "runtime",
        rules_config_path=tmp_path / "rules.json",
        target_config_path=tmp_path / "targets.json",
    )


def _dependencies(tokens, seen):
    generations = iter(tokens)

    def load_rules(path):
        seen.setdefault("rules", []).append(path)
        return _rules()

    def load_generation(path, *, db_path):
        seen.setdefault("generation", []).append((path, db_path))
        return {"cacheToken": next(generations)}

    def build_facts(**kwargs):
        seen.setdefault("facts", []).append(kwargs)
        return {
            "status": "ready",
            "generationToken": kwargs["generation_token"],
            "factsCacheStatus": "hit",
            "readModelCacheStatus": "hit",
            "monthlyTotals": [],
        }

    def build_quality(**kwargs):
        seen.setdefault("quality", []).append(kwargs)
        return {
            "status": "ready",
            "overallScore": 100,
            "cacheStatus": "hit",
        }

    def build_forecast(*, cache_dir):
        seen.setdefault("forecast", []).append(cache_dir)
        return {
            "status": "ready",
            "cache": {"path": str(cache_dir / "ai_test.pkl"), "version": "v1"},
        }

    def build_health(*, db_path, cache_path, runtime_dir):
        seen.setdefault("health", []).append((db_path, cache_path, runtime_dir))
        return {"status": "ok", "latestAcceptance": {}}

    def load_targets(path):
        seen.setdefault("targets", []).append(path)
        return {"status": "not_configured", "targets": []}

    return SnapshotDependencies(
        rules_loader=load_rules,
        generation_loader=load_generation,
        facts_builder=build_facts,
        data_quality_builder=build_quality,
        forecast_builder=build_forecast,
        health_builder=build_health,
        target_loader=load_targets,
    )


def test_snapshot_uses_one_generation_and_explicit_paths(tmp_path):
    seen = {}
    paths = _paths(tmp_path)
    service = ApplicationSnapshotService(
        paths,
        dependencies=_dependencies(["7:abc", "7:abc"], seen),
    )

    snapshot = service.build()

    assert snapshot.generation_token == "7:abc"
    assert seen["rules"] == [paths.rules_config_path]
    assert seen["generation"] == [
        (paths.generation_path, paths.db_path),
        (paths.generation_path, paths.db_path),
    ]
    assert seen["facts"][0] == {
        "db_path": paths.db_path,
        "generation_token": "7:abc",
        "cache_dir": paths.cache_dir,
        "branch_mapping": {"01": "Branch A"},
        "target_branches_s3": ["Branch A"],
        "cruise_depts": ["Cruise"],
        "sales_rep_list": ["Amy"],
    }
    assert seen["quality"][0] == {
        "db_path": paths.db_path,
        "generation_token": "7:abc",
        "cache_dir": paths.cache_dir,
    }
    assert seen["forecast"] == [paths.cache_dir]
    assert seen["health"] == [(paths.db_path, paths.cache_dir, paths.runtime_dir)]
    assert seen["targets"] == [paths.target_config_path]
    assert snapshot.provenance["coreGenerationConsistent"] is True
    assert snapshot.provenance["snapshotAttemptCount"] == 1
    assert snapshot.provenance["rulesFingerprint"] == "rules-fingerprint"
    assert snapshot.provenance["factsCacheStatus"] == "hit"
    assert snapshot.provenance["readModelCacheStatus"] == "hit"
    assert snapshot.provenance["dataQualityCacheStatus"] == "hit"
    assert snapshot.provenance["forecastStatus"] == "ready"
    assert snapshot.provenance["systemHealthStatus"] == "ok"
    assert "forecastGenerationMatched" not in snapshot.provenance


def test_snapshot_rebuilds_every_dependency_when_generation_changes_once(tmp_path):
    seen = {}
    service = ApplicationSnapshotService(
        _paths(tmp_path),
        dependencies=_dependencies(
            ["1:old", "2:new", "2:new", "2:new"],
            seen,
        ),
    )

    snapshot = service.build()

    assert snapshot.generation_token == "2:new"
    assert snapshot.provenance["snapshotAttemptCount"] == 2
    assert [call["generation_token"] for call in seen["facts"]] == ["1:old", "2:new"]
    assert [call["generation_token"] for call in seen["quality"]] == ["1:old", "2:new"]
    assert len(seen["rules"]) == 2
    assert len(seen["forecast"]) == 2
    assert len(seen["health"]) == 2
    assert len(seen["targets"]) == 2


def test_snapshot_raises_typed_conflict_after_second_change(tmp_path):
    seen = {}
    service = ApplicationSnapshotService(
        _paths(tmp_path),
        dependencies=_dependencies(
            ["1:first", "2:second", "2:second", "3:third"],
            seen,
        ),
    )

    with pytest.raises(SnapshotGenerationConflict) as raised:
        service.build()

    assert raised.value.attempts == 2
    assert raised.value.observed_tokens == (
        ("1:first", "2:second"),
        ("2:second", "3:third"),
    )


def test_snapshot_propagates_builder_exceptions_without_stale_fallback(tmp_path):
    seen = {}
    dependencies = _dependencies(["1:stable", "1:stable"], seen)
    failure = RuntimeError("facts failed")

    def fail_facts(**kwargs):
        raise failure

    service = ApplicationSnapshotService(
        _paths(tmp_path),
        dependencies=replace(dependencies, facts_builder=fail_facts),
    )

    with pytest.raises(RuntimeError) as raised:
        service.build()

    assert raised.value is failure
    assert "quality" not in seen
    assert "forecast" not in seen


def test_snapshot_module_keeps_framework_and_data_details_outside_boundary():
    module_path = Path("backend/services/application_snapshot_service.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert imported_roots.isdisjoint({"fastapi", "streamlit", "pandas", "pipeline", "database"})


def test_default_dependencies_build_snapshot_from_isolated_persistent_sources(tmp_path):
    paths = _paths(tmp_path)
    tour = pd.DataFrame(
        [
            {
                "來源單據號": "A001",
                "收款時間": "2026-06-01",
                "統一日期": "2026-06-01",
                "收款原幣金額": 1000,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "銷售點": "Branch A",
                "銷售員": "Amy",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            }
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "B001",
                "收款時間": "2026-06-02",
                "統一日期": "2026-06-02",
                "收款原幣金額": 300,
                "收款類型": "正常收款",
                "收款方式": "信用卡",
                "銷售點": "Branch A",
                "銷售員": "Amy",
                "目的地大類": "票務",
                "團負責人部門": "",
                "行程天數": 0,
                "數量": 1,
            }
        ]
    )
    with sqlite3.connect(paths.db_path) as connection:
        tour.to_sql("tour_data", connection, if_exists="replace", index=False)
        others.to_sql("others_data", connection, if_exists="replace", index=False)
    paths.rules_config_path.write_text(
        json.dumps(
            {
                "BRANCH_MAPPING": {"A": "Branch A"},
                "TARGET_BRANCHES_S3": ["Branch A"],
                "CRUISE_DEPTS": [],
                "SALES_REP_LIST": ["Amy"],
            }
        ),
        encoding="utf-8",
    )

    snapshot = ApplicationSnapshotService(paths).build()

    assert snapshot.generation_token.startswith("0:")
    assert snapshot.facts["status"] == "ready"
    assert snapshot.quality["status"] == "ready"
    assert snapshot.forecast["status"] == "not_ready"
    assert snapshot.targets["status"] == "not_configured"
    assert snapshot.provenance["coreGenerationConsistent"] is True
    assert snapshot.provenance["dbPath"] == str(paths.db_path)
