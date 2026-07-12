from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import database
from backend.services.cache_generation_service import advance_cache_generation
from backend.services.monthly_baseline_service import build_governed_stability_gate
from backend.services.stability_history_service import record_stability_history
from backend.services.upload_lock_service import UploadOperation
from backend.services.upload_preflight_service import run_upload_preflight
from backend.services.upload_rollback_service import handle_core_drift_rollback
from rules import load_business_rules


@dataclass
class UploadExecution:
    response: dict[str, Any]
    anomaly_frame: pd.DataFrame
    entity_audit: dict[str, Any]


def _record(timings: list[dict], label: str, started: float) -> None:
    timings.append({"階段": label, "秒數": round(time.perf_counter() - started, 2)})


def _current_rules(loader: Callable[[], dict]) -> tuple[dict, list[str], list[str]]:
    rules = loader()
    mapping = rules.get("BRANCH_MAPPING", {})
    return (
        dict(mapping) if isinstance(mapping, dict) else {},
        list(rules.get("EXCLUDE_PREFIXES", [])),
        list(rules.get("SALES_REP_LIST", [])),
    )


def _latest_date(*frames: pd.DataFrame) -> str | None:
    candidates: list[pd.Timestamp] = []
    for frame in frames:
        if frame.empty:
            continue
        for column in ("統一日期", "收款時間", "日期"):
            if column in frame.columns:
                dates = pd.to_datetime(frame[column], errors="coerce").dropna()
                if not dates.empty:
                    candidates.append(dates.max())
                    break
    return max(candidates).strftime("%Y-%m-%d") if candidates else None


def execute_upload_operation(
    operation: UploadOperation,
    *,
    main_file,
    tour_file=None,
    other_files=None,
    live_db_path: str | Path,
    preflight_runner: Callable = run_upload_preflight,
    upsert_runner: Callable = database.upsert_to_db,
    load_runner: Callable = database.load_all_data_from_db,
    gate_builder: Callable = build_governed_stability_gate,
    rollback_handler: Callable = handle_core_drift_rollback,
    generation_advancer: Callable = advance_cache_generation,
    history_writer: Callable = record_stability_history,
    rules_loader: Callable = load_business_rules,
    accepted_cache_rebuilder: Callable[[], None] | None = None,
) -> UploadExecution:
    live_path = database.resolve_db_path(live_db_path)
    mapping, excluded, sales_reps = _current_rules(rules_loader)
    preflight = preflight_runner(
        main_file, tour_file, list(other_files or []), mapping, excluded, sales_reps,
        source_files=list(operation.source_files), live_db_path=live_path,
    )
    prepared = preflight.get("prepared") or {}
    anomaly = prepared.get("anm") if isinstance(prepared.get("anm"), pd.DataFrame) else pd.DataFrame()
    entity_audit = prepared.get("entity_audit") if isinstance(prepared.get("entity_audit"), dict) else {}
    base = {
        "operationId": operation.operation_id,
        "entryPoint": operation.entry_point,
        "sourceFiles": list(operation.source_files),
        "preflightReport": {key: value for key, value in preflight.items() if key != "prepared"},
        "monthlyBaseline": (preflight.get("stabilityGate") or {}).get("monthlyBaseline") or {},
        "upsertSummary": None, "stabilityGate": None, "rollbackResult": None,
        "historyRecordId": None, "historyError": None, "writeCommitted": False,
        "cacheState": "unchanged", "cacheError": None, "dataGeneration": {},
        "stageTimings": list(preflight.get("stageTimings") or []),
    }
    if preflight.get("status") != "matched":
        return UploadExecution({**base, "status": "blocked", "message": preflight.get("message") or "上傳預演未通過。"}, anomaly, entity_audit)

    tour = prepared.get("tour") if isinstance(prepared.get("tour"), pd.DataFrame) else pd.DataFrame()
    others = prepared.get("others") if isinstance(prepared.get("others"), pd.DataFrame) else pd.DataFrame()
    if tour.empty and others.empty:
        return UploadExecution({**base, "status": "blocked", "message": "清洗後沒有任何可寫入資料；請檢查來源檔案與匹配規則。"}, anomaly, entity_audit)

    timings: list[dict] = []
    started = time.perf_counter()
    upsert = upsert_runner(tour, others, db_path=live_path)
    _record(timings, "正式 SQLite upsert", started)
    started = time.perf_counter()
    after_tour, after_others = load_runner(db_path=live_path)
    latest_data_date = _latest_date(after_tour, after_others)
    _record(timings, "寫入後 SQLite reload", started)
    started = time.perf_counter()
    gate = gate_builder(db_path=live_path)
    _record(timings, "Governed stability gate", started)
    started = time.perf_counter()
    rollback = rollback_handler(
        gate, upsert.get("backup_path"),
        restore_database=lambda backup: database.restore_database_from_backup(backup, live_db_path=live_path),
        rebuild_cache=lambda: None,
        build_gate=lambda: gate_builder(db_path=live_path),
    )
    _record(timings, "Rollback guard", started)
    final_status = str(rollback.get("status") or "rollback_failed")
    generation: dict = {}
    cache_state, cache_error = "unchanged", None
    if final_status in {"accepted", "rejected_rolled_back"}:
        try:
            generation = generation_advancer(db_path=live_path, operation_id=operation.operation_id, status=final_status)
            cache_state = "invalidated"
        except Exception as exc:
            cache_error = f"{type(exc).__name__}: {exc}"
            cache_state = "refresh_required"
    if final_status == "accepted" and cache_error is None and accepted_cache_rebuilder is not None:
        accepted_cache_rebuilder()
        cache_state = "streamlit_rebuilt"

    public_status = "success"
    message = f"上傳批次已寫入；SQLite 最新收款日期：{latest_data_date or '—'}。"
    if final_status == "rejected_rolled_back":
        public_status, message = "error", "本次上傳因 blocking drift 已回滾；正式 SQLite 已恢復 accepted state。"
    elif final_status == "rollback_failed":
        public_status, message = "error", "偵測到 blocking drift，但 rollback 未完成驗證。"
    elif cache_error:
        public_status, message = "degraded", "資料已寫入，但 cache generation 更新失敗；下次載入必須以 DB signature 強制刷新。"

    final_gate = rollback.get("postRollbackGate") or gate
    all_timings = list(preflight.get("stageTimings") or []) + timings
    history_context = {
        "operation_id": operation.operation_id, "entry_point": operation.entry_point,
        "upload_status": final_status, "upload_message": message,
        "source_files": list(operation.source_files), "latest_data_date": latest_data_date,
        "batch_summary": preflight.get("batchSummary") or [], "upsert_summary": upsert,
        "drift_diagnosis": preflight.get("driftDiagnosis") or {},
        "monthly_baseline": final_gate.get("monthlyBaseline") or {},
        "rollback_status": rollback.get("rollbackStatus"), "backup_path": rollback.get("backupPath"),
        "quarantine_path": rollback.get("quarantinePath"), "post_rollback_gate": rollback.get("postRollbackGate"),
        "rollback_error": rollback.get("rollbackError"), "stage_timings": all_timings,
        "cache_state": cache_state, "cache_error": cache_error, "data_generation": generation,
    }
    history_id, history_error = None, None
    try:
        history_id = history_writer(final_gate, history_context, db_path=live_path)
    except Exception as exc:
        history_error = f"{type(exc).__name__}: {exc}"
        if public_status == "success":
            public_status, message = "degraded", "資料已寫入，但 stability history 未完整保存。"
    response = {
        **base, "status": public_status, "message": message, "upsertSummary": upsert,
        "stabilityGate": gate, "monthlyBaseline": final_gate.get("monthlyBaseline") or {},
        "rollbackResult": rollback, "historyRecordId": history_id, "historyError": history_error,
        "writeCommitted": final_status == "accepted", "cacheState": cache_state,
        "cacheError": cache_error, "dataGeneration": generation, "stageTimings": all_timings,
    }
    return UploadExecution(response, anomaly, entity_audit)
