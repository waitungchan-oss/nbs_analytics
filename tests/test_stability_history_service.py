import importlib
import sqlite3

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
            "monthly_baseline": {
                "registryVersion": "monthly-revenue-v1",
                "allMatched": True,
                "checks": [{"month": "2026-01", "status": "matched"}],
            },
            "operation_id": "op-1",
            "entry_point": "streamlit",
            "stage_timings": [{"階段": "正式 SQLite upsert", "秒數": 0.2}],
            "cache_state": "streamlit_rebuilt",
            "cache_error": None,
            "data_generation": {"generation": 4, "operationId": "op-1"},
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
    assert rows[0]["monthlyBaseline"] == {
        "registryVersion": "monthly-revenue-v1",
        "allMatched": True,
        "checks": [{"month": "2026-01", "status": "matched"}],
    }
    assert rows[0]["operationId"] == "op-1"
    assert rows[0]["entryPoint"] == "streamlit"
    assert rows[0]["stageTimings"] == [{"階段": "正式 SQLite upsert", "秒數": 0.2}]
    assert rows[0]["cacheState"] == "streamlit_rebuilt"
    assert rows[0]["cacheError"] is None
    assert rows[0]["dataGeneration"]["generation"] == 4


def test_stability_history_uses_explicit_database_path_without_touching_default(tmp_path, monkeypatch):
    default_path = tmp_path / "default.db"
    history_path = tmp_path / "history.db"
    monkeypatch.setattr(database, "DB_FILE", str(default_path))
    history_service = importlib.import_module("backend.services.stability_history_service")

    history_service.record_stability_history(
        _sample_gate(),
        {"upload_status": "success", "upload_message": "explicit", "source_files": []},
        db_path=history_path,
    )

    rows = history_service.list_stability_history(limit=10, db_path=history_path)

    assert len(rows) == 1
    assert rows[0]["uploadMessage"] == "explicit"
    assert not default_path.exists()


def test_legacy_history_rows_deserialize_new_evidence_fields_with_defaults(tmp_path):
    history_path = tmp_path / "legacy-history.db"
    connection = sqlite3.connect(history_path)
    try:
        connection.execute(
            """
            CREATE TABLE stability_gate_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                upload_status TEXT NOT NULL,
                upload_message TEXT NOT NULL,
                source_files_json TEXT NOT NULL,
                core_status TEXT NOT NULL,
                baseline_month TEXT,
                formatted_expected_total TEXT,
                formatted_actual_total TEXT,
                delta_amount REAL NOT NULL DEFAULT 0,
                matched_checks INTEGER NOT NULL DEFAULT 0,
                total_checks INTEGER NOT NULL DEFAULT 0,
                drift_check_count INTEGER NOT NULL DEFAULT 0,
                freshness_status TEXT NOT NULL,
                freshness_update_count INTEGER NOT NULL DEFAULT 0,
                latest_data_date TEXT,
                batch_summary_json TEXT NOT NULL,
                upsert_summary_json TEXT NOT NULL,
                drift_diagnosis_json TEXT NOT NULL,
                gate_json TEXT NOT NULL,
                monthly_baseline_json TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO stability_gate_history (
                created_at, upload_status, upload_message, source_files_json,
                core_status, freshness_status, batch_summary_json,
                upsert_summary_json, drift_diagnosis_json, gate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-12T12:00:00+08:00",
                "accepted",
                "legacy",
                "[]",
                "matched",
                "stable",
                "[]",
                "[]",
                "{}",
                "{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    history_service = importlib.import_module("backend.services.stability_history_service")
    row = history_service.list_stability_history(limit=1, db_path=history_path)[0]

    assert row["uploadMessage"] == "legacy"
    assert row["operationId"] is None
    assert row["stageTimings"] == []
    assert row["dataGeneration"] == {}


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
