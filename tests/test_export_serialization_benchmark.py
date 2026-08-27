import pandas as pd


def _frames():
    return (
        pd.DataFrame([{
            "來源單據號": "T-001", "統一日期": "2026-05-01", "銷售點": "銅鑼灣分社",
            "銷售員": "Alice", "收款原幣金額": 100, "收款類型": "正常收款",
            "收款方式": "現金", "團負責人部門": "", "交易時間": "2026-05-01",
            "行程天數": 3, "數量": 2,
        }]),
        pd.DataFrame([{
            "來源單據號": "O-001", "統一日期": "2026-05-01", "銷售點": "銅鑼灣分社",
            "銷售員": "Alice", "收款原幣金額": 25, "收款類型": "正常收款",
            "收款方式": "信用卡", "交易時間": "2026-05-01", "團名稱": "景點門票",
            "來源報表標籤": "門券all", "行程天數": 0, "數量": 3,
        }]),
    )


def test_legacy_export_measurement_has_three_artifacts_and_stage_timings():
    from app_workflows import _compute_export_workbooks
    from backend.services.export_benchmark_service import measure_legacy_export

    tour, others = _frames()
    result = measure_legacy_export(_compute_export_workbooks, tour, others)

    assert set(result.artifacts) == {"ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer"}
    assert all(item.bytes_written > 0 for item in result.artifacts.values())
    assert result.timings["serialization_ms"] >= 0
    assert result.timings["total_ms"] >= result.timings["serialization_ms"]
