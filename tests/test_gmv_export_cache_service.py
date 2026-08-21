import json
from pathlib import Path

import pandas as pd

from backend.services.gmv_export_cache_service import (
    build_gmv_export_cache,
    gmv_export_cache_key,
    load_gmv_export_cache,
)


def _inputs():
    return {
        "version_id": "version-1",
        "revenue_generation_token": "revenue-1",
        "rule_version": "rules-1",
        "total_workbooks": {"完整報表.xlsx": b"total-xlsx"},
        "paid_workbooks": {"完整報表.xlsx": b"paid-xlsx"},
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
    manifest = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    assert manifest.status == "ready"
    assert manifest.build_duration_ms >= 0
    assert all(int(item["bytes"]) > 0 for item in manifest.artifacts.values())
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path
    )
    assert loaded == manifest
    assert json.loads((tmp_path / "version-1").glob("*/manifest.json").__iter__().__next__().read_text())["status"] == "ready"


def test_cache_miss_for_wrong_identity_and_partial_artifact(tmp_path: Path):
    build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="other", rule_version="rules-1", cache_dir=tmp_path) is None
    manifest = load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path)
    assert manifest is not None
    detail = next(item for item in manifest.artifacts.values() if item["kind"] == "csv")
    next((tmp_path / "version-1").glob(f"*/{detail['path']}" )).unlink()
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path) is None


def test_failed_serialization_is_not_ready(tmp_path: Path):
    values = _inputs()
    values["total_workbooks"] = {"broken.xlsx": object()}
    manifest = build_gmv_export_cache(cache_dir=tmp_path, **values)
    assert manifest.status == "failed"
    assert load_gmv_export_cache(version_id="version-1", revenue_generation_token="revenue-1", rule_version="rules-1", cache_dir=tmp_path) is None
