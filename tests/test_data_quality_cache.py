import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from backend.services import data_quality_service as service


def _raw_frame():
    return pd.DataFrame(
        [
            {
                "來源單據號": "A001",
                "收款時間": "2026-07-01",
                "統一日期": "2026-07-01",
                "收款原幣金額": 100.0,
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "來源報表標籤": "旅行團",
            }
        ]
    )


def test_data_quality_cache_reuses_same_generation(monkeypatch, tmp_path):
    calls = []

    def load_frames(*, db_path):
        calls.append(db_path)
        return _raw_frame(), pd.DataFrame()

    monkeypatch.setattr(service, "load_all_data_from_db", load_frames)

    first = service.build_data_quality_cached(
        db_path=tmp_path / "quality.db",
        generation_token="1:abc",
        cache_dir=tmp_path / "cache",
    )
    second = service.build_data_quality_cached(
        db_path=tmp_path / "quality.db",
        generation_token="1:abc",
        cache_dir=tmp_path / "cache",
    )

    assert first["cacheStatus"] == "rebuilt"
    assert second["cacheStatus"] == "hit"
    assert first["generationToken"] == second["generationToken"] == "1:abc"
    assert len(calls) == 1


def test_data_quality_cache_rebuilds_for_new_generation(monkeypatch, tmp_path):
    calls = []

    def load_frames(*, db_path):
        calls.append(db_path)
        return _raw_frame(), pd.DataFrame()

    monkeypatch.setattr(service, "load_all_data_from_db", load_frames)
    kwargs = {"db_path": tmp_path / "quality.db", "cache_dir": tmp_path / "cache"}

    first = service.build_data_quality_cached(generation_token="1:abc", **kwargs)
    second = service.build_data_quality_cached(generation_token="2:def", **kwargs)

    assert first["cacheStatus"] == second["cacheStatus"] == "rebuilt"
    assert second["generationToken"] == "2:def"
    assert len(calls) == 2


def test_data_quality_cache_repairs_corrupted_json(monkeypatch, tmp_path):
    calls = []

    def load_frames(*, db_path):
        calls.append(db_path)
        return _raw_frame(), pd.DataFrame()

    monkeypatch.setattr(service, "load_all_data_from_db", load_frames)
    cache_dir = tmp_path / "cache"
    kwargs = {
        "db_path": tmp_path / "quality.db",
        "generation_token": "1:abc",
        "cache_dir": cache_dir,
    }
    service.build_data_quality_cached(**kwargs)
    cache_file = next(cache_dir.glob("data_quality_*.json"))
    cache_file.write_text("{broken", encoding="utf-8")

    rebuilt = service.build_data_quality_cached(**kwargs)

    assert rebuilt["cacheStatus"] == "rebuilt"
    assert len(calls) == 2
    assert cache_file.read_text(encoding="utf-8").startswith("{")


def test_data_quality_cache_rebuilds_tampered_payload(monkeypatch, tmp_path):
    calls = []

    def load_frames(*, db_path):
        calls.append(db_path)
        return _raw_frame(), pd.DataFrame()

    monkeypatch.setattr(service, "load_all_data_from_db", load_frames)
    cache_dir = tmp_path / "cache"
    kwargs = {
        "db_path": tmp_path / "quality.db",
        "generation_token": "1:abc",
        "cache_dir": cache_dir,
    }
    service.build_data_quality_cached(**kwargs)
    cache_file = next(cache_dir.glob("data_quality_*.json"))
    wrapper = json.loads(cache_file.read_text(encoding="utf-8"))
    wrapper["payload"]["overallScore"] = -1
    cache_file.write_text(json.dumps(wrapper, ensure_ascii=False), encoding="utf-8")

    rebuilt = service.build_data_quality_cached(**kwargs)

    assert rebuilt["cacheStatus"] == "rebuilt"
    assert rebuilt["overallScore"] >= 0
    assert len(calls) == 2


def test_data_quality_atomic_writes_use_unique_temporary_files(monkeypatch, tmp_path):
    barrier = threading.Barrier(2)
    original_replace = service.os.replace

    def synchronized_replace(source, destination):
        barrier.wait(timeout=2)
        original_replace(source, destination)

    monkeypatch.setattr(service.os, "replace", synchronized_replace)
    path = tmp_path / "quality.json"
    payload = {"status": "ready", "scope": "scope", "overallScore": 100}

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda index: service._save_data_quality_cache(
                    path,
                    "cache-key",
                    "1:abc",
                    {**payload, "writer": index},
                ),
                range(2),
            )
        )

    assert path.exists()


def test_data_quality_cache_single_flight_avoids_duplicate_rebuild(monkeypatch, tmp_path):
    calls = []
    start = threading.Barrier(3)

    def load_frames(*, db_path):
        calls.append(db_path)
        time.sleep(0.05)
        return _raw_frame(), pd.DataFrame()

    def build():
        start.wait(timeout=2)
        return service.build_data_quality_cached(
            db_path=tmp_path / "quality.db",
            generation_token="1:abc",
            cache_dir=tmp_path / "cache",
        )

    monkeypatch.setattr(service, "load_all_data_from_db", load_frames)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(build) for _ in range(2)]
        start.wait(timeout=2)
        results = [future.result(timeout=3) for future in futures]

    assert len(calls) == 1
    assert sorted(result["cacheStatus"] for result in results) == ["hit", "rebuilt"]
