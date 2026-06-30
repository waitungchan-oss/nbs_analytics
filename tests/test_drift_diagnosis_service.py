import pandas as pd

from backend.services.drift_diagnosis_service import build_upload_drift_diagnosis


def test_drift_diagnosis_reports_new_excluded_receipt_as_driver():
    live_tour = pd.DataFrame(
        [
            {
                "來源單據號": "SO-001",
                "收款單號": "R-001",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "銷售點": "主表A",
                "副表_銷售點": "副表A",
                "銷售員": "Amy",
                "統一日期": "2026-05-03",
            }
        ]
    )
    temp_tour = pd.DataFrame(
        [
            {
                "來源單據號": "SO-001",
                "收款單號": "R-001",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "銷售點": "主表A",
                "副表_銷售點": "副表A",
                "銷售員": "Amy",
                "統一日期": "2026-05-03",
            },
            {
                "來源單據號": "SO-001",
                "收款單號": "R-002",
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "收款原幣金額": 500.0,
                "銷售點": "主表A",
                "副表_銷售點": "副表A",
                "銷售員": "Amy",
                "統一日期": "2026-06-25",
            },
        ]
    )

    diagnosis = build_upload_drift_diagnosis(
        live_tour,
        pd.DataFrame(),
        temp_tour,
        pd.DataFrame(),
        stability_gate={
            "status": "drift",
            "baselineMonth": "2026-05",
            "expectedTotal": 12057968.0,
            "actualTotal": 12056968.0,
            "deltaAmount": -1000.0,
        },
    )

    assert diagnosis["status"] == "drift"
    assert diagnosis["baselineMonth"] == "2026-05"
    assert diagnosis["deltaAmount"] == -1000.0
    assert diagnosis["topDrivers"][0]["sourceOrderNo"] == "SO-001"
    assert diagnosis["topDrivers"][0]["receiptNo"] == "R-002"
    assert "排除" in diagnosis["topDrivers"][0]["reason"]
    assert diagnosis["sourceOrderDiffs"][0]["tempExcluded"] is True
    assert diagnosis["excludedReceiptDiffs"][0]["receiptNo"] == "R-002"


def test_drift_diagnosis_returns_no_drift_for_identical_frames():
    frame = pd.DataFrame(
        [
            {
                "來源單據號": "SO-002",
                "收款單號": "R-010",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 900.0,
                "銷售點": "主表B",
                "副表_銷售點": "副表B",
                "銷售員": "Ben",
                "統一日期": "2026-05-05",
            }
        ]
    )

    diagnosis = build_upload_drift_diagnosis(
        frame,
        pd.DataFrame(),
        frame.copy(),
        pd.DataFrame(),
        stability_gate={
            "status": "matched",
            "baselineMonth": "2026-05",
            "expectedTotal": 12057968.0,
            "actualTotal": 12057968.0,
            "deltaAmount": 0.0,
        },
    )

    assert diagnosis["status"] == "no_drift"
    assert diagnosis["topDrivers"] == []
    assert diagnosis["summaryMessage"] == "核心口徑未漂移。"
