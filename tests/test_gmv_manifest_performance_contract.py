import json

from backend.services.gmv_export_cache_service import build_gmv_export_cache, load_gmv_export_cache
from tests.test_gmv_export_cache_service import _inputs


def test_manifest_round_trips_performance_fallback_and_refund_state(tmp_path):
    manifest = build_gmv_export_cache(
        cache_dir=tmp_path,
        performance={"totalMs": 123, "stageTimings": [{"stage": "serialize", "ms": 4}]},
        fallback={"used": False, "reason": None},
        refund_state_sha256="a" * 64,
        **_inputs(),
    )
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1",
        rule_version="rules-1", cache_dir=tmp_path,
    )
    assert loaded == manifest
    assert loaded.performance["totalMs"] == 123
    assert loaded.fallback["used"] is False
    assert loaded.refund_state_sha256 == "a" * 64


def test_failed_manifest_never_replaces_ready_active_pointer(tmp_path):
    active = build_gmv_export_cache(cache_dir=tmp_path, **_inputs())
    values = _inputs()
    values["total_workbooks"] = {"invalid.txt": b"bad"}
    failed = build_gmv_export_cache(
        cache_dir=tmp_path, performance={"totalMs": 1}, fallback={"used": True}, **values,
    )
    loaded = load_gmv_export_cache(
        version_id="version-1", revenue_generation_token="revenue-1",
        rule_version="rules-1", cache_dir=tmp_path,
    )
    assert failed.status == "failed"
    assert loaded == active
    pointer = json.loads((tmp_path / "version-1" / "active.json").read_text())
    assert pointer["generationPath"] == active.generation_path
