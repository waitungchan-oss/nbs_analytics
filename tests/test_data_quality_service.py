import pandas as pd

from backend.services import data_quality_service


def test_quality_summary_reports_five_dimensions_and_scope():
    raw = pd.DataFrame([
        {"來源單據號": "A1", "收款時間": "2026-06-01", "收款原幣金額": 100, "銷售點": "A", "銷售員": "X", "來源報表標籤": "旅行團"},
        {"來源單據號": "A1", "收款時間": "2026-06-03", "收款原幣金額": 0, "銷售點": "A", "銷售員": "", "來源報表標籤": "未匹配"},
    ])

    payload = data_quality_service.build_data_quality_from_frames(
        raw,
        pd.DataFrame(),
        raw.iloc[:1],
        pd.DataFrame(),
        {"raw_rows": 2, "analysis_rows": 1, "excluded_rows": 1, "raw_amount": 100, "analysis_amount": 100, "excluded_amount": 0},
    )

    assert payload["status"] == "ready"
    assert len(payload["dimensions"]) == 5
    assert payload["latestDate"] == "2026-06-03"
    assert payload["missingDays"] == 1
    assert payload["unmatchedRows"] == 1
    assert payload["scope"] == "不含掛賬核銷與TT退款轉團款"

