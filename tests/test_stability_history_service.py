import importlib

import database


def _sample_gate():
    return {
        "status": "matched",
        "message": "重建成功，核心口徑穩定：2/2 checks matched；資料已更新 3 項。",
        "baselineMonth": "2026-05",
        "formattedExpectedTotal": "HKD 12,057,968",
        "formattedActualTotal": "HKD 12,057,968",
        "deltaAmount": -0.08,
        "matchedChecks": 2,
        "totalChecks": 2,
        "driftCheckCount": 0,
        "freshnessStatus": "updated",
        "freshnessUpdateCount": 3,
        "freshnessUpdates": [
            {
                "key": "maxDate",
                "label": "最新收款日期",
                "expected": "2026-06-22",
                "actual": "2026-06-23",
                "status": "updated",
            }
        ],
    }


def test_record_and_list_stability_history_uses_dedicated_audit_table(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "history.db"))
    history_service = importlib.import_module("backend.services.stability_history_service")

    record_id = history_service.record_stability_history(
        _sample_gate(),
        {
            "upload_status": "success",
            "upload_message": "上傳完成",
            "source_files": ["main.xlsx", "tour.xlsx"],
            "latest_data_date": "2026-06-23",
            "batch_summary": [{"資料集": "旅行團", "筆數": 89}],
            "upsert_summary": [{"資料表": "tour_data", "新增": 89}],
            "drift_diagnosis": {"status": "no_drift", "summaryMessage": "核心口徑未漂移。"},
            "rollback_status": "verified",
            "backup_path": "backup.db",
            "quarantine_path": "quarantine.db",
            "post_rollback_gate": {"status": "matched"},
            "rollback_error": None,
        },
    )
    rows = history_service.list_stability_history(limit=10)

    assert record_id == 1
    assert len(rows) == 1
    assert rows[0]["id"] == 1
    assert rows[0]["uploadStatus"] == "success"
    assert rows[0]["coreStatus"] == "matched"
    assert rows[0]["freshnessStatus"] == "updated"
    assert rows[0]["freshnessUpdateCount"] == 3
    assert rows[0]["latestDataDate"] == "2026-06-23"
    assert rows[0]["sourceFiles"] == ["main.xlsx", "tour.xlsx"]
    assert rows[0]["driftDiagnosis"] == {"status": "no_drift", "summaryMessage": "核心口徑未漂移。"}
    assert rows[0]["gate"]["baselineMonth"] == "2026-05"
    assert rows[0]["rollbackStatus"] == "verified"
    assert rows[0]["backupPath"] == "backup.db"
    assert rows[0]["quarantinePath"] == "quarantine.db"
    assert rows[0]["postRollbackGate"] == {"status": "matched"}
    assert rows[0]["rollbackError"] is None


def test_stability_history_limit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "history.db"))
    history_service = importlib.import_module("backend.services.stability_history_service")

    for index in range(3):
        history_service.record_stability_history(
            _sample_gate(),
            {
                "upload_status": "success",
                "upload_message": f"batch-{index}",
                "source_files": [f"main-{index}.xlsx"],
                "latest_data_date": "2026-06-23",
                "drift_diagnosis": {"status": "no_drift"},
            },
        )

    rows = history_service.list_stability_history(limit=2)

    assert [row["uploadMessage"] for row in rows] == ["batch-2", "batch-1"]
