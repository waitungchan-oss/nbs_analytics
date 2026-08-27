import hashlib
import sqlite3

from tests.test_gmv_one_click_merge_integration import _active


def test_benchmark_suite_reports_median_p95_and_keeps_db_unchanged(tmp_path):
    from backend.services.gmv_rebuild_benchmark_service import run_gmv_rebuild_benchmark_suite

    db_path, _, _, receipt = _active(tmp_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with sqlite3.connect(db_path) as conn:
        schema_before = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]

    report = run_gmv_rebuild_benchmark_suite(
        db_path=db_path,
        version_id=receipt.version_id,
        cache_dir=tmp_path / "bench",
        modes=("legacy-cold", "fast-cold"),
        samples=3,
        workers=1,
    )

    assert report["sampleCount"] == 3
    assert set(report["modes"]) == {"legacy-cold", "fast-cold"}
    for mode in report["modes"].values():
        assert len(mode["samples"]) == 3
        assert mode["p95Ms"] >= mode["medianMs"]
        assert mode["equivalenceRate"] == 1.0
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == schema_before
    assert not (tmp_path / ".nbs_runtime_cache").exists()


def test_benchmark_sample_exposes_stage_and_artifact_evidence(tmp_path):
    from backend.services.gmv_rebuild_benchmark_service import run_gmv_rebuild_benchmark

    db_path, _, _, receipt = _active(tmp_path)
    sample = run_gmv_rebuild_benchmark(
        db_path=db_path,
        version_id=receipt.version_id,
        cache_dir=tmp_path / "bench",
        mode="legacy-cold",
        workers=2,
    )

    assert sample.mode == "legacy-cold"
    assert sample.workers == 2
    assert sample.total_ms >= 0
    assert sample.peak_rss_bytes is None or sample.peak_rss_bytes > 0
    assert sample.artifact_fingerprints
    assert sample.equivalence_status == "NOT_RUN"
    assert "totalMs" in sample.stage_ms
