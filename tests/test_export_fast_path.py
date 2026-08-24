import io
import time

import pandas as pd


def _fixture_frames():
    tour = pd.DataFrame(
        [
            {
                "來源單據號": "T-001",
                "統一日期": "2026-05-01",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Alice",
                "收款原幣金額": 100,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "團負責人部門": "",
                "交易時間": "2026-05-01",
                "行程天數": 3,
                "數量": 2,
            }
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "O-001",
                "統一日期": "2026-05-01",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Alice",
                "收款原幣金額": 25,
                "收款類型": "正常收款",
                "收款方式": "信用卡",
                "交易時間": "2026-05-01",
                "團名稱": "景點門票",
                "來源報表標籤": "門券all",
                "行程天數": 0,
                "數量": 3,
            }
        ]
    )
    return tour, others


def test_legacy_export_produces_three_workbooks():
    import app_workflows

    tour, others = _fixture_frames()
    started = time.perf_counter()
    payload = app_workflows._compute_export_workbooks(tour, others)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    keys = ("ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer")
    assert all(isinstance(payload[key], bytes) and payload[key] for key in keys)
    assert payload["export_cache_version"] == app_workflows.EXPORT_CACHE_VERSION
    assert elapsed_ms >= 0
    assert all(len(payload[key]) > 0 for key in keys)
