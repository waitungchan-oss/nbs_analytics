import sqlite3

import pandas as pd

from backend.services.gmv_export_cache_service import build_gmv_export_cache
from scripts.ui_acceptance_fixture_preflight import (
    build_ui_fixture_blocker,
    inspect_ui_fixture_cache,
)


def _active_db(path, *, version="v1", token="db-token", rule="rules-1"):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE gmv_scope_versions (version_id TEXT, revenue_generation_token TEXT, rule_version TEXT, status TEXT)"
        )
        conn.execute(
            "INSERT INTO gmv_scope_versions VALUES (?, ?, ?, 'ACTIVE')",
            (version, token, rule),
        )


def _cache(cache_dir, *, version="v1", token="cache-token", rule="rules-1"):
    workbooks = {
        "ex.xlsx": b"ex",
        "ex_no_writeoff.xlsx": b"no-writeoff",
        "ex_no_writeoff_refund_transfer.xlsx": b"official",
        "audit.xlsx": b"audit",
    }
    return build_gmv_export_cache(
        cache_dir=cache_dir,
        version_id=version,
        revenue_generation_token=token,
        rule_version=rule,
        total_workbooks=workbooks,
        paid_workbooks=workbooks,
        total_detail=pd.DataFrame([{"receipt": "R-1"}]),
        paid_detail=pd.DataFrame([{"receipt": "R-1"}]),
        summaries=[],
    )


def test_missing_active_version_is_bounded_and_does_not_create_cache(tmp_path):
    cache_dir = tmp_path / "missing-cache"

    result = inspect_ui_fixture_cache(
        db_path=tmp_path / "missing.db",
        cache_dir=cache_dir,
        project_root=tmp_path,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "missing_active_version"
    assert cache_dir.exists() is False


def test_token_mismatch_is_rejected_before_browser(tmp_path):
    db_path = tmp_path / "shard.db"
    cache_dir = tmp_path / "cache"
    _active_db(db_path)
    _cache(cache_dir)

    result = inspect_ui_fixture_cache(
        db_path=db_path, cache_dir=cache_dir, project_root=tmp_path
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "revenue_token_mismatch"
    assert result["activeVersionId"] == "v1"


def test_cache_artifact_integrity_is_checked(tmp_path):
    db_path = tmp_path / "shard.db"
    cache_dir = tmp_path / "cache"
    _active_db(db_path, token="shared-token")
    manifest = _cache(cache_dir, token="shared-token")
    artifact = next(item for item in manifest.artifacts.values() if item["kind"] == "csv")
    artifact_path = next((cache_dir / "v1").rglob(str(artifact["path"])))
    artifact_path.write_bytes(b"tampered")

    result = inspect_ui_fixture_cache(
        db_path=db_path, cache_dir=cache_dir, project_root=tmp_path
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "artifact_integrity_failure"


def test_symlinked_cache_version_directory_is_rejected(tmp_path):
    db_path = tmp_path / "shard.db"
    cache_dir = tmp_path / "cache"
    _active_db(db_path, token="shared-token")
    _cache(cache_dir, token="shared-token")
    version_root = cache_dir / "v1"
    real_version_root = cache_dir / "real-v1"
    version_root.rename(real_version_root)
    version_root.symlink_to(real_version_root, target_is_directory=True)

    result = inspect_ui_fixture_cache(
        db_path=db_path, cache_dir=cache_dir, project_root=tmp_path
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "cache_version_path_invalid"


def test_ui_fixture_blocker_has_stable_schema_and_reason():
    blocker = build_ui_fixture_blocker("revenue_token_mismatch")

    assert blocker["schemaVersion"] == "ui-fixture-preflight-v1"
    assert blocker["status"] == "BLOCKED"
    assert blocker["failureCode"] == "ui_fixture_cache_mismatch"
    assert blocker["reason"] == "revenue_token_mismatch"
