from io import BytesIO

import pandas as pd

import database


def test_upload_preflight_uses_temp_database_and_preserves_live_db(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service

    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))

    live_conn = database.sqlite3.connect(live_path)
    try:
        pd.DataFrame(
            [
                {
                    "來源單據號": "OLD001",
                    "收款單號": "SK2605000001",
                    "收款類型": "旅費",
                    "收款方式": "現金",
                    "收款原幣金額": 1000.0,
                    "統一日期": "2026-05-01",
                }
            ]
        ).to_sql("tour_data", live_conn, if_exists="replace", index=False)
        live_conn.commit()
    finally:
        live_conn.close()

    main_file = BytesIO(b"dummy")
    main_file.name = "財務收款總數-0101-0625.xlsx"  # type: ignore[attr-defined]

    tour_df = pd.DataFrame(
        [
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2605000001",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-01",
            },
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2606000002",
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "收款原幣金額": 500.0,
                "統一日期": "2026-06-25",
            },
        ]
    )
    others_df = pd.DataFrame(
        [
            {
                "來源單據號": "O001",
                "收款單號": "O001",
                "收款類型": "正常收款",
                "收款方式": "現金",
                "收款原幣金額": 200.0,
                "收款時間": "2026-06-15 09:19:16",
            }
        ]
    )

    def fake_process_raw_files(*args, **kwargs):
        return tour_df.copy(), others_df.copy(), pd.DataFrame(), {"summary": pd.DataFrame()}

    monkeypatch.setattr(upload_preflight_service, "process_raw_files", fake_process_raw_files)
    monkeypatch.setattr(
        upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {
            "status": "no_drift",
            "baselineMonth": "2026-05",
            "expectedTotal": 12057968.0,
            "actualTotal": 12057968.0,
            "deltaAmount": 0.0,
            "summaryMessage": "核心口徑未漂移。",
            "rowLimit": 50,
            "sourceOrderDiffs": [],
            "receiptDiffs": [],
            "excludedReceiptDiffs": [],
            "topDrivers": [],
        },
    )
    monkeypatch.setattr(
        upload_preflight_service,
        "build_phase2c_stability_gate",
        lambda: {
            "status": "matched",
            "formattedExpectedTotal": "HKD 12,057,968",
            "formattedActualTotal": "HKD 12,057,968",
            "deltaAmount": -0.08,
            "deltaPct": 0.0,
            "totalChecks": 2,
            "matchedChecks": 2,
            "driftCheckCount": 0,
            "driftChecks": [],
            "coreValidation": {"status": "matched", "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0}, "checks": []},
            "freshnessStatus": "stable",
            "freshnessUpdateCount": 0,
            "freshnessUpdates": [],
            "freshnessUpdate": {"status": "stable", "summary": {"totalChecks": 3, "stableChecks": 3, "updatedChecks": 0}, "checks": []},
            "stabilityBaseline": {"formattedActualTotal": "HKD 12,057,968"},
        },
    )

    report = upload_preflight_service.run_upload_preflight(
        main_file,
        None,
        [],
        branch_mapping={"OLD": "Legacy Branch"},
        exclude_prefixes=[],
        sales_reps=[],
        source_files=["財務收款總數-0101-0625.xlsx"],
    )

    assert report["status"] == "matched"
    assert report["filteredExcludedRows"] == 1
    assert report["writeRows"] == 2
    assert report["sourceFiles"] == ["財務收款總數-0101-0625.xlsx"]
    assert report["liveDbUnchanged"] is True
    assert report["driftDiagnosis"]["status"] == "no_drift"
    assert report["prepared"]["tour"].shape[0] == 2
    assert report["prepared"]["others"].shape[0] == 1
    assert report["batchSummary"][0]["包含 2026-06-15"] is False
    assert report["batchSummary"][1]["包含 2026-06-15"] is True
    assert report["batchSummary"][1]["最早收款時間"] == "2026-06-15 09:19:16"
    timing_labels = [item["階段"] for item in report["stageTimings"]]
    assert "建立 Preflight 臨時 DB" in timing_labels
    assert "清洗與 Entity Resolution" in timing_labels
    assert "臨時 SQLite upsert" in timing_labels
    assert "Preflight stability gate" in timing_labels
    assert "Drift diagnosis" in timing_labels
    assert "Preflight total" in timing_labels
