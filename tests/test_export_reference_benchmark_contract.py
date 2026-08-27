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


def test_benchmark_distinguishes_materialization_hit_and_stale_scenarios():
    from scripts.benchmark_data_export_serialization import build_reference_benchmark_report

    report = build_reference_benchmark_report(*_frames(), worker_count=1)

    assert {"first_materialization", "same_identity_hit", "stale_identity"} <= set(report)
    assert report["database_mutated"] is False
    assert report["first_materialization"]["reference_status"] == "MATERIALIZED"
    assert report["same_identity_hit"]["reference_status"] == "HIT"
    assert report["same_identity_hit"]["deep_diff_skipped"] is True
    assert report["stale_identity"]["reference_status"] == "MATERIALIZED"
    assert report["stale_identity"]["deep_diff_skipped"] is False
