import pandas as pd

import database


def test_virtual_upload_profiling_dry_run_uses_temp_db_and_reports_stage_timings(tmp_path, monkeypatch):
    from backend.services import upload_profiling_service

    live_path = tmp_path / "live.db"
    default_path = tmp_path / "default.db"
    monkeypatch.setattr(database, "DB_FILE", str(default_path))

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

    observed = {"gate_paths": [], "db_files_during_gates": []}

    def fake_gate(*, db_path=None):
        observed["gate_paths"].append(database.resolve_db_path(db_path))
        observed["db_files_during_gates"].append(database.DB_FILE)
        return matched_gate

    monkeypatch.setattr(upload_profiling_service, "build_phase2c_stability_gate", fake_gate)
    monkeypatch.setattr(upload_profiling_service.upload_preflight_service, "build_phase2c_stability_gate", fake_gate)
    monkeypatch.setattr(
        upload_profiling_service.upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {"status": "no_drift", "summaryMessage": "核心口徑未漂移。"},
    )

    report = upload_profiling_service.run_virtual_upload_profiling_dry_run(
        row_count=3,
        live_db_path=live_path,
    )

    assert report["dryRun"] is True
    assert report["liveDbUnchanged"] is True
    assert report["preflightStatus"] == "matched"
    assert report["driftDiagnosisMode"] == "skipped"
    assert report["rollbackStatus"] == "not_required"
    assert report["tempDbPath"]
    assert report["sourceRows"] == 3
    assert report["liveBefore"]["tour_rows"] == 1
    assert report["liveAfter"]["tour_rows"] == 1
    assert observed["gate_paths"]
    assert all(path != live_path for path in observed["gate_paths"])
    assert observed["db_files_during_gates"] == [str(default_path)] * len(observed["gate_paths"])
    assert database.DB_FILE == str(default_path)

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
