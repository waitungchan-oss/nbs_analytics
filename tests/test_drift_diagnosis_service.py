import pandas as pd

from backend.services.drift_diagnosis_service import build_upload_drift_diagnosis


def test_drift_diagnosis_skips_row_level_diff_when_core_gate_is_matched(monkeypatch):
    from backend.services import drift_diagnosis_service

    frame = pd.DataFrame(
        [
            {
                "來源單據號": f"SO-{index:04d}",
                "收款單號": f"R-{index:04d}",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 100.0,
                "統一日期": "2026-07-02",
            }
            for index in range(200)
        ]
    )

    def fail_if_row_level_diff_runs(*args, **kwargs):
        raise AssertionError("row-level diff should not run when the core gate is matched")

    monkeypatch.setattr(drift_diagnosis_service, "_frame_with_keys", fail_if_row_level_diff_runs)

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
    assert diagnosis["sourceOrderDiffs"] == []
    assert diagnosis["receiptDiffs"] == []
    assert diagnosis["excludedReceiptDiffs"] == []
    assert diagnosis["topDrivers"] == []


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


def test_drift_diagnosis_limits_receipt_detail_diff_to_candidate_orders(monkeypatch):
    from backend.services import drift_diagnosis_service

    unchanged_rows = [
        {
            "來源單據號": f"UNCHANGED-{index:04d}",
            "收款單號": f"RU-{index:04d}",
            "收款類型": "旅費",
            "收款方式": "現金",
            "收款原幣金額": 100.0,
            "統一日期": "2026-05-03",
        }
        for index in range(200)
    ]
    live_tour = pd.DataFrame(
        unchanged_rows
        + [
            {
                "來源單據號": "SO-CHANGED",
                "收款單號": "R-CHANGED",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-03",
            }
        ]
    )
    temp_tour = live_tour.copy()
    temp_tour.loc[temp_tour["來源單據號"] == "SO-CHANGED", "收款原幣金額"] = 700.0

    original_frame_with_keys = drift_diagnosis_service._frame_with_keys
    observed_sizes = []

    def recording_frame_with_keys(frame, label):
        observed_sizes.append((label, int(len(frame)), sorted(set(frame["來源單據號"].astype(str)))))
        return original_frame_with_keys(frame, label)

    monkeypatch.setattr(drift_diagnosis_service, "_frame_with_keys", recording_frame_with_keys)

    diagnosis = build_upload_drift_diagnosis(
        live_tour,
        pd.DataFrame(),
        temp_tour,
        pd.DataFrame(),
        stability_gate={
            "status": "drift",
            "baselineMonth": "2026-05",
            "expectedTotal": 12057968.0,
            "actualTotal": 12057668.0,
            "deltaAmount": -300.0,
        },
    )

    assert diagnosis["sourceOrderDiffs"][0]["sourceOrderNo"] == "SO-CHANGED"
    assert diagnosis["receiptDiffs"][0]["sourceOrderNo"] == "SO-CHANGED"
    assert diagnosis["detailMode"] == "candidate_order_scope"
    assert diagnosis["candidateOrderCount"] == 1
    assert diagnosis["candidateReceiptRows"] == {"live": 1, "temp": 1}
    assert observed_sizes == [
        ("live", 1, ["SO-CHANGED"]),
        ("temp", 1, ["SO-CHANGED"]),
    ]


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
