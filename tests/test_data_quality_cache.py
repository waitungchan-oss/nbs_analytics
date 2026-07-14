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
