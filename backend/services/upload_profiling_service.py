from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

import database
import rules
from backend.services import upload_preflight_service
from backend.services.dashboard_service import build_dashboard_summary
from backend.services.stability_service import PHASE2B_BASELINE_FILTERS, build_phase2c_stability_gate
from backend.services.upload_rollback_service import handle_core_drift_rollback


def _record_stage(stage_timings: list[dict], label: str, started_at: float) -> None:
    stage_timings.append({"階段": label, "秒數": round(time.perf_counter() - started_at, 2)})


def _table_row_count(db_path: Path, table_name: str) -> int:
    if not db_path.exists():
        return 0
    conn = database.sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
        if not exists:
            return 0
        return int(conn.execute(f'SELECT count(*) FROM "{table_name}"').fetchone()[0])
    finally:
        conn.close()


def _db_counts(db_path: Path) -> dict:
    return {
        "tour_rows": _table_row_count(db_path, "tour_data"),
        "others_rows": _table_row_count(db_path, "others_data"),
    }


@contextmanager
def _optional_lightweight_drift_diagnosis(enabled: bool):
    if not enabled:
        yield
        return
    original = upload_preflight_service.build_upload_drift_diagnosis
    upload_preflight_service.build_upload_drift_diagnosis = lambda *args, **kwargs: {
        "status": "skipped_for_profiling",
        "summaryMessage": "Dry-run profiling skipped detailed drift diagnosis to avoid long row-level diff.",
    }
    try:
        yield
    finally:
        upload_preflight_service.build_upload_drift_diagnosis = original


def _virtual_upload_frames(row_count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = max(1, int(row_count))
    main_rows = []
    tour_rows = []
    for index in range(rows):
        source_id = f"17DRY{index + 1:06d}"
        receipt_id = f"DRY260702{index + 1:06d}"
        amount = 1000.0 + index
        receipt_time = f"2026-07-02 {10 + (index % 8):02d}:{index % 60:02d}:00"
        main_rows.append(
            {
                "來源單據號": source_id,
                "收款單號": receipt_id,
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": amount,
                "收款本幣金額": amount,
                "收款時間": receipt_time,
                "收款操作員": "DRYRUN",
            }
        )
        tour_rows.append(
            {
                "交易號碼": source_id,
                "交易時間": receipt_time,
                "行程天數": "3",
                "數量": "2",
                "幣種": "HKD",
                "應收": amount,
                "已收": amount,
                "團負責人": "DRYRUN",
                "團負責人部門": "測試部門",
                "目的地大類": "測試",
                "一級目的地": "測試",
                "二級目的地": "測試",
                "目的地名稱": "Dry Run",
                "銷售點": "17荃灣綠楊坊分社",
                "銷售員": "DRYRUN",
                "團名稱": "Dry Run Tour",
                "團代號": f"DRY{index + 1:04d}",
            }
        )
    return pd.DataFrame(main_rows), pd.DataFrame(tour_rows)


def run_virtual_upload_profiling_dry_run(
    *,
    row_count: int = 25,
    live_db_path: str | Path | None = None,
    skip_drift_diagnosis: bool = True,
) -> dict:
    """Run upload-like profiling against a disposable DB copy.

    This never writes to the live database path. The dry-run copy is intentionally
    kept on disk for short-term inspection and removed by the OS temp cleanup.
    """

    stage_timings: list[dict] = []
    total_started = time.perf_counter()
    live_path = database.resolve_db_path(live_db_path)
    live_before = _db_counts(live_path)

    tempdir = tempfile.mkdtemp(prefix="nbs_upload_profile_")
    temp_db_path = Path(tempdir) / "dry_run.db"
    database.snapshot_sqlite_database(live_path, temp_db_path)

    started = time.perf_counter()
    main_df, tour_df = _virtual_upload_frames(row_count)
    source_files = ["virtual_main_upload.xlsx", "virtual_tour_upload.xlsx"]
    _record_stage(stage_timings, "建立虛擬上傳資料", started)

    started = time.perf_counter()
    with _optional_lightweight_drift_diagnosis(skip_drift_diagnosis):
        preflight_result = upload_preflight_service.run_upload_preflight(
            main_df,
            ("virtual_tour_upload.xlsx", tour_df),
            [],
            rules.DEFAULT_BRANCH_MAPPING,
            rules.DEFAULT_RULES["EXCLUDE_PREFIXES"],
            rules.DEFAULT_RULES["SALES_REP_LIST"],
            source_files=source_files,
            live_db_path=temp_db_path,
        )
    _record_stage(stage_timings, "Preflight 臨時 DB 與口徑驗收", started)

    new_t_df = preflight_result.get("prepared", {}).get("tour", pd.DataFrame())
    new_o_df = preflight_result.get("prepared", {}).get("others", pd.DataFrame())
    upsert_summary = {}
    if str(preflight_result.get("status") or "drift") == "matched":
        started = time.perf_counter()
        upsert_summary = database.upsert_to_db(new_t_df, new_o_df, db_path=temp_db_path)
        _record_stage(stage_timings, "正式 SQLite upsert（dry-run temp DB）", started)

        started = time.perf_counter()
        dashboard_summary = build_dashboard_summary(dict(PHASE2B_BASELINE_FILTERS), db_path=temp_db_path)
        _record_stage(stage_timings, "Dashboard summary rebuild（dry-run temp DB）", started)

        started = time.perf_counter()
        db_after_tour, db_after_others = database.load_all_data_from_db(db_path=temp_db_path)
        _record_stage(stage_timings, "寫入後 SQLite reload（dry-run temp DB）", started)

        started = time.perf_counter()
        stability_gate = build_phase2c_stability_gate(db_path=temp_db_path)
        _record_stage(stage_timings, "Stability gate 驗證（dry-run temp DB）", started)

        started = time.perf_counter()
        rollback_result = handle_core_drift_rollback(
            stability_gate,
            upsert_summary.get("backup_path") if isinstance(upsert_summary, dict) else None,
            restore_database=lambda backup_path: {"quarantine_path": None, "backup_path": backup_path},
            rebuild_cache=lambda: None,
            build_gate=lambda: build_phase2c_stability_gate(db_path=temp_db_path),
        )
        _record_stage(stage_timings, "Rollback guard（dry-run temp DB）", started)
    else:
        dashboard_summary = {}
        db_after_tour, db_after_others = pd.DataFrame(), pd.DataFrame()
        stability_gate = preflight_result.get("stabilityGate") or {}
        rollback_result = {
            "status": "not_run",
            "rollbackStatus": "not_run",
            "backupPath": None,
            "quarantinePath": None,
            "postRollbackGate": None,
            "rollbackError": None,
        }

    _record_stage(stage_timings, "Upload dry-run total", total_started)
    live_after = _db_counts(live_path)
    temp_after = _db_counts(temp_db_path)

    return {
        "dryRun": True,
        "liveDbPath": str(live_path),
        "tempDbPath": str(temp_db_path),
        "liveDbUnchanged": live_before == live_after,
        "liveBefore": live_before,
        "liveAfter": live_after,
        "tempAfter": temp_after,
        "sourceRows": max(1, int(row_count)),
        "sourceFiles": source_files,
        "driftDiagnosisMode": "skipped" if skip_drift_diagnosis else "full",
        "preflightStatus": str(preflight_result.get("status") or "unknown"),
        "filteredExcludedRows": preflight_result.get("filteredExcludedRows"),
        "writeRows": preflight_result.get("writeRows"),
        "upsertSummary": upsert_summary,
        "preflightStageTimings": preflight_result.get("stageTimings") or [],
        "dashboardStatus": (dashboard_summary.get("stabilityBaseline") or {}).get("status"),
        "stabilityStatus": str(stability_gate.get("status") or "unknown"),
        "formattedActualTotal": stability_gate.get("formattedActualTotal"),
        "rollbackStatus": rollback_result.get("rollbackStatus"),
        "rollbackResult": rollback_result,
        "stageTimings": stage_timings,
        "pid": os.getpid(),
    }
