import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from scripts.benchmark_gmv_refund_cache import run_gmv_cache_benchmark
import scripts.benchmark_gmv_refund_cache as benchmark_module
from tests.test_gmv_one_click_merge_integration import _active


EXPECTED_ARTIFACT_KEYS = {
    "total.detail",
    "total.workbook.ex.xlsx",
    "total.workbook.ex_no_writeoff.xlsx",
    "total.workbook.ex_no_writeoff_refund_transfer.xlsx",
    "total.workbook.audit.xlsx",
    "paid.detail",
    "paid.workbook.ex.xlsx",
    "paid.workbook.ex_no_writeoff.xlsx",
    "paid.workbook.ex_no_writeoff_refund_transfer.xlsx",
    "paid.workbook.audit.xlsx",
    "summaries",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_benchmark_writes_json_only_to_requested_cache(tmp_path):
    db_path, _, _, receipt = _active(tmp_path)
    db_hash_before = _sha256(db_path)
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]

    result = run_gmv_cache_benchmark(
        db_path=db_path,
        version_id=receipt.version_id,
        cache_dir=tmp_path / "benchmark-cache",
        mode="legacy",
        workers=2,
    )

    assert result["mode"] == "legacy"
    assert result["totalMs"] >= 0
    assert result["artifactBytes"] > 0
    assert result["equivalenceStatus"] == "NOT_RUN"
    assert result["builderMode"] == "legacy"
    assert result["error"] is None
    assert set(result["artifactFingerprints"]) == EXPECTED_ARTIFACT_KEYS
    cache_dir = tmp_path / "benchmark-cache"
    assert cache_dir.is_dir()
    assert any(cache_dir.iterdir())
    assert not (tmp_path / ".nbs_runtime_cache").exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == before
    assert _sha256(db_path) == db_hash_before


def test_benchmark_rejects_unknown_mode(tmp_path):
    db_path, _, _, receipt = _active(tmp_path)

    try:
        run_gmv_cache_benchmark(
            db_path=db_path,
            version_id=receipt.version_id,
            cache_dir=tmp_path / "benchmark-cache",
            mode="unknown",
            workers=2,
        )
    except ValueError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("unknown benchmark mode must fail closed")


def test_benchmark_rejects_formal_cache_path(tmp_path):
    db_path, _, _, receipt = _active(tmp_path)
    try:
        run_gmv_cache_benchmark(
            db_path=db_path,
            version_id=receipt.version_id,
            cache_dir=tmp_path / ".nbs_runtime_cache",
            mode="legacy",
            workers=2,
        )
    except ValueError as exc:
        assert "formal runtime cache" in str(exc)
    else:
        raise AssertionError("formal cache path must fail closed")


def test_fast_benchmark_accepts_bounded_worker_count(tmp_path):
    db_path, _, _, receipt = _active(tmp_path)
    result = run_gmv_cache_benchmark(
        db_path=db_path, version_id=receipt.version_id,
        cache_dir=tmp_path / "benchmark-cache-fast", mode="fast", workers=1,
    )
    assert result["mode"] == "fast"
    assert result["artifactCount"] == 11


def test_trusted_warm_benchmark_reuses_manifest_identity(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.services.gmv_refund_service._gmv_baseline_status", lambda **kwargs: "PASS")
    db_path, _, _, receipt = _active(tmp_path)
    cache_dir = tmp_path / "benchmark-cache-warm"
    shadow = run_gmv_cache_benchmark(
        db_path=db_path, version_id=receipt.version_id,
        cache_dir=cache_dir, mode="shadow", workers=1,
    )
    warm = run_gmv_cache_benchmark(
        db_path=db_path, version_id=receipt.version_id,
        cache_dir=cache_dir, mode="trusted_warm", workers=1,
    )
    assert shadow["contentFingerprint"] == warm["contentFingerprint"]
    assert warm["referenceStatus"] == "HIT"
    assert warm["shadowStatus"] == "PASS"
    assert warm["lookupMs"] >= 0
    assert warm["validationMs"] >= 0


def test_benchmark_cli_writes_json_only_under_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / "benchmark-cache"
    output = cache_dir / "result.json"
    monkeypatch.setattr(
        benchmark_module,
        "run_gmv_cache_benchmark",
        lambda **kwargs: {"mode": kwargs["mode"], "workers": kwargs["workers"]},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_gmv_refund_cache.py",
            "--db-path", "db.sqlite",
            "--version-id", "version-1",
            "--cache-dir", str(cache_dir),
            "--mode", "legacy",
            "--workers", "2",
            "--output", str(output),
        ],
    )
    benchmark_module.main()
    assert json.loads(output.read_text(encoding="utf-8")) == {"mode": "legacy", "workers": 2}
