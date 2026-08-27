import json
import hashlib
import shutil
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backend.services.gmv_export_cache_service import (
    LEGACY_CACHE_SCHEMA_VERSION,
    _gmv_export_cache_key_for_schema,
    _target_dir_for_cache_key,
    build_gmv_export_cache,
    gmv_export_cache_key,
    load_gmv_export_cache,
    publish_gmv_export_cache_manifest,
)


def _inputs():
    return {
        "version_id": "version-1",
        "revenue_generation_token": "revenue-1",
        "rule_version": "rules-1",
        "total_workbooks": {
            "ex.xlsx": b"total-ex", "ex_no_writeoff.xlsx": b"total-no-writeoff",
            "ex_no_writeoff_refund_transfer.xlsx": b"total-official", "audit.xlsx": b"total-audit",
        },
        "paid_workbooks": {
            "ex.xlsx": b"paid-ex", "ex_no_writeoff.xlsx": b"paid-no-writeoff",
            "ex_no_writeoff_refund_transfer.xlsx": b"paid-official", "audit.xlsx": b"paid-audit",
        },
        "total_detail": pd.DataFrame([{"來源單據號": "S-1", "退款金額": 20}]),
        "paid_detail": pd.DataFrame([{"來源單據號": "S-1", "退款金額": 20}]),
        "summaries": [{"dimension": "TOTAL_REFUND", "amount": 20}],
    }


def test_cache_key_is_sensitive_to_version_token_rule_and_schema():
    base = gmv_export_cache_key(version_id="v1", revenue_generation_token="r1", rule_version="rules-1")
    assert base != gmv_export_cache_key(version_id="v2", revenue_generation_token="r1", rule_version="rules-1")
    assert base != gmv_export_cache_key(version_id="v1", revenue_generation_token="r2", rule_version="rules-1")
    assert base != gmv_export_cache_key(version_id="v1", revenue_generation_token="r1", rule_version="rules-2")


def test_build_and_load_cache_validates_all_artifacts(tmp_path: Path):
    manifest = build_gmv_export_cache(
        cache_dir=tmp_path, builder_mode="fast", equivalence_status="PASS",
        content_fingerprint="a" * 64, reference_id="gmv-trusted-reference-v1:" + "a" * 64,
        validation_mode="trusted_warm", shadow_status="PASS", reference_manifest_sha256="b" * 64,
        **_inputs(),
    )
    assert manifest.status == "ready"
    assert manifest.build_duration_ms >= 0
    assert manifest.schema_version == "gmv-formal-export-cache-v2"
    assert manifest.builder_mode == "fast"
    assert manifest.equivalence_status == "PASS"
    assert manifest.validation_mode == "trusted_warm"
    assert manifest.shadow_status == "PASS"
    assert all(int(item["bytes"]) > 0 for item in manifest.artifacts.values())
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path
    )
    assert loaded == manifest
    assert json.loads(next((tmp_path / "version-1").rglob("manifest.json")).read_text())["status"] == "ready"
    assert (tmp_path / "version-1" / "active.json").is_file()


def test_seed_cache_can_be_written_without_replacing_active_pointer(tmp_path: Path):
    active = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    seed = build_gmv_export_cache(cache_dir=tmp_path, publish_active=False, builder_mode="legacy_seed", **_inputs())
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path,
    )
    assert seed.status == "ready"
    assert loaded == active


def test_incomplete_artifact_set_is_failed_and_preserves_active_pointer(tmp_path: Path):
    active = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    values = _inputs()
    values["paid_workbooks"] = {
        name: content for name, content in values["paid_workbooks"].items()
        if name != "audit.xlsx"
    }

    failed = build_gmv_export_cache(cache_dir=tmp_path, **values)
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path,
    )

    assert failed.status == "failed"
    assert "artifact contract" in (failed.error or "")
    assert loaded == active


def test_publish_rejects_manifest_with_missing_generation_files(tmp_path: Path):
    active = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    pointer = tmp_path / "version-1" / "active.json"
    before = pointer.read_bytes()
    forged = replace(active, generation_path="generations/missing")

    with pytest.raises(ValueError, match="artifact file"):
        publish_gmv_export_cache_manifest(cache_dir=tmp_path, manifest=forged)

    assert pointer.read_bytes() == before


def test_cache_miss_for_wrong_identity_and_partial_artifact(tmp_path: Path):
    build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="other", rule_version="rules-1", cache_dir=tmp_path) is None
    manifest = load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path)
    assert manifest is not None
    detail = next(item for item in manifest.artifacts.values() if item["kind"] == "csv")
    next((tmp_path / "version-1").rglob(str(detail["path"]))).unlink()
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path) is None


def test_active_pointer_mismatch_is_not_current(tmp_path: Path):
    build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    pointer = tmp_path / "version-1" / "active.json"
    payload = json.loads(pointer.read_text())
    payload["cacheKey"] = "gmv-formal-export-v1:stale"
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path) is None


def test_active_pointer_manifest_checksum_is_verified(tmp_path: Path):
    build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    manifest_path = next((tmp_path / "version-1").rglob("manifest.json"))
    manifest_path.write_text(manifest_path.read_text() + "\n", encoding="utf-8")
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path) is None


def test_failed_serialization_is_not_ready(tmp_path: Path):
    values = _inputs()
    values["total_workbooks"] = {"broken.xlsx": object()}
    manifest = build_gmv_export_cache(cache_dir=tmp_path, **values)
    assert manifest.status == "failed"
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path) is None


def test_v1_manifest_remains_readable(tmp_path: Path):
    manifest = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    path = next((tmp_path / "version-1").rglob("manifest.json"))
    payload = json.loads(path.read_text())
    payload["schemaVersion"] = "gmv-formal-export-cache-v1"
    payload.pop("builderMode", None)
    payload.pop("equivalenceStatus", None)
    payload.pop("artifactCount", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    pointer_path = tmp_path / "version-1" / "active.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["manifestSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    loaded = load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path)
    assert loaded is not None
    assert loaded.schema_version == "gmv-formal-export-cache-v1"


def test_actual_v1_cache_key_and_path_remain_readable(tmp_path: Path):
    manifest = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    source = tmp_path / "version-1" / manifest.generation_path
    legacy_key = _gmv_export_cache_key_for_schema(
        version_id="version-1", revenue_generation_token="revenue-1",
        rule_version="rules-1", schema_version=LEGACY_CACHE_SCHEMA_VERSION,
    )
    legacy_target = _target_dir_for_cache_key(tmp_path, "version-1", legacy_key)
    shutil.copytree(source, legacy_target)
    legacy_manifest = json.loads((legacy_target / "manifest.json").read_text())
    legacy_manifest["cacheKey"] = legacy_key
    legacy_manifest["schemaVersion"] = LEGACY_CACHE_SCHEMA_VERSION
    for key in ("builderMode", "equivalenceStatus", "artifactCount", "generationPath"):
        legacy_manifest.pop(key, None)
    (legacy_target / "manifest.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")
    (tmp_path / "version-1" / "active.json").unlink()
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1",
        rule_version="rules-1", cache_dir=tmp_path,
    )
    assert loaded is not None
    assert loaded.cache_key == legacy_key
    assert loaded.schema_version == LEGACY_CACHE_SCHEMA_VERSION
