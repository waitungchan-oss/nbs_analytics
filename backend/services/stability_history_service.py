from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import database

TABLE_NAME = "stability_gate_history"


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, default=str)


def _json_load(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _ensure_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
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
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_created_at "
        f"ON {TABLE_NAME}(created_at DESC)"
    )
    existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
    migrations = {
        "rollback_status": "TEXT",
        "backup_path": "TEXT",
        "quarantine_path": "TEXT",
        "post_rollback_gate_json": "TEXT",
        "rollback_error": "TEXT",
        "drift_diagnosis_json": "TEXT",
        "monthly_baseline_json": "TEXT",
        "operation_id": "TEXT",
        "entry_point": "TEXT",
        "stage_timings_json": "TEXT",
        "cache_state": "TEXT",
        "cache_error": "TEXT",
        "data_generation_json": "TEXT",
    }
    for column, sqlite_type in migrations.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {column} {sqlite_type}")
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE_NAME}_operation_id "
        f"ON {TABLE_NAME}(operation_id) WHERE operation_id IS NOT NULL"
    )


def record_stability_history(
    gate: dict,
    context: dict | None = None,
    *,
    db_path: str | None = None,
) -> int:
    context = context or {}
    conn = database.get_db_connection(db_path=db_path)
    try:
        _ensure_table(conn)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        cursor = conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                created_at, upload_status, upload_message, source_files_json,
                core_status, baseline_month, formatted_expected_total,
                formatted_actual_total, delta_amount, matched_checks,
                total_checks, drift_check_count, freshness_status,
                freshness_update_count, latest_data_date, batch_summary_json,
                upsert_summary_json, drift_diagnosis_json, gate_json, rollback_status,
                monthly_baseline_json, backup_path, quarantine_path, post_rollback_gate_json, rollback_error,
                operation_id, entry_point, stage_timings_json, cache_state, cache_error, data_generation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                str(context.get("upload_status") or "unknown"),
                str(context.get("upload_message") or ""),
                _json_dump(context.get("source_files") or []),
                str(gate.get("status") or "unknown"),
                gate.get("baselineMonth"),
                gate.get("formattedExpectedTotal"),
                gate.get("formattedActualTotal"),
                float(gate.get("deltaAmount") or 0),
                int(gate.get("matchedChecks") or 0),
                int(gate.get("totalChecks") or 0),
                int(gate.get("driftCheckCount") or 0),
                str(gate.get("freshnessStatus") or "unknown"),
                int(gate.get("freshnessUpdateCount") or 0),
                context.get("latest_data_date"),
                _json_dump(context.get("batch_summary") or []),
                _json_dump(context.get("upsert_summary") or []),
                _json_dump(context.get("drift_diagnosis") or {}),
                _json_dump(gate),
                context.get("rollback_status"),
                _json_dump(context.get("monthly_baseline") or gate.get("monthlyBaseline") or {}),
                context.get("backup_path"),
                context.get("quarantine_path"),
                _json_dump(context.get("post_rollback_gate") or {}),
                context.get("rollback_error"),
                context.get("operation_id"),
                context.get("entry_point"),
                _json_dump(context.get("stage_timings") or []),
                context.get("cache_state"),
                context.get("cache_error"),
                _json_dump(context.get("data_generation") or {}),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def list_stability_history(
    limit: int = 20,
    *,
    db_path: str | None = None,
) -> list[dict]:
    bounded_limit = max(1, min(int(limit), 100))
    conn = database.get_db_connection(db_path=db_path)
    try:
        _ensure_table(conn)
        conn.commit()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {TABLE_NAME} ORDER BY id DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": int(row["id"]),
            "createdAt": row["created_at"],
            "uploadStatus": row["upload_status"],
            "uploadMessage": row["upload_message"],
            "sourceFiles": _json_load(row["source_files_json"], []),
            "coreStatus": row["core_status"],
            "baselineMonth": row["baseline_month"],
            "formattedExpectedTotal": row["formatted_expected_total"],
            "formattedActualTotal": row["formatted_actual_total"],
            "deltaAmount": float(row["delta_amount"] or 0),
            "matchedChecks": int(row["matched_checks"] or 0),
            "totalChecks": int(row["total_checks"] or 0),
            "driftCheckCount": int(row["drift_check_count"] or 0),
            "freshnessStatus": row["freshness_status"],
            "freshnessUpdateCount": int(row["freshness_update_count"] or 0),
            "latestDataDate": row["latest_data_date"],
            "batchSummary": _json_load(row["batch_summary_json"], []),
            "upsertSummary": _json_load(row["upsert_summary_json"], []),
            "driftDiagnosis": _json_load(row["drift_diagnosis_json"], {}),
            "gate": _json_load(row["gate_json"], {}),
            "monthlyBaseline": _json_load(row["monthly_baseline_json"], {}),
            "rollbackStatus": row["rollback_status"],
            "backupPath": row["backup_path"],
            "quarantinePath": row["quarantine_path"],
            "postRollbackGate": _json_load(row["post_rollback_gate_json"], {}),
            "rollbackError": row["rollback_error"],
            "operationId": row["operation_id"],
            "entryPoint": row["entry_point"],
            "stageTimings": _json_load(row["stage_timings_json"], []),
            "cacheState": row["cache_state"],
            "cacheError": row["cache_error"],
            "dataGeneration": _json_load(row["data_generation_json"], {}),
        }
        for row in rows
    ]
