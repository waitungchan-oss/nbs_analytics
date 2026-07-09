import pandas as pd

import database


def test_virtual_upload_profiling_dry_run_uses_temp_db_and_reports_stage_timings(tmp_path, monkeypatch):
    from backend.services import upload_profiling_service

    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))

    conn = database.sqlite3.connect(live_path)
    try:
        pd.DataFrame(
            [
                {
                    "來源單據號": "1700000001",
                    "收款單號": "SK2605000001",
                    "收款類型": "旅費",
                    "收款方式": "現金",
                    "收款原幣金額": 1000.0,
                    "收款時間": "2026-05-01",
                    "統一日期": "2026-05-01",
                }
            ]
        ).to_sql("tour_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

    matched_gate = {
        "status": "matched",
        "formattedExpectedTotal": "HKD 12,057,968",
        "formattedActualTotal": "HKD 12,057,968",
        "deltaAmount": 0.0,
        "coreValidation": {"status": "matched", "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0}, "checks": []},
        "stabilityBaseline": {"formattedActualTotal": "HKD 12,057,968"},
        "driftChecks": [],
    }

    monkeypatch.setattr(upload_profiling_service, "build_phase2c_stability_gate", lambda: matched_gate)
    monkeypatch.setattr(upload_profiling_service.upload_preflight_service, "build_phase2c_stability_gate", lambda: matched_gate)
    monkeypatch.setattr(
        upload_profiling_service.upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {"status": "no_drift", "summaryMessage": "核心口徑未漂移。"},
    )

    report = upload_profiling_service.run_virtual_upload_profiling_dry_run(row_count=3)

    assert report["dryRun"] is True
    assert report["liveDbUnchanged"] is True
    assert report["preflightStatus"] == "matched"
    assert report["driftDiagnosisMode"] == "skipped"
    assert report["rollbackStatus"] == "not_required"
    assert report["tempDbPath"]
    assert report["sourceRows"] == 3
    assert report["liveBefore"]["tour_rows"] == 1
    assert report["liveAfter"]["tour_rows"] == 1

    labels = [item["階段"] for item in report["stageTimings"]]
    assert labels == [
        "建立虛擬上傳資料",
        "Preflight 臨時 DB 與口徑驗收",
        "正式 SQLite upsert（dry-run temp DB）",
        "Dashboard summary rebuild（dry-run temp DB）",
        "寫入後 SQLite reload（dry-run temp DB）",
        "Stability gate 驗證（dry-run temp DB）",
        "Rollback guard（dry-run temp DB）",
        "Upload dry-run total",
    ]
