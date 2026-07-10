from __future__ import annotations

import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

import database
from backend.services.drift_diagnosis_service import build_upload_drift_diagnosis
from backend.services.monthly_baseline_service import build_governed_stability_gate
from pipeline import process_raw_files

# Backward-compatible dependency hook used by profiling and tests.
build_phase2c_stability_gate = build_governed_stability_gate


def _money_text(value: float) -> str:
    return f"HKD {float(value):,.0f}"


def _record_stage(stage_timings: list[dict], label: str, started_at: float) -> None:
    stage_timings.append({"階段": label, "秒數": round(time.perf_counter() - started_at, 2)})


def _frame_max_date(frame: pd.DataFrame) -> str | None:
    if frame.empty:
        return None
    for column in ("統一日期", "收款時間", "日期"):
        if column in frame.columns:
            series = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not series.empty:
                return str(series.max().date())
    return None


def _frame_date_series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="datetime64[ns]")
    for column in ("收款時間", "統一日期", "日期"):
        if column in frame.columns:
            return pd.to_datetime(frame[column], errors="coerce").dropna()
    return pd.Series(dtype="datetime64[ns]")


def _batch_summary_row(label: str, frame: pd.DataFrame, target_date: str = "2026-06-15") -> dict:
    dates = _frame_date_series(frame)
    normalized_dates = dates.dt.normalize() if not dates.empty else pd.Series(dtype="datetime64[ns]")
    amount = float(pd.to_numeric(frame.get("收款原幣金額", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not frame.empty else 0.0
    latest_date = str(dates.max().date()) if not dates.empty else None
    return {
        "資料表": label,
        "清洗後行數": int(len(frame)),
        "清洗後金額": _money_text(amount),
        "最新日期": latest_date,
        "最早收款時間": str(dates.min()) if not dates.empty else None,
        "最晚收款時間": str(dates.max()) if not dates.empty else None,
        "金額合計": round(amount, 2),
        f"包含 {target_date}": bool((normalized_dates == pd.Timestamp(target_date)).any()) if not normalized_dates.empty else False,
    }


def _combined_max_date(*frames: pd.DataFrame) -> str | None:
    dates: list[pd.Series] = []
    for frame in frames:
        for column in ("統一日期", "收款時間", "日期"):
            if not frame.empty and column in frame.columns:
                series = pd.to_datetime(frame[column], errors="coerce").dropna()
                if not series.empty:
                    dates.append(series)
                    break
    if not dates:
        return None
    return str(pd.concat(dates, ignore_index=True).max().date())


def _table_row_count(path: Path, table_name: str) -> int:
    if not path.exists():
        return 0
    conn = database.sqlite3.connect(path)
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]) if _table_exists(conn, table_name) else 0
    finally:
        conn.close()


def _table_exists(conn, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
        == 1
    )


@contextmanager
def _temporary_database_path(temp_db_path: Path):
    original = database.DB_FILE
    database.DB_FILE = str(temp_db_path)
    try:
        yield
    finally:
        database.DB_FILE = original


def _snapshot_live_database(temp_db_path: Path) -> None:
    live_path = Path(database.DB_FILE)
    if live_path.exists():
        shutil.copy2(live_path, temp_db_path)
    else:
        temp_db_path.touch()


def run_upload_preflight(
    main_file,
    tour_file,
    other_files,
    branch_mapping: dict,
    exclude_prefixes: list[str],
    sales_reps: list[str],
    *,
    source_files: list[str] | None = None,
) -> dict:
    stage_timings: list[dict] = []
    preflight_started = time.perf_counter()
    source_files = source_files or [
        item.name
        for item in [main_file, tour_file, *(other_files or [])]
        if item is not None and getattr(item, "name", None)
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_db_path = Path(tmpdir) / "preflight.db"
        stage_started = time.perf_counter()
        _snapshot_live_database(temp_db_path)
        _record_stage(stage_timings, "建立 Preflight 臨時 DB", stage_started)
        stage_started = time.perf_counter()
        live_tour_before, live_others_before = database.load_all_data_from_db()
        _record_stage(stage_timings, "讀取正式 SQLite 快照", stage_started)
        stage_started = time.perf_counter()
        live_before = {
            "tour_rows": _table_row_count(Path(database.DB_FILE), "tour_data"),
            "others_rows": _table_row_count(Path(database.DB_FILE), "others_data"),
        }
        _record_stage(stage_timings, "統計正式 DB 行數", stage_started)
        with _temporary_database_path(temp_db_path):
            stage_started = time.perf_counter()
            new_t_df, new_o_df, anm_df, entity_audit = process_raw_files(
                main_file,
                tour_file,
                other_files or [],
                branch_mapping,
                exclude_prefixes,
                sales_reps,
                return_entity_audit=True,
            )
            _record_stage(stage_timings, "清洗與 Entity Resolution", stage_started)
            stage_started = time.perf_counter()
            upsert_summary = database.upsert_to_db(new_t_df, new_o_df)
            _record_stage(stage_timings, "臨時 SQLite upsert", stage_started)
            stage_started = time.perf_counter()
            stability_gate = build_phase2c_stability_gate()
            _record_stage(stage_timings, "Preflight stability gate", stage_started)
            stage_started = time.perf_counter()
            temp_tour_after, temp_others_after = database.load_all_data_from_db()
            _record_stage(stage_timings, "讀取臨時 DB 結果", stage_started)
            stage_started = time.perf_counter()
            drift_diagnosis = build_upload_drift_diagnosis(
                live_tour_before,
                live_others_before,
                temp_tour_after,
                temp_others_after,
                stability_gate=stability_gate,
            )
            _record_stage(stage_timings, "Drift diagnosis", stage_started)
            stage_started = time.perf_counter()
            latest_data_date = _combined_max_date(temp_tour_after, temp_others_after)
            _record_stage(stage_timings, "Preflight 最新日期彙總", stage_started)
        stage_started = time.perf_counter()
        live_after = {
            "tour_rows": _table_row_count(Path(database.DB_FILE), "tour_data"),
            "others_rows": _table_row_count(Path(database.DB_FILE), "others_data"),
        }
        _record_stage(stage_timings, "確認正式 DB 未變更", stage_started)

    stage_started = time.perf_counter()
    total_filtered = sum(int(upsert_summary.get(key, {}).get("filtered_excluded_rows", 0)) for key in ("tour_data", "others_data"))
    total_write_rows = sum(int(upsert_summary.get(key, {}).get("write_rows", 0)) for key in ("tour_data", "others_data"))
    batch_summary = [
        _batch_summary_row("旅行團", new_t_df),
        _batch_summary_row("其他業務", new_o_df),
    ]
    _record_stage(stage_timings, "Preflight batch summary", stage_started)
    _record_stage(stage_timings, "Preflight total", preflight_started)
    drift_checks = stability_gate.get("driftChecks") or []
    status = str(stability_gate.get("status") or "drift")
    message = (
        "上傳預演通過：正式 SQLite 不會漂移，才會進入正式寫入。"
        if status == "matched"
        else "上傳預演發現核心口徑漂移，正式 SQLite 不會寫入。"
    )
    return {
        "status": status,
        "message": message,
        "sourceFiles": source_files,
        "filteredExcludedRows": total_filtered,
        "writeRows": total_write_rows,
        "latestDataDate": latest_data_date,
        "formattedActualTotal": stability_gate.get("formattedActualTotal"),
        "deltaAmount": stability_gate.get("deltaAmount"),
        "driftChecks": drift_checks,
        "driftDiagnosis": drift_diagnosis,
        "batchSummary": batch_summary,
        "stageTimings": stage_timings,
        "upsertSummary": upsert_summary,
        "stabilityGate": stability_gate,
        "prepared": {
            "tour": new_t_df,
            "others": new_o_df,
            "anm": anm_df,
            "entity_audit": entity_audit,
        },
        "liveDbUnchanged": live_before == live_after,
    }
