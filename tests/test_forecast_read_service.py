import pickle

import pandas as pd

from backend.services import forecast_read_service


def test_forecast_reader_builds_daily_and_macro_views(tmp_path):
    dates = pd.date_range("2026-06-25", periods=30)
    ts = pd.DataFrame({"Revenue": [100.0] * 30}, index=pd.date_range("2026-05-26", periods=30))
    series = pd.Series([100.0] * 30, index=dates)
    payload = {
        "version": "daily-macro-normal-tight-v1",
        "data": {
            "ptrk": (ts, series, series * 2, series * 3),
            "err": None,
            "bt": {"weights": pd.DataFrame([{"權重版本": "1 日", "推薦策略": "總額模型", "ARIMA": 1, "Prophet": 0, "LightGBM": 0}])},
            "bt_macro": {"summary": pd.DataFrame()},
        },
    }
    path = tmp_path / "ai_test.pkl"
    path.write_bytes(pickle.dumps(payload))

    result = forecast_read_service.build_forecast_from_cache(path)

    assert result["status"] == "ready"
    assert len(result["daily"]) == 30
    assert result["daily"][0]["consensus"] == 100.0
    assert result["sevenDay"]["consensus"] == 700.0
    assert result["monthEnd"]["month"] == "2026-06"


def test_forecast_reader_returns_not_ready_for_missing_cache(tmp_path):
    result = forecast_read_service.build_forecast_read_model(tmp_path)
    assert result["status"] == "not_ready"
    assert "cache" in result["message"].lower()

