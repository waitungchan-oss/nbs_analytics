import time

from backend.services.gmv_export_cache_service import load_gmv_export_cache, read_gmv_export_artifact
from backend.services.gmv_refund_repository import GmvRefundRepository
from backend.services.gmv_refund_service import build_gmv_formal_artifacts

from tests.test_gmv_one_click_merge_integration import _active


def test_ready_cache_load_and_repeat_download_are_fast_and_read_only(tmp_path):
    db_path, frames, _, receipt = _active(tmp_path)
    cache_dir = tmp_path / "cache"
    artifacts = build_gmv_formal_artifacts(
        repository=GmvRefundRepository(db_path), version_id=receipt.version_id,
        revenue_frames=frames, rule_version="rules-1", cache_dir=cache_dir,
    )
    manifest = artifacts.cache_manifest
    mtimes_before = {
        key: (cache_dir / receipt.version_id).glob("*/" + str(record["path"])).__iter__().__next__().stat().st_mtime_ns
        for key, record in manifest.artifacts.items()
    }
    started = time.perf_counter()
    loaded = load_gmv_export_cache(
        version_id=receipt.version_id, revenue_generation_token=manifest.revenue_generation_token,
        rule_version="rules-1", cache_dir=cache_dir,
    )
    for key in manifest.artifacts:
        read_gmv_export_artifact(loaded, cache_dir, key)
    elapsed = time.perf_counter() - started
    assert loaded is not None
    assert elapsed < 2.0
    mtimes_after = {
        key: (cache_dir / receipt.version_id).glob("*/" + str(record["path"])).__iter__().__next__().stat().st_mtime_ns
        for key, record in manifest.artifacts.items()
    }
    assert mtimes_after == mtimes_before
