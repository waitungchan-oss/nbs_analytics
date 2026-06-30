from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd

import database
from backend.services.diagnostics_service import default_environment_payload
from backend.services.operational_monitor_service import compact_health_payload
from backend.services.stability_history_service import record_stability_history
from backend.services.stability_service import build_phase2c_stability_gate
from backend.services.system_health_service import build_system_health
from backend.services.upload_preflight_service import run_upload_preflight
from backend.services.upload_rollback_service import handle_core_drift_rollback
from rules import load_business_rules

UPLOAD_OPERATION_LOCK = Lock()


class NamedBytesIO(BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def _wrap_named_bytes(data: bytes, name: str) -> NamedBytesIO:
    return NamedBytesIO(data, name)


def _current_rules() -> tuple[dict, list[str], list[str], list[str]]:
    rules = load_business_rules()
    branch_mapping = rules.get("BRANCH_MAPPING", {})
    if not isinstance(branch_mapping, dict):
        branch_mapping = {}
    return (
        {str(key).strip().upper(): str(value).strip() for key, value in branch_mapping.items() if str(key).strip() and str(value).strip()},
        list(rules.get("TARGET_BRANCHES_S3", [])),
        list(rules.get("EXCLUDE_PREFIXES", [])),
        list(rules.get("SALES_REP_LIST", [])),
    )


def _latest_data_date_from_db() -> str | None:
    db_tour, db_others = database.load_all_data_from_db()
    candidates: list[str] = []
    for frame in (db_tour, db_others):
        if frame.empty:
            continue
        for column in ("統一日期", "收款時間", "日期"):
            if column in frame.columns:
                series = pd.to_datetime(frame[column], errors="coerce").dropna()
                if not series.empty:
                    candidates.append(str(series.max().date()))
                    break
    return max(candidates) if candidates else None


def _compact_preflight_report(report: dict[str, Any]) -> dict[str, Any]:
    compact = dict(report)
    compact.pop("prepared", None)
    return compact


def _frame_to_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.fillna("").to_dict(orient="records")
    return []


def _compact_entity_audit(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "summary": _frame_to_records(value.get("summary")),
        "source_breakdown": _frame_to_records(value.get("source_breakdown")),
        "duplicate_detail": _frame_to_records(value.get("duplicate_detail")),
        "unmatched_detail": _frame_to_records(value.get("unmatched_detail")),
        "id_cleaning_samples": _frame_to_records(value.get("id_cleaning_samples")),
    }


async def run_vue_upload_action(
    *,
    main_file,
    tour_file=None,
    other_files=None,
) -> dict[str, Any]:
    branch_mapping, _, exclude_prefixes, sales_reps = _current_rules()
    other_files = other_files or []

    main_bytes = await main_file.read()
    tour_bytes = await tour_file.read() if tour_file is not None else None
    other_payloads = []
    for item in other_files:
        other_payloads.append((item.filename or "other.xlsx", await item.read()))

    main_name = main_file.filename or "main.xlsx"
    tour_name = tour_file.filename if tour_file is not None and tour_file.filename else "tour.xlsx"
    source_files = [main_name]
    if tour_file is not None:
        source_files.append(tour_name)
    source_files.extend(name for name, _ in other_payloads)

    main_upload = _wrap_named_bytes(main_bytes, main_name)
    tour_upload = _wrap_named_bytes(tour_bytes, tour_name) if tour_bytes is not None else None
    other_uploads = [_wrap_named_bytes(payload, name) for name, payload in other_payloads]

    with UPLOAD_OPERATION_LOCK:
        preflight_result = run_upload_preflight(
            main_upload,
            tour_upload,
            other_uploads,
            branch_mapping,
            exclude_prefixes,
            sales_reps,
            source_files=source_files,
        )

        preflight_status = str(preflight_result.get("status") or "drift")
        entity_audit = preflight_result.get("prepared", {}).get("entity_audit", {})
        compact_preflight = _compact_preflight_report(preflight_result)
        if preflight_status != "matched":
            return {
                "status": "blocked",
                "message": preflight_result.get("message", "上傳預演發現核心口徑漂移，正式 SQLite 不會寫入。"),
            "sourceFiles": source_files,
            "preflightReport": compact_preflight,
                "rollbackResult": None,
                "historyRecordId": None,
                "historyError": None,
                "writeCommitted": False,
                "latestHealth": compact_health_payload(
                    build_system_health(
                        db_path=Path(database.DB_FILE),
                        cache_path=Path(".nbs_runtime_cache"),
                        runtime_dir=Path(".nbs_runtime"),
                    )
                ),
                "entityAudit": _compact_entity_audit(entity_audit),
            }

        new_t_df = preflight_result.get("prepared", {}).get("tour")
        new_o_df = preflight_result.get("prepared", {}).get("others")
        anm_df = preflight_result.get("prepared", {}).get("anm")
        if getattr(new_t_df, "empty", True) and getattr(new_o_df, "empty", True):
            return {
                "status": "blocked",
                "message": "清洗後沒有任何可寫入資料；請檢查來源檔案與匹配規則。",
                "sourceFiles": source_files,
                "preflightReport": compact_preflight,
                "rollbackResult": None,
                "historyRecordId": None,
                "historyError": None,
                "writeCommitted": False,
                "entityAudit": entity_audit,
                "latestHealth": compact_health_payload(
                    build_system_health(
                        db_path=Path(database.DB_FILE),
                        cache_path=Path(".nbs_runtime_cache"),
                        runtime_dir=Path(".nbs_runtime"),
                    )
                ),
                "entityAudit": _compact_entity_audit(entity_audit),
            }

        upsert_summary = database.upsert_to_db(new_t_df, new_o_df)
        db_after_max = _latest_data_date_from_db()
        stability_gate = build_phase2c_stability_gate()
        rollback_result = handle_core_drift_rollback(
            stability_gate,
            upsert_summary.get("backup_path") if isinstance(upsert_summary, dict) else None,
            restore_database=database.restore_database_from_backup,
            rebuild_cache=lambda: None,
            build_gate=build_phase2c_stability_gate,
        )

        status = "success"
        message = f"上傳批次已寫入並重建 dashboard cache；SQLite 最新收款時間：{db_after_max or '—'}。"
        if rollback_result["status"] == "rejected_rolled_back":
            status = "error"
            message = "本次上傳因核心口徑 Drift 已被拒絕；異常資料庫已隔離，正式 SQLite 已回滾，回滾後核心口徑 2/2 matched。"
        elif rollback_result["status"] == "rollback_failed":
            status = "error"
            message = "偵測到核心口徑 Drift，但自動回滾未能完成二次驗證。請停止使用本次更新後數據並查看 Rollback Error。"

        history_record_id = None
        history_error = None
        try:
            history_record_id = record_stability_history(
                stability_gate,
                {
                    "upload_status": rollback_result.get("status", status),
                    "upload_message": message,
                    "source_files": source_files,
                    "latest_data_date": db_after_max,
                    "batch_summary": preflight_result.get("batchSummary") or [],
                    "upsert_summary": upsert_summary,
                    "drift_diagnosis": preflight_result.get("driftDiagnosis") or {},
                    "rollback_status": rollback_result.get("rollbackStatus"),
                    "backup_path": rollback_result.get("backupPath"),
                    "quarantine_path": rollback_result.get("quarantinePath"),
                    "post_rollback_gate": rollback_result.get("postRollbackGate"),
                    "rollback_error": rollback_result.get("rollbackError"),
                },
            )
        except Exception as exc:
            history_error = f"{type(exc).__name__}: {exc}"

        latest_health = build_system_health(
            db_path=Path(database.DB_FILE),
            cache_path=Path(".nbs_runtime_cache"),
            runtime_dir=Path(".nbs_runtime"),
        )
        return {
            "status": status,
            "message": message,
            "sourceFiles": source_files,
            "preflightReport": compact_preflight,
            "upsertSummary": upsert_summary,
            "stabilityGate": stability_gate,
            "rollbackResult": rollback_result,
            "historyRecordId": history_record_id,
            "historyError": history_error,
            "writeCommitted": True,
            "latestHealth": compact_health_payload(latest_health),
            "entityAudit": _compact_entity_audit(entity_audit),
            "anmRowCount": int(len(anm_df)) if anm_df is not None else 0,
            "environment": default_environment_payload(),
        }
