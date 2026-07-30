from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import pickle
import time
import traceback
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import config as config_module  # noqa: E402
import database as database_module  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import visuals as visuals_module  # noqa: E402
import forecasting as forecasting_module  # noqa: E402
from streamlit_rendering import _render_sidebar_navigation  # noqa: E402
from backend.services.monthly_baseline_service import (  # noqa: E402
    build_monthly_baseline_governance,
    build_governed_stability_gate as build_phase2c_stability_gate,
    evaluate_monthly_baselines,
    list_monthly_baseline_promotions,
    promote_monthly_baselines,
)
from backend.services.dashboard_analytics_service import build_analytics_from_facts  # noqa: E402
from backend.services.dashboard_facts_service import build_dashboard_facts  # noqa: E402
from backend.services.stability_history_service import record_stability_history  # noqa: E402
from backend.services.upload_preflight_service import run_upload_preflight  # noqa: E402
from backend.services.upload_rollback_service import handle_core_drift_rollback  # noqa: E402
from backend.services.cache_generation_service import (  # noqa: E402
    load_cache_generation,
    refresh_cache_generation_signature,
)

config_module = importlib.reload(config_module)
database_module = importlib.reload(database_module)
pipeline_module = importlib.reload(pipeline_module)
visuals_module = importlib.reload(visuals_module)
forecasting_module = importlib.reload(forecasting_module)

COL_BRANCH = config_module.COL_BRANCH
COL_DATE = config_module.COL_DATE
COL_MONEY = config_module.COL_MONEY
COL_ORDER_ID = config_module.COL_ORDER_ID
COL_QTY = config_module.COL_QTY
COL_SALESPERSON = config_module.COL_SALESPERSON
COL_TRANS_TIME = config_module.COL_TRANS_TIME
CONFIG_FILE = config_module.CONFIG_FILE
DB_FILE = config_module.DB_FILE
SESSION_RULE_KEYS = config_module.SESSION_RULE_KEYS
TARGET_DEPT_FOR_REP = config_module.TARGET_DEPT_FOR_REP
init_session_state_config = config_module.init_session_state_config
save_business_rules = config_module.save_business_rules

clear_database = database_module.clear_database
load_all_data_from_db = database_module.load_all_data_from_db
repair_operator_sales_rep_assignments = database_module.repair_operator_sales_rep_assignments
repair_subtable_branch_assignments = database_module.repair_subtable_branch_assignments
restore_database_from_backup = database_module.restore_database_from_backup
upsert_to_db = database_module.upsert_to_db

HAS_AI_LIBS = forecasting_module.HAS_AI_LIBS
build_macro_forecast_summary = forecasting_module.build_macro_forecast_summary
run_ai_backtest_report = forecasting_module.run_ai_backtest_report
run_ai_macro_backtest_report = forecasting_module.run_ai_macro_backtest_report
run_ai_prediction_tracks = forecasting_module.run_ai_prediction_tracks

build_dashboard_data = pipeline_module.build_dashboard_data
build_dashboard_data_excluding_receipt_types = pipeline_module.build_dashboard_data_excluding_receipt_types
clean_invoice_number = pipeline_module.clean_invoice_number
map_dest_category = pipeline_module.map_dest_category
map_ticket_category = pipeline_module.map_ticket_category
normalize_runtime_columns = pipeline_module.normalize_runtime_columns

HAS_MATPLOTLIB = visuals_module.HAS_MATPLOTLIB
draw_forecast_chart = visuals_module.draw_forecast_chart
draw_month_end_macro_chart = visuals_module.draw_month_end_macro_chart
draw_seven_day_macro_chart = visuals_module.draw_seven_day_macro_chart
draw_top10_barh = visuals_module.draw_top10_barh
safe_draw_pie = visuals_module.safe_draw_pie

init_session_state_config()
st.session_state.setdefault('PROCESSED_DATA_CACHE', None)
st.session_state.setdefault('DB_LOADED_FLAG', False)
st.session_state.setdefault('NBS_UI_THEME', 'light')
if st.session_state.get('OPERATOR_REPAIR_RULE_VERSION') != 1:
    st.session_state['PROCESSED_DATA_CACHE'] = None
    st.session_state['DB_LOADED_FLAG'] = False
    st.session_state['OPERATOR_REPAIR_RULE_VERSION'] = 1
if st.session_state.get('SUBTABLE_BRANCH_REPAIR_RULE_VERSION') != 1:
    st.session_state['PROCESSED_DATA_CACHE'] = None
    st.session_state['DB_LOADED_FLAG'] = False
    st.session_state['SUBTABLE_BRANCH_REPAIR_RULE_VERSION'] = 1
if st.session_state.get('EXPORT_RULE_VERSION') != 5:
    st.session_state['PROCESSED_DATA_CACHE'] = None
    st.session_state['DB_LOADED_FLAG'] = False
    st.session_state['EXPORT_RULE_VERSION'] = 5
if st.session_state.get('REVENUE_SCOPE_RULE_VERSION') != 1:
    st.session_state['PROCESSED_DATA_CACHE'] = None
    st.session_state['DB_LOADED_FLAG'] = False
    st.session_state['REVENUE_SCOPE_RULE_VERSION'] = 1
if st.session_state.get('FORECAST_STRATEGY_RULE_VERSION') != 5:
    st.session_state['PROCESSED_DATA_CACHE'] = None
    st.session_state['DB_LOADED_FLAG'] = False
    st.session_state['FORECAST_STRATEGY_RULE_VERSION'] = 5

REVENUE_SCOPE_LABEL = '不含掛賬核銷與TT退款轉團款'
REVENUE_SCOPE_CAPTION = '收入口徑：不含收款類型「掛賬核銷」；不含收款方式「TT 退款轉團款」。'
REVENUE_SCOPE_EXCLUDED_RECEIPT_TYPES = ('掛賬核銷',)
REVENUE_SCOPE_EXCLUDED_PAYMENT_METHODS = ('TT 退款轉團款',)
AI_CACHE_VERSION = 'daily-macro-normal-tight-v1'
EXPORT_CACHE_VERSION = 'export-lazy-v3'
OFFICIAL_EXPORT_SCHEMA_CONTRACT = 'official-branch-salesperson-v1'
AI_CACHE_DIR = Path(__file__).resolve().parent / '.nbs_runtime_cache'
PERSISTENT_REPAIR_STATE_PATH = Path(__file__).resolve().parent / '.nbs_runtime' / 'persistent_repair_state.json'


def _evaluate_monthly_baselines_for_runtime() -> dict:
    cache = st.session_state.get("PROCESSED_DATA_CACHE")
    if cache and isinstance(cache.get("s1"), pd.DataFrame) and isinstance(cache.get("s2"), pd.DataFrame):
        def builder(filters: dict) -> dict:
            return {
                "revenueScope": REVENUE_SCOPE_LABEL,
                **build_analytics_from_facts(cache["s1"], cache["s2"], filters),
            }

        return evaluate_monthly_baselines(analytics_builder=builder)
    return evaluate_monthly_baselines()
AI_CLEANING_RULE_TYPES = ("BRANCH_MAPPING", "EXCLUDE_PREFIXES", "SALES_REP_LIST", "CRUISE_DEPTS")
AI_CLEANING_BRANCH_PLACEHOLDER = "待填分社名稱"


def _money_text(value: float) -> str:
    return f"HKD {value:,.0f}"


def _current_rules() -> tuple[dict, list[str], list[str], list[str], list[str]]:
    return (
        st.session_state["BRANCH_MAPPING"],
        st.session_state["TARGET_BRANCHES_S3"],
        st.session_state["CRUISE_DEPTS"],
        st.session_state["SALES_REP_LIST"],
        st.session_state["EXCLUDE_PREFIXES"],
    )

def _sum_money(df: pd.DataFrame) -> float:
    if df.empty or COL_MONEY not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[COL_MONEY], errors="coerce").fillna(0).sum())

def _date_bounds(df: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if df.empty or COL_DATE not in df.columns:
        return None, None
    dates = pd.to_datetime(df[COL_DATE], errors="coerce").dropna()
    if dates.empty:
        return None, None
    return pd.Timestamp(dates.min()), pd.Timestamp(dates.max())

def _fmt_date(value) -> str:
    if value is None or pd.isna(value):
        return ""
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)

def _upload_batch_summary(label: str, df: pd.DataFrame) -> dict:
    min_date, max_date = _date_bounds(df)
    order_ids = 0
    if not df.empty and COL_ORDER_ID in df.columns:
        order_ids = int(df[COL_ORDER_ID].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())
    normalized_dates = pd.to_datetime(df[COL_DATE], errors="coerce").dt.normalize() if (not df.empty and COL_DATE in df.columns) else pd.Series(dtype="datetime64[ns]")
    return {
        "資料表": label,
        "清洗後行數": int(len(df)),
        "來源單據號數": order_ids,
        "最早收款時間": _fmt_date(min_date),
        "最晚收款時間": _fmt_date(max_date),
        "金額合計": round(_sum_money(df), 2),
        "包含 2026-06-15": bool((normalized_dates == pd.Timestamp("2026-06-15")).any()) if not normalized_dates.empty else False,
    }

def _reset_uploaded_file(file_obj) -> None:
    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
    except Exception:
        pass

def _parse_upload_dates(df: pd.DataFrame, date_col: str) -> pd.Series:
    if df.empty or date_col not in df.columns:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(df[date_col], errors="coerce", utc=True).dt.tz_convert(None)

def _uploaded_excel_frame(file_obj) -> pd.DataFrame:
    _reset_uploaded_file(file_obj)
    try:
        return pd.read_excel(file_obj, dtype=str)
    finally:
        _reset_uploaded_file(file_obj)

def _upload_date_source_diagnostics_from_frames(
    main_name: str,
    main_df: pd.DataFrame,
    secondary_frames: list[tuple[str, str, pd.DataFrame]],
    target_date: str = "2026-06-15",
) -> dict:
    target = pd.Timestamp(target_date)
    rows: list[dict] = []

    main_dates = _parse_upload_dates(main_df, COL_DATE)
    main_target_rows = int((main_dates.dt.normalize() == target).sum()) if not main_dates.empty else 0
    main_max = main_dates.max() if not main_dates.empty and main_dates.notna().any() else None
    rows.append(
        {
            "資料來源": "營收主表",
            "檔名": main_name,
            "日期欄位": COL_DATE,
            "行數": int(len(main_df)),
            f"{target_date} 行數": main_target_rows,
            "最大日期": _fmt_date(main_max),
            "金額合計": round(
                float(pd.to_numeric(main_df.get(COL_MONEY, pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
                2,
            ),
        }
    )

    secondary_max_values: list[pd.Timestamp] = []
    secondary_target_rows = 0
    for label, source_name, df in secondary_frames:
        dates = _parse_upload_dates(df, COL_TRANS_TIME)
        target_rows = int((dates.dt.normalize() == target).sum()) if not dates.empty else 0
        max_date = dates.max() if not dates.empty and dates.notna().any() else None
        if max_date is not None and not pd.isna(max_date):
            secondary_max_values.append(pd.Timestamp(max_date))
        secondary_target_rows += target_rows
        rows.append(
            {
                "資料來源": label,
                "檔名": source_name,
                "日期欄位": COL_TRANS_TIME,
                "行數": int(len(df)),
                f"{target_date} 行數": target_rows,
                "最大日期": _fmt_date(max_date),
                "金額合計": "",
            }
        )

    secondary_max = max(secondary_max_values) if secondary_max_values else None
    mismatch = bool(main_target_rows == 0 and secondary_target_rows > 0)
    warning = ""
    if mismatch:
        warning = (
            f"副表有 {target_date} 交易，但營收主表沒有 {target_date} 收款；"
            "正式營收看板與報表以主表收款時間為準，因此不會新增該日收入。"
        )

    return {
        "rows": rows,
        "main_revenue_max_date": _fmt_date(main_max),
        "main_revenue_target_date_rows": main_target_rows,
        "secondary_transaction_max_date": _fmt_date(secondary_max),
        "secondary_transaction_target_date_rows": secondary_target_rows,
        "date_mismatch_warning": warning,
        "target_date": target_date,
    }

def _upload_date_source_diagnostics(
    main_file,
    tour_file,
    other_files,
    target_date: str = "2026-06-15",
) -> dict:
    main_df = _uploaded_excel_frame(main_file)
    secondary_frames: list[tuple[str, str, pd.DataFrame]] = []
    if tour_file is not None:
        secondary_frames.append(("旅行團副表", getattr(tour_file, "name", str(tour_file)), _uploaded_excel_frame(tour_file)))
    for file_obj in other_files or []:
        secondary_frames.append(("其他業務副表", getattr(file_obj, "name", str(file_obj)), _uploaded_excel_frame(file_obj)))

    for file_obj in [main_file, tour_file, *(other_files or [])]:
        if file_obj is not None:
            _reset_uploaded_file(file_obj)

    return _upload_date_source_diagnostics_from_frames(
        getattr(main_file, "name", str(main_file)),
        main_df,
        secondary_frames,
        target_date=target_date,
    )

def _combined_max_date(*frames: pd.DataFrame) -> pd.Timestamp | None:
    max_values = [max_date for _, max_date in (_date_bounds(frame) for frame in frames) if max_date is not None and not pd.isna(max_date)]
    if not max_values:
        return None
    return max(max_values)

def _build_upload_stability_gate_workbook(gate: dict) -> bytes:
    baseline = gate.get("stabilityBaseline") or {}
    core_validation = gate.get("coreValidation") or baseline.get("coreValidation") or {}
    freshness_update = gate.get("freshnessUpdate") or baseline.get("freshnessUpdate") or {}
    summary_rows = [
        {"項目": "Gate", "值": gate.get("label", "Phase 2C Upload Rebuild Stability Gate")},
        {"項目": "Core Validation Status", "值": gate.get("status", "unknown")},
        {"項目": "Freshness Status", "值": gate.get("freshnessStatus", "unknown")},
        {"項目": "Message", "值": gate.get("message", "")},
        {"項目": "Baseline Month", "值": gate.get("baselineMonth", "")},
        {"項目": "Expected Total", "值": gate.get("formattedExpectedTotal", "")},
        {"項目": "Actual Total", "值": gate.get("formattedActualTotal", "")},
        {"項目": "Delta Amount", "值": gate.get("deltaAmount", 0)},
        {"項目": "Delta Percent", "值": gate.get("deltaPct", 0)},
        {"項目": "Core Matched Checks", "值": gate.get("matchedChecks", 0)},
        {"項目": "Core Total Checks", "值": gate.get("totalChecks", 0)},
        {"項目": "Freshness Updated Checks", "值": gate.get("freshnessUpdateCount", 0)},
    ]
    checks = baseline.get("checks") or []
    drift_checks = gate.get("driftChecks") or []
    core_checks = core_validation.get("checks") or []
    freshness_checks = freshness_update.get("checks") or []

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Gate Summary", index=False)
        pd.DataFrame(core_checks).to_excel(writer, sheet_name="Core Validation", index=False)
        pd.DataFrame(freshness_checks).to_excel(writer, sheet_name="Freshness Update", index=False)
        pd.DataFrame(checks).to_excel(writer, sheet_name="Gate Checks", index=False)
        pd.DataFrame(drift_checks).to_excel(writer, sheet_name="Core Drift Checks", index=False)
    buffer.seek(0)
    return buffer.getvalue()

def _build_drift_diagnosis_workbook(diagnosis: dict) -> bytes:
    summary_rows = [
        {"項目": "Status", "值": diagnosis.get("status", "unknown")},
        {"項目": "Baseline Month", "值": diagnosis.get("baselineMonth", "")},
        {"項目": "Expected Total", "值": diagnosis.get("expectedTotal", 0)},
        {"項目": "Actual Total", "值": diagnosis.get("actualTotal", 0)},
        {"項目": "Delta Amount", "值": diagnosis.get("deltaAmount", 0)},
        {"項目": "Summary Message", "值": diagnosis.get("summaryMessage", "")},
        {"項目": "Row Limit", "值": diagnosis.get("rowLimit", 0)},
    ]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Drift Summary", index=False)
        pd.DataFrame(diagnosis.get("topDrivers") or []).to_excel(writer, sheet_name="Top Drivers", index=False)
        pd.DataFrame(diagnosis.get("sourceOrderDiffs") or []).to_excel(writer, sheet_name="Source Order Diffs", index=False)
        pd.DataFrame(diagnosis.get("receiptDiffs") or []).to_excel(writer, sheet_name="Receipt Diffs", index=False)
        pd.DataFrame(diagnosis.get("excludedReceiptDiffs") or []).to_excel(writer, sheet_name="Excluded Receipt Diffs", index=False)
    buffer.seek(0)
    return buffer.getvalue()

def _upsert_summary_rows(summary: dict) -> list[dict]:
    rows: list[dict] = []
    for table_key, label in (("tour_data", "旅行團"), ("others_data", "其他業務")):
        item = summary.get(table_key, {}) if isinstance(summary, dict) else {}
        before = item.get("before", {}) or {}
        after = item.get("after", {}) or {}
        rows.append(
            {
                "資料表": label,
                "寫入前行數": before.get("rows", 0),
                "寫入後行數": after.get("rows", 0),
                "寫入前最大日期": _fmt_date(before.get("max_date")),
                "寫入後最大日期": _fmt_date(after.get("max_date")),
                "本次輸入行數": item.get("input_rows", 0),
                "口徑排除行數": item.get("filtered_excluded_rows", 0),
                "實際寫入行數": item.get("write_rows", item.get("inserted_rows", 0)),
                "重覆來源單據號數": item.get("overlap_order_ids", 0),
                "刪除舊行數": item.get("deleted_rows", 0),
                "追加行數": item.get("inserted_rows", 0),
            }
        )
    return rows

def _strategy_by_horizon_from_backtest(report: dict | None) -> dict[int, str]:
    if not report:
        return {}
    weights_df = report.get("weights", pd.DataFrame())
    if weights_df.empty or "推薦策略" not in weights_df.columns:
        return {}
    result: dict[int, str] = {}
    for _, row in weights_df.iterrows():
        version = str(row.get("權重版本", ""))
        horizon_text = version.split(" ")[0].strip()
        try:
            horizon = int(horizon_text)
        except ValueError:
            continue
        strategy = str(row.get("推薦策略", "")).strip()
        if strategy:
            result[horizon] = strategy
    return result

def _model_health_label(wape: float | int | None) -> str:
    if wape is None or pd.isna(wape):
        return "未評估"
    value = float(wape)
    if value < 10:
        return "優秀"
    if value < 20:
        return "可接受"
    if value < 30:
        return "可參考"
    return "需謹慎"

def _add_health_column(df: pd.DataFrame, metric_col: str = "WAPE", column_name: str = "模型健康燈號") -> pd.DataFrame:
    if df.empty or metric_col not in df.columns:
        return df.copy()
    result = df.copy()
    result[column_name] = result[metric_col].apply(_model_health_label)
    return result

def _quality_health_label(score: float | int | None) -> str:
    if score is None or pd.isna(score):
        return "不適用"
    value = float(score)
    if value >= 90:
        return "優秀"
    if value >= 75:
        return "可接受"
    if value >= 60:
        return "需關注"
    return "需處理"

def _safe_rate(numerator: float | int, denominator: float | int) -> float:
    denominator = float(denominator or 0)
    if denominator <= 0:
        return 0.0
    return float(numerator or 0) / denominator

def _quality_score(value: float | int | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(max(0.0, min(100.0, float(value))), 2)

def _combine_quality_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    available = [normalize_runtime_columns(df.copy()) for df in frames if isinstance(df, pd.DataFrame) and not df.empty]
    if not available:
        return pd.DataFrame()
    return pd.concat(available, ignore_index=True, sort=False)

def _quality_date_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    total_rows = len(df)
    if df.empty or COL_DATE not in df.columns:
        summary = pd.DataFrame(
            [
                {
                    "指標": "收款日期欄位",
                    "數值": "不適用",
                    "說明": f"缺少 {COL_DATE} 欄位，無法評估日期覆蓋。",
                }
            ]
        )
        return summary, {"score": None, "latest_date": "不適用", "missing_days": None, "invalid_rate": None}

    date_series = pd.to_datetime(df[COL_DATE], errors="coerce")
    valid_dates = date_series.dropna()
    invalid_rows = int(total_rows - len(valid_dates))
    invalid_rate = _safe_rate(invalid_rows, total_rows)
    if valid_dates.empty:
        summary = pd.DataFrame(
            [
                {"指標": "有效收款日期", "數值": 0, "說明": "所有收款日期均無法解析。"},
                {"指標": "無效日期比例", "數值": invalid_rate, "說明": "無效日期筆數 / 全部筆數。"},
            ]
        )
        return summary, {"score": 0.0, "latest_date": "不適用", "missing_days": None, "invalid_rate": invalid_rate}

    normalized = valid_dates.dt.normalize()
    min_date = pd.Timestamp(normalized.min())
    max_date = pd.Timestamp(normalized.max())
    expected_days = pd.date_range(min_date, max_date, freq="D")
    actual_days = pd.DatetimeIndex(sorted(normalized.unique()))
    missing_days = expected_days.difference(actual_days)
    missing_rate = _safe_rate(len(missing_days), len(expected_days))
    score = _quality_score(100 - invalid_rate * 80 - missing_rate * 20)
    summary = pd.DataFrame(
        [
            {"指標": "最早收款日期", "數值": min_date.strftime("%Y-%m-%d"), "說明": "正式營收日期來源。"},
            {"指標": "最新收款日期", "數值": max_date.strftime("%Y-%m-%d"), "說明": "SQLite 目前可見的最新營收日期。"},
            {"指標": "有效日期天數", "數值": int(len(actual_days)), "說明": "日期範圍內實際有資料的天數。"},
            {"指標": "缺失日期天數", "數值": int(len(missing_days)), "說明": "最早至最新日期之間沒有營收記錄的日子。"},
            {"指標": "無效日期筆數", "數值": invalid_rows, "說明": "無法解析為日期的收款時間。"},
            {"指標": "無效日期比例", "數值": invalid_rate, "說明": "無效日期筆數 / 全部筆數。"},
        ]
    )
    return summary, {
        "score": score,
        "latest_date": max_date.strftime("%Y-%m-%d"),
        "missing_days": int(len(missing_days)),
        "invalid_rate": invalid_rate,
    }

def _quality_field_completeness(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required_cols = [COL_ORDER_ID, COL_DATE, COL_MONEY, COL_BRANCH, COL_SALESPERSON, COL_TRANS_TIME, "來源報表標籤"]
    rows: list[dict] = []
    scores: list[float] = []
    total_rows = len(df)
    for col in required_cols:
        if df.empty or col not in df.columns:
            rows.append(
                {
                    "欄位": col,
                    "狀態": "不適用",
                    "完整筆數": None,
                    "總筆數": total_rows,
                    "完整率": None,
                    "說明": "此欄位不存在於目前合併資料，Scorecard 不將其視為錯誤。",
                }
            )
            continue
        series = df[col]
        if col == COL_MONEY:
            valid = pd.to_numeric(series, errors="coerce").notna()
        else:
            valid = series.notna() & series.astype(str).str.strip().ne("")
        complete = int(valid.sum())
        rate = _safe_rate(complete, total_rows)
        scores.append(rate * 100)
        rows.append(
            {
                "欄位": col,
                "狀態": "已評估",
                "完整筆數": complete,
                "總筆數": total_rows,
                "完整率": rate,
                "說明": "完整率 = 非空且可用筆數 / 全部筆數。",
            }
        )
    score = _quality_score(sum(scores) / len(scores)) if scores else None
    return pd.DataFrame(rows), {"score": score}

def _quality_entity_resolution(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    total_rows = len(df)
    duplicate_rows = 0
    unique_ids = 0
    if not df.empty and COL_ORDER_ID in df.columns:
        ids = clean_invoice_number(df[COL_ORDER_ID]).replace({"": pd.NA, "NAN": pd.NA}).dropna()
        unique_ids = int(ids.nunique())
        duplicate_rows = int(ids.duplicated(keep=False).sum())
    duplicate_rate = _safe_rate(duplicate_rows, total_rows)

    unmatched_rows = 0
    unmatched_amount = 0.0
    if not df.empty and "來源報表標籤" in df.columns:
        unmatched_mask = df["來源報表標籤"].astype(str).str.strip().eq("未匹配")
        unmatched_rows = int(unmatched_mask.sum())
        unmatched_amount = _sum_money(df.loc[unmatched_mask])
    unmatched_rate = _safe_rate(unmatched_rows, total_rows)
    score = _quality_score(100 - unmatched_rate * 60 - duplicate_rate * 40)
    rows = [
        {"指標": "來源單據號唯一數", "數值": unique_ids, "比例": _safe_rate(unique_ids, total_rows), "說明": "用於跨表匹配與排重的核心 entity。"},
        {"指標": "重複來源單據號筆數", "數值": duplicate_rows, "比例": duplicate_rate, "說明": "同一來源單據號出現多筆；可能正常分攤，也可能需要稽核。"},
        {"指標": "未匹配副表筆數", "數值": unmatched_rows, "比例": unmatched_rate, "說明": "來源報表標籤為未匹配的其他業務資料。"},
        {"指標": "未匹配副表金額", "數值": round(unmatched_amount, 2), "比例": _safe_rate(unmatched_amount, _sum_money(df)), "說明": "未匹配資料涉及的金額占比。"},
    ]
    return pd.DataFrame(rows), {"score": score, "unmatched_rows": unmatched_rows, "duplicate_rows": duplicate_rows}

def _current_entity_resolution_audit(cache: dict) -> dict:
    raw_t = normalize_runtime_columns(cache.get("raw_t", pd.DataFrame()).copy())
    raw_o = normalize_runtime_columns(cache.get("raw_o", pd.DataFrame()).copy())
    if not raw_t.empty:
        raw_t["資料來源"] = "旅行團"
    if not raw_o.empty and "資料來源" not in raw_o.columns:
        raw_o["資料來源"] = raw_o.get("來源報表標籤", "其他業務")
    raw_df = _combine_quality_frames([raw_t, raw_o])

    total_rows = int(len(raw_df))
    ids = clean_invoice_number(raw_df[COL_ORDER_ID]).replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA}).dropna() if COL_ORDER_ID in raw_df.columns else pd.Series(dtype=object)
    duplicate_counts = ids.value_counts()
    duplicate_detail = duplicate_counts[duplicate_counts > 1].reset_index()
    duplicate_detail.columns = ["單號", "出現次數"] if not duplicate_detail.empty else ["單號", "出現次數"]
    duplicate_detail.insert(0, "資料表", "SQLite 已落地資料")

    source_col = "來源報表標籤" if "來源報表標籤" in raw_df.columns else "資料來源"
    source_series = raw_df.get(source_col, pd.Series("", index=raw_df.index)).fillna("").astype(str).str.strip()
    unmatched_mask = source_series.eq("未匹配")
    matched_rows = int(total_rows - unmatched_mask.sum())
    unmatched_rows = int(unmatched_mask.sum())
    match_rate = _safe_rate(matched_rows, total_rows)

    amount_series = pd.to_numeric(raw_df.get(COL_MONEY, 0), errors="coerce").fillna(0)
    work = raw_df.copy()
    work["匹配來源"] = source_series.replace({"": "旅行團/已匹配"})
    work["_audit_amount"] = amount_series
    source_breakdown = (
        work.groupby("匹配來源", dropna=False)
        .agg(
            行數=(COL_ORDER_ID, "size"),
            唯一來源單據號=(COL_ORDER_ID, lambda s: clean_invoice_number(s).replace({"": pd.NA, "NAN": pd.NA}).dropna().nunique()),
            金額合計=("_audit_amount", "sum"),
        )
        .reset_index()
        .sort_values(["行數", "匹配來源"], ascending=[False, True])
    )

    unmatched_cols = [c for c in [COL_ORDER_ID, COL_DATE, COL_MONEY, COL_BRANCH, COL_SALESPERSON, "來源報表標籤", "資料來源"] if c in raw_df.columns]
    unmatched_detail = raw_df.loc[unmatched_mask, unmatched_cols].copy() if unmatched_cols else pd.DataFrame()
    if not unmatched_detail.empty:
        unmatched_detail.insert(0, "方向", "主表有 / 副表無")
        unmatched_detail.insert(1, "處理", "正式營收保留，但副表資訊未補充")

    summary = pd.DataFrame(
        [
            {"指標": "SQLite 已落地行數", "數值": total_rows, "說明": "目前資料庫中可被正式看板讀取的主表收款明細。"},
            {"指標": "唯一來源單據號數", "數值": int(ids.nunique()), "說明": "已清洗來源單據號後的唯一 entity 數。"},
            {"指標": "已匹配 / 已分類行數", "數值": matched_rows, "說明": "不是未匹配標籤的行數。"},
            {"指標": "未匹配行數", "數值": unmatched_rows, "說明": "主表已落地但副表沒有補充產品資訊的行數。"},
            {"指標": "重複單號筆數", "數值": int(ids.duplicated(keep=False).sum()), "說明": "同一來源單據號在 SQLite 已落地資料中出現多筆。"},
            {"指標": "匹配健康率", "數值": round(match_rate, 4), "說明": "非未匹配行數 / SQLite 已落地行數。"},
        ]
    )
    id_samples = pd.DataFrame(
        [{"資料表": "SQLite 已落地資料", "欄位": COL_ORDER_ID, "清洗前": "不適用", "清洗後": "入庫前已標準化", "說明": "目前頁面無法還原上傳原始字串；批次上傳 audit 會展示清洗前後樣本。"}]
    )
    return {
        "summary": summary,
        "source_breakdown": source_breakdown,
        "duplicate_detail": duplicate_detail,
        "unmatched_detail": unmatched_detail,
        "id_cleaning_samples": id_samples,
        "match_rate": match_rate,
        "unmatched_rows": unmatched_rows,
        "secondary_only_rows": None,
        "duplicate_rows": int(ids.duplicated(keep=False).sum()),
    }

def _quality_scope_health(scope_audit: dict) -> tuple[pd.DataFrame, dict]:
    raw_rows = int(scope_audit.get("raw_rows", 0) or 0)
    analysis_rows = int(scope_audit.get("analysis_rows", 0) or 0)
    excluded_rows = int(scope_audit.get("excluded_rows", 0) or 0)
    excluded_orders = int(scope_audit.get("excluded_order_count", 0) or 0)
    raw_amount = float(scope_audit.get("raw_amount", 0) or 0)
    analysis_amount = float(scope_audit.get("analysis_amount", 0) or 0)
    excluded_amount = float(scope_audit.get("excluded_amount", raw_amount - analysis_amount) or 0)
    excluded_row_rate = _safe_rate(excluded_rows, raw_rows)
    excluded_amount_rate = _safe_rate(excluded_amount, raw_amount)
    # Official exclusions are expected business rules, so only very high exclusion impact mildly lowers the health score.
    score = _quality_score(100 - min(excluded_amount_rate / 0.25, 1.0) * 20)
    rows = [
        {"指標": "正式口徑", "數值": REVENUE_SCOPE_LABEL, "比例": None, "說明": REVENUE_SCOPE_CAPTION},
        {"指標": "原始筆數", "數值": raw_rows, "比例": 1.0 if raw_rows else 0.0, "說明": "SQLite 載入後、正式口徑排除前。"},
        {"指標": "正式分析筆數", "數值": analysis_rows, "比例": _safe_rate(analysis_rows, raw_rows), "說明": "正式看板實際使用的筆數。"},
        {"指標": "排除來源單據號數", "數值": excluded_orders, "比例": _safe_rate(excluded_orders, raw_rows), "說明": "掛賬核銷與 TT 退款轉團款相關來源單據號。"},
        {"指標": "排除筆數", "數值": excluded_rows, "比例": excluded_row_rate, "說明": "正式口徑排除的明細筆數。"},
        {"指標": "排除金額", "數值": round(excluded_amount, 2), "比例": excluded_amount_rate, "說明": "正式口徑排除金額占原始金額比例。"},
    ]
    return pd.DataFrame(rows), {"score": score, "excluded_amount_rate": excluded_amount_rate}

def _quality_amount_health(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    total_rows = len(df)
    if df.empty or COL_MONEY not in df.columns:
        rows = [{"指標": "金額欄位", "數值": "不適用", "比例": None, "說明": f"缺少 {COL_MONEY} 欄位。"}]
        return pd.DataFrame(rows), {"score": None}
    amount = pd.to_numeric(df[COL_MONEY], errors="coerce")
    missing_rows = int(amount.isna().sum())
    zero_rows = int(amount.fillna(0).eq(0).sum())
    negative_rows = int(amount.fillna(0).lt(0).sum())
    total_amount = float(amount.fillna(0).sum())
    abs_positive = amount.dropna().abs().sort_values(ascending=False)
    top_5_count = max(1, int(len(abs_positive) * 0.05)) if len(abs_positive) else 0
    top_5_amount = float(abs_positive.head(top_5_count).sum()) if top_5_count else 0.0
    top_5_rate = _safe_rate(top_5_amount, float(abs_positive.sum())) if len(abs_positive) else 0.0
    missing_rate = _safe_rate(missing_rows, total_rows)
    zero_negative_rate = _safe_rate(zero_rows + negative_rows, total_rows)
    concentration_penalty = max(0.0, top_5_rate - 0.6) / 0.4 * 10
    score = _quality_score(100 - missing_rate * 50 - zero_negative_rate * 40 - concentration_penalty)
    rows = [
        {"指標": "金額合計", "數值": round(total_amount, 2), "比例": 1.0 if total_amount else 0.0, "說明": "目前原始資料金額合計。"},
        {"指標": "金額缺失筆數", "數值": missing_rows, "比例": missing_rate, "說明": "無法解析為數值的金額。"},
        {"指標": "零值金額筆數", "數值": zero_rows, "比例": _safe_rate(zero_rows, total_rows), "說明": "金額為 0 的明細。"},
        {"指標": "負值金額筆數", "數值": negative_rows, "比例": _safe_rate(negative_rows, total_rows), "說明": "金額為負的明細，可能是退款或沖銷，需要按業務規則解讀。"},
        {"指標": "Top 5% 金額集中度", "數值": round(top_5_amount, 2), "比例": top_5_rate, "說明": "按絕對金額排序前 5% 明細占比，用來觀察大單集中風險。"},
    ]
    return pd.DataFrame(rows), {"score": score, "top_5_rate": top_5_rate}

def _compute_data_quality_scorecard(cache: dict) -> dict:
    raw_df = _combine_quality_frames([cache.get("raw_t", pd.DataFrame()), cache.get("raw_o", pd.DataFrame())])
    analysis_df = _combine_quality_frames([cache.get("t", pd.DataFrame()), cache.get("o", pd.DataFrame())])
    scope_audit = cache.get("scope", {}) or {}

    date_df, date_meta = _quality_date_summary(raw_df)
    field_df, field_meta = _quality_field_completeness(raw_df)
    entity_df, entity_meta = _quality_entity_resolution(raw_df)
    scope_df, scope_meta = _quality_scope_health(scope_audit)
    amount_df, amount_meta = _quality_amount_health(raw_df)

    dimension_rows = [
        {
            "維度": "Date Coverage",
            "分數": date_meta.get("score"),
            "健康燈號": _quality_health_label(date_meta.get("score")),
            "關鍵指標": f"最新日期 {date_meta.get('latest_date', '不適用')} / 缺失日期 {date_meta.get('missing_days', '不適用')}",
        },
        {
            "維度": "Field Completeness",
            "分數": field_meta.get("score"),
            "健康燈號": _quality_health_label(field_meta.get("score")),
            "關鍵指標": "核心欄位非空與可解析比例",
        },
        {
            "維度": "Entity Resolution",
            "分數": entity_meta.get("score"),
            "健康燈號": _quality_health_label(entity_meta.get("score")),
            "關鍵指標": f"未匹配 {entity_meta.get('unmatched_rows', 0):,} / 重複 {entity_meta.get('duplicate_rows', 0):,}",
        },
        {
            "維度": "Official Scope Health",
            "分數": scope_meta.get("score"),
            "健康燈號": _quality_health_label(scope_meta.get("score")),
            "關鍵指標": f"排除金額占比 {_safe_rate(scope_audit.get('excluded_amount', 0), scope_audit.get('raw_amount', 0)):.2%}",
        },
        {
            "維度": "Amount Health",
            "分數": amount_meta.get("score"),
            "健康燈號": _quality_health_label(amount_meta.get("score")),
            "關鍵指標": f"Top 5% 集中度 {float(amount_meta.get('top_5_rate', 0) or 0):.2%}",
        },
    ]
    dimension_df = pd.DataFrame(dimension_rows)
    valid_scores = pd.to_numeric(dimension_df["分數"], errors="coerce").dropna()
    overall_score = _quality_score(valid_scores.mean()) if not valid_scores.empty else None
    overview_df = pd.DataFrame(
        [
            {"指標": "Overall Data Quality Score", "數值": overall_score, "說明": "五個可評估維度的平均分。"},
            {"指標": "Raw Rows", "數值": int(len(raw_df)), "說明": "SQLite 原始明細合併筆數。"},
            {"指標": "Official Scope Rows", "數值": int(len(analysis_df)), "說明": "正式口徑排除後明細筆數。"},
            {"指標": "Raw Amount", "數值": round(_sum_money(raw_df), 2), "說明": "SQLite 原始明細金額。"},
            {"指標": "Official Scope Amount", "數值": round(_sum_money(analysis_df), 2), "說明": "正式口徑排除後金額。"},
        ]
    )
    return {
        "overall_score": overall_score,
        "overall_health": _quality_health_label(overall_score),
        "latest_date": date_meta.get("latest_date", "不適用"),
        "missing_days": date_meta.get("missing_days"),
        "unmatched_rows": entity_meta.get("unmatched_rows", 0),
        "excluded_amount_rate": scope_meta.get("excluded_amount_rate", 0),
        "overview": overview_df,
        "dimension_summary": dimension_df,
        "field_completeness": field_df,
        "date_coverage": date_df,
        "entity_resolution": entity_df,
        "official_scope": scope_df,
        "amount_health": amount_df,
    }

def _best_macro_metric(report: dict | None, layer: str) -> tuple[float | None, str, str]:
    if not report:
        return None, "未評估", "—"
    summary_df = report.get("summary", pd.DataFrame())
    if summary_df.empty or "聚合層級" not in summary_df.columns or "WAPE" not in summary_df.columns:
        return None, "未評估", "—"
    subset = summary_df[(summary_df["聚合層級"] == layer) & summary_df["WAPE"].notna()].copy()
    if subset.empty:
        return None, "未評估", "—"
    best = subset.sort_values(["WAPE", "MAPE", "策略", "模型"]).iloc[0]
    wape = float(best["WAPE"])
    label = f"{best['策略']} / {best['模型']}"
    return wape, _model_health_label(wape), label

def _best_metric_row(df: pd.DataFrame, metric_col: str = "WAPE") -> pd.Series | None:
    if df.empty or metric_col not in df.columns:
        return None
    ranked = df[df[metric_col].notna()].copy()
    if ranked.empty:
        return None
    sort_cols = [metric_col]
    for col in ("MAPE", "策略", "模型"):
        if col in ranked.columns:
            sort_cols.append(col)
    return ranked.sort_values(sort_cols).iloc[0]

def _collect_revenue_scope_excluded_ids(dfs: list[pd.DataFrame]) -> set[str]:
    excluded_types = {str(v).strip() for v in REVENUE_SCOPE_EXCLUDED_RECEIPT_TYPES if str(v).strip()}
    excluded_methods = {str(v).strip() for v in REVENUE_SCOPE_EXCLUDED_PAYMENT_METHODS if str(v).strip()}
    excluded_ids: set[str] = set()

    for df in dfs:
        if df.empty or COL_ORDER_ID not in df.columns:
            continue
        mask = pd.Series(False, index=df.index)
        if excluded_types and "收款類型" in df.columns:
            mask |= df["收款類型"].astype(str).str.strip().isin(excluded_types)
        if excluded_methods and "收款方式" in df.columns:
            mask |= df["收款方式"].astype(str).str.strip().isin(excluded_methods)
        excluded_ids.update(df.loc[mask, COL_ORDER_ID].astype(str).str.strip())

    return {v for v in excluded_ids if v}

def _drop_revenue_scope_excluded_ids(df: pd.DataFrame, excluded_ids: set[str]) -> pd.DataFrame:
    if df.empty or not excluded_ids or COL_ORDER_ID not in df.columns:
        return df.copy()
    keep_mask = ~df[COL_ORDER_ID].astype(str).str.strip().isin(excluded_ids)
    return df.loc[keep_mask].copy()

def _build_revenue_scope_frames(db_tour: pd.DataFrame, db_others: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw_tour = normalize_runtime_columns(db_tour)
    raw_others = normalize_runtime_columns(db_others)
    excluded_ids = _collect_revenue_scope_excluded_ids([raw_tour, raw_others])
    analysis_tour = _drop_revenue_scope_excluded_ids(raw_tour, excluded_ids)
    analysis_others = _drop_revenue_scope_excluded_ids(raw_others, excluded_ids)
    audit = {
        "scope_label": REVENUE_SCOPE_LABEL,
        "excluded_order_count": len(excluded_ids),
        "raw_rows": len(raw_tour) + len(raw_others),
        "analysis_rows": len(analysis_tour) + len(analysis_others),
        "excluded_rows": (len(raw_tour) + len(raw_others)) - (len(analysis_tour) + len(analysis_others)),
        "raw_amount": _sum_money(raw_tour) + _sum_money(raw_others),
        "analysis_amount": _sum_money(analysis_tour) + _sum_money(analysis_others),
    }
    audit["excluded_amount"] = audit["raw_amount"] - audit["analysis_amount"]
    return analysis_tour, analysis_others, audit

def _file_signature(path_value: str | Path) -> dict:
    path = Path(path_value)
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }

def _file_content_hash(path_value: str | Path) -> str:
    path = Path(path_value)
    if not path.exists():
        return "missing"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return "unreadable"

def _stable_frame_fingerprint(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {"label": label, "rows": 0, "amount": 0.0, "hash": "empty"}

    frame = normalize_runtime_columns(df)
    important_cols = [
        COL_ORDER_ID,
        "統一日期",
        COL_MONEY,
        COL_BRANCH,
        COL_SALESPERSON,
        "收款類型",
        "收款方式",
        "團負責人部門",
        "目的地大類",
        "一級目的地",
        "二級目的地",
        "團名稱",
        "行程天數",
        COL_QTY,
    ]
    cols = [col for col in important_cols if col in frame.columns]
    compact = frame[cols].copy() if cols else pd.DataFrame(index=frame.index)
    for col in compact.columns:
        compact[col] = compact[col].astype(str).fillna("")
    sort_cols = [col for col in (COL_ORDER_ID, "統一日期", COL_MONEY) if col in compact.columns]
    if sort_cols:
        compact = compact.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
    else:
        compact = compact.reset_index(drop=True)
    row_hash = pd.util.hash_pandas_object(compact, index=False).astype("uint64").to_numpy().tobytes()
    date_values = pd.to_datetime(frame["統一日期"], errors="coerce") if "統一日期" in frame.columns else pd.Series(dtype="datetime64[ns]")
    return {
        "label": label,
        "rows": int(len(frame)),
        "amount": round(_sum_money(frame), 2),
        "min_date": "" if date_values.empty or date_values.dropna().empty else str(date_values.min().date()),
        "max_date": "" if date_values.empty or date_values.dropna().empty else str(date_values.max().date()),
        "hash": hashlib.sha256(row_hash).hexdigest(),
    }

def _data_semantic_fingerprint(tour_df: pd.DataFrame, others_df: pd.DataFrame) -> dict:
    return {
        "tour": _stable_frame_fingerprint(tour_df, "tour"),
        "others": _stable_frame_fingerprint(others_df, "others"),
    }

def _legacy_ai_cache_key(scope_audit: dict) -> str:
    payload = {
        "version": AI_CACHE_VERSION,
        "db": _file_signature(DB_FILE),
        "rules": _file_signature(CONFIG_FILE),
        "scope": {
            "label": scope_audit.get("scope_label"),
            "analysis_rows": scope_audit.get("analysis_rows"),
            "analysis_amount": round(float(scope_audit.get("analysis_amount", 0) or 0), 2),
            "excluded_order_count": scope_audit.get("excluded_order_count"),
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]

def _ai_cache_key(scope_audit: dict, analysis_tour: pd.DataFrame, analysis_others: pd.DataFrame) -> str:
    payload = {
        "version": AI_CACHE_VERSION,
        "rules_content_hash": _file_content_hash(CONFIG_FILE),
        "data": _data_semantic_fingerprint(analysis_tour, analysis_others),
        "scope": {
            "label": scope_audit.get("scope_label"),
            "analysis_rows": scope_audit.get("analysis_rows"),
            "analysis_amount": round(float(scope_audit.get("analysis_amount", 0) or 0), 2),
            "excluded_order_count": scope_audit.get("excluded_order_count"),
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]

def _ai_cache_path(cache_key: str) -> Path:
    return AI_CACHE_DIR / f"ai_{cache_key}.pkl"

def _export_cache_path(cache_key: str) -> Path:
    return AI_CACHE_DIR / f"export_{cache_key}.pkl"

def _load_pickle_cache(path: Path, version: str) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            payload = pickle.load(f)
        if payload.get("version") != version:
            return None
        return payload.get("data")
    except Exception:
        return None

def _save_pickle_cache(path: Path, version: str, data: dict) -> bool:
    try:
        AI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({"version": version, "data": data}, f, protocol=pickle.HIGHEST_PROTOCOL)
        return True
    except Exception:
        return False

def _load_ai_runtime_cache(cache_key: str, fallback_key: str | None = None) -> tuple[dict | None, str, str | None]:
    cache_path = _ai_cache_path(cache_key)
    data = _load_pickle_cache(cache_path, AI_CACHE_VERSION)
    if data is not None:
        return data, "hit", cache_key
    if fallback_key and fallback_key != cache_key:
        fallback_path = _ai_cache_path(fallback_key)
        data = _load_pickle_cache(fallback_path, AI_CACHE_VERSION)
        if data is not None:
            _save_ai_runtime_cache(cache_key, data)
            return data, "legacy_hit", fallback_key
    return None, "miss", None

def _save_ai_runtime_cache(cache_key: str, data: dict) -> bool:
    return _save_pickle_cache(_ai_cache_path(cache_key), AI_CACHE_VERSION, data)

def _export_cache_key(raw_tour: pd.DataFrame, raw_others: pd.DataFrame, scope_audit: dict) -> str:
    payload = {
        "version": EXPORT_CACHE_VERSION,
        "rules_content_hash": _file_content_hash(CONFIG_FILE),
        "data": _data_semantic_fingerprint(raw_tour, raw_others),
        "scope": {
            "label": scope_audit.get("scope_label"),
            "raw_rows": scope_audit.get("raw_rows"),
            "raw_amount": round(float(scope_audit.get("raw_amount", 0) or 0), 2),
            "analysis_rows": scope_audit.get("analysis_rows"),
            "analysis_amount": round(float(scope_audit.get("analysis_amount", 0) or 0), 2),
            "excluded_order_count": scope_audit.get("excluded_order_count"),
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]

def _load_export_runtime_cache(cache_key: str) -> dict | None:
    return _load_pickle_cache(_export_cache_path(cache_key), EXPORT_CACHE_VERSION)

def _save_export_runtime_cache(cache_key: str, data: dict) -> bool:
    return _save_pickle_cache(_export_cache_path(cache_key), EXPORT_CACHE_VERSION, data)

def _buffer_to_bytes(buffer: io.BytesIO | None) -> bytes | None:
    if buffer is None:
        return None
    buffer.seek(0)
    return buffer.getvalue()

def _compute_export_workbooks(db_tour: pd.DataFrame, db_others: pd.DataFrame) -> dict:
    branch_mapping, target_branches, cruise_depts, sales_reps, _ = _current_rules()
    excel_buf, _, _ = build_dashboard_data(
        db_tour,
        db_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
    )
    excel_no_writeoff_buf, _, _ = build_dashboard_data_excluding_receipt_types(
        db_tour,
        db_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        ["掛賬核銷"],
    )
    excel_no_writeoff_refund_transfer_buf, _, _ = build_dashboard_data_excluding_receipt_types(
        db_tour,
        db_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        ["掛賬核銷"],
        excluded_payment_methods=["TT 退款轉團款"],
        include_branch_salesperson_sheet=True,
    )
    return {
        "ex": _buffer_to_bytes(excel_buf),
        "ex_no_writeoff": _buffer_to_bytes(excel_no_writeoff_buf),
        "ex_no_writeoff_refund_transfer": _buffer_to_bytes(excel_no_writeoff_refund_transfer_buf),
        "export_cache_version": EXPORT_CACHE_VERSION,
        "official_export_schema": OFFICIAL_EXPORT_SCHEMA_CONTRACT,
    }

def _read_gmv_exclusion_file(file_obj) -> pd.DataFrame:
    _reset_uploaded_file(file_obj)
    try:
        name = str(getattr(file_obj, "name", "")).lower()
        if name.endswith(".csv"):
            return pd.read_csv(file_obj, dtype=str)
        return pd.read_excel(file_obj, dtype=str)
    finally:
        _reset_uploaded_file(file_obj)

def _parse_gmv_exclusion_ids(file_obj) -> tuple[set[str], pd.DataFrame]:
    df = _read_gmv_exclusion_file(file_obj)
    if df.empty:
        return set(), pd.DataFrame(columns=["交易號碼"])
    source_col = "交易號碼" if "交易號碼" in df.columns else df.columns[0]
    ids = clean_invoice_number(df[source_col]).replace({"": pd.NA, "NAN": pd.NA}).dropna()
    audit = pd.DataFrame({"交易號碼": sorted(set(ids.astype(str)))})
    return set(audit["交易號碼"].astype(str)), audit

def _order_id_series(df: pd.DataFrame) -> pd.Series:
    if df.empty or COL_ORDER_ID not in df.columns:
        return pd.Series("", index=df.index, dtype=str)
    return clean_invoice_number(df[COL_ORDER_ID])

def _filter_gmv_exclusion_frames(
    db_tour: pd.DataFrame,
    db_others: pd.DataFrame,
    exclusion_ids: set[str],
) -> dict:
    tour = normalize_runtime_columns(db_tour.copy())
    others = normalize_runtime_columns(db_others.copy())
    tour_ids = _order_id_series(tour)
    others_ids = _order_id_series(others)
    tour_mask = tour_ids.isin(exclusion_ids) if exclusion_ids else pd.Series(False, index=tour.index)
    others_mask = others_ids.isin(exclusion_ids) if exclusion_ids else pd.Series(False, index=others.index)
    excluded_tour = tour.loc[tour_mask].copy()
    excluded_others = others.loc[others_mask].copy()
    filtered_tour = tour.loc[~tour_mask].copy()
    filtered_others = others.loc[~others_mask].copy()
    matched_ids = set(_order_id_series(excluded_tour)) | set(_order_id_series(excluded_others))
    unmatched_ids = sorted(exclusion_ids - matched_ids)
    excluded_detail = pd.concat(
        [
            excluded_tour.assign(資料表="旅行團"),
            excluded_others.assign(資料表="其他業務"),
        ],
        ignore_index=True,
        sort=False,
    )
    return {
        "tour": filtered_tour,
        "others": filtered_others,
        "excluded_tour": excluded_tour,
        "excluded_others": excluded_others,
        "excluded_detail": excluded_detail,
        "matched_ids": matched_ids,
        "unmatched_ids": unmatched_ids,
    }

def _gmv_amount(df: pd.DataFrame) -> float:
    return _sum_money(df)

def _gmv_summary_rows(db_tour: pd.DataFrame, db_others: pd.DataFrame, filtered: dict, exclusion_ids: set[str]) -> list[dict]:
    before = _gmv_amount(db_tour) + _gmv_amount(db_others)
    excluded = _gmv_amount(filtered["excluded_tour"]) + _gmv_amount(filtered["excluded_others"])
    after = _gmv_amount(filtered["tour"]) + _gmv_amount(filtered["others"])
    return [
        {"指標": "排除清單筆數", "數值": len(exclusion_ids)},
        {"指標": "成功匹配訂單數", "數值": len(filtered["matched_ids"])},
        {"指標": "未匹配訂單數", "數值": len(filtered["unmatched_ids"])},
        {"指標": "排除前 GMV", "數值": round(before, 2)},
        {"指標": "排除金額", "數值": round(excluded, 2)},
        {"指標": "排除後 GMV", "數值": round(after, 2)},
    ]

def _compute_gmv_exclusion_workbooks(filtered_tour: pd.DataFrame, filtered_others: pd.DataFrame) -> dict:
    return _compute_export_workbooks(filtered_tour, filtered_others)

def _build_gmv_audit_workbook(summary_rows: list[dict], excluded_detail: pd.DataFrame, unmatched_ids: list[str]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="排除清單摘要", index=False)
        excluded_detail.to_excel(writer, sheet_name="匹配排除明細", index=False)
        pd.DataFrame({"交易號碼": unmatched_ids}).to_excel(writer, sheet_name="未匹配交易號碼", index=False)
    buf.seek(0)
    return buf.getvalue()

def _compute_ai_runtime_outputs(analysis_tour: pd.DataFrame, analysis_others: pd.DataFrame) -> dict:
    backtest_report, backtest_err = run_ai_backtest_report(analysis_tour, analysis_others)
    macro_backtest_report, macro_backtest_err = run_ai_macro_backtest_report(analysis_tour, analysis_others, window_days=15)
    pred_tracks, err = run_ai_prediction_tracks(
        analysis_tour,
        analysis_others,
        strategy_by_horizon=_strategy_by_horizon_from_backtest(backtest_report),
        f_steps=35,
    )
    return {
        "ptrk": pred_tracks,
        "err": err,
        "bt": backtest_report,
        "bt_err": backtest_err,
        "bt_macro": macro_backtest_report,
        "bt_macro_err": macro_backtest_err,
    }

def _empty_ai_runtime_payload(message: str = "") -> dict:
    return {
        "ptrk": None,
        "err": message,
        "bt": {},
        "bt_err": None,
        "bt_macro": {},
        "bt_macro_err": None,
    }

def _load_and_compute_cache(include_ai: bool = True) -> None:
    generation = load_cache_generation(db_path=database_module.DB_FILE)
    generation_token = str(generation.get("cacheToken") or "0:missing")
    branch_mapping, target_branches, cruise_depts, sales_reps, _ = _current_rules()
    try:
        facts = build_dashboard_facts(
            db_path=database_module.DB_FILE,
            generation_token=generation_token,
            branch_mapping=branch_mapping,
            target_branches_s3=target_branches,
            cruise_depts=cruise_depts,
            sales_rep_list=sales_reps,
            cache_dir=AI_CACHE_DIR,
        )
    except Exception as exc:
        st.session_state["PROCESSED_DATA_CACHE"] = None
        st.session_state["DB_LOADED_FLAG"] = False
        raise RuntimeError(
            f"Dashboard Facts 建立失敗：db_path={database_module.DB_FILE}; "
            f"generation_token={generation_token}; {type(exc).__name__}: {exc}"
        ) from exc

    db_tour = facts["rawTour"]
    db_others = facts["rawOthers"]
    if db_tour.empty and db_others.empty:
        st.session_state["PROCESSED_DATA_CACHE"] = None
        st.session_state["DB_LOADED_FLAG"] = True
        return
    analysis_tour = facts["analysisTour"]
    analysis_others = facts["analysisOthers"]
    scope_audit = facts["scopeAudit"]
    s1 = facts["branchFacts"]
    s2 = facts["specialistFacts"]
    ai_cache_key = _ai_cache_key(scope_audit, analysis_tour, analysis_others)
    legacy_ai_cache_key = _legacy_ai_cache_key(scope_audit)
    ai_payload, ai_cache_status, ai_cache_source_key = _load_ai_runtime_cache(ai_cache_key, fallback_key=legacy_ai_cache_key)
    if ai_payload is None:
        if include_ai:
            ai_payload = _compute_ai_runtime_outputs(analysis_tour, analysis_others)
            _save_ai_runtime_cache(ai_cache_key, ai_payload)
            ai_cache_status = "rebuilt"
            ai_cache_source_key = ai_cache_key
        else:
            ai_payload = _empty_ai_runtime_payload("AI 預測 cache 已延後重建；營運 dashboard 已先刷新。")
            ai_cache_status = "deferred"
            ai_cache_source_key = None
    export_cache_key = _export_cache_key(normalize_runtime_columns(db_tour), normalize_runtime_columns(db_others), scope_audit)
    export_cache_path = _export_cache_path(export_cache_key)
    export_cache_status = "ready" if export_cache_path.exists() else "not_prepared"
    st.session_state["PROCESSED_DATA_CACHE"] = {
        "ex": None,
        "ex_no_writeoff": None,
        "ex_no_writeoff_refund_transfer": None,
        "anm": pd.DataFrame(),
        "t": analysis_tour,
        "o": analysis_others,
        "raw_t": normalize_runtime_columns(db_tour),
        "raw_o": normalize_runtime_columns(db_others),
        "s1": s1,
        "s2": s2,
        "ptrk": ai_payload.get("ptrk"),
        "err": ai_payload.get("err"),
        "bt": ai_payload.get("bt"),
        "bt_err": ai_payload.get("bt_err"),
        "bt_macro": ai_payload.get("bt_macro"),
        "bt_macro_err": ai_payload.get("bt_macro_err"),
        "ai_cache_key": ai_cache_key,
        "ai_cache_source_key": ai_cache_source_key,
        "ai_cache_status": ai_cache_status,
        "export_cache_key": export_cache_key,
        "export_cache_status": export_cache_status,
        "export_cache_path": str(export_cache_path),
        "export_cache_version": EXPORT_CACHE_VERSION,
        "official_export_schema": OFFICIAL_EXPORT_SCHEMA_CONTRACT,
        "scope": scope_audit,
        "facts_service_version": facts["serviceVersion"],
        "facts_cache_key": facts["cacheKey"],
        "facts_cache_status": facts["factsCacheStatus"],
    }
    st.session_state["DB_LOADED_FLAG"] = True
    st.session_state["DATA_GENERATION"] = int(generation.get("generation", 0))
    st.session_state["DATA_GENERATION_TOKEN"] = generation_token


def _invalidate_session_cache_if_generation_changed() -> bool:
    current = load_cache_generation(db_path=database_module.DB_FILE)
    generation = int(current.get("generation", 0))
    token = str(current.get("cacheToken") or f"{generation}:missing")
    loaded_token = st.session_state.get("DATA_GENERATION_TOKEN")
    if loaded_token is None:
        st.session_state["DATA_GENERATION"] = generation
        st.session_state["DATA_GENERATION_TOKEN"] = token
        return False
    if str(loaded_token) == token:
        return False
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["DATA_GENERATION"] = generation
    st.session_state["DATA_GENERATION_TOKEN"] = token
    return True

def _rebuild_cache_after_database_restore() -> None:
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    _load_and_compute_cache()

def _repair_operator_assignments_before_load() -> None:
    try:
        repair_info = repair_operator_sales_rep_assignments(st.session_state["SALES_REP_LIST"])
    except Exception:
        st.warning("依收款操作員修復專職歸屬時發生錯誤，系統已保留原資料繼續載入。")
        return

    updated = int(repair_info.get("updated", 0) or 0)
    if updated <= 0:
        return

    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["OPERATOR_REPAIR_NOTICE"] = f"已依收款操作員修正 {updated} 筆專職銷售歸屬。"

def _repair_subtable_branch_assignments_before_load() -> None:
    try:
        repair_info = repair_subtable_branch_assignments(st.session_state["SALES_REP_LIST"])
    except Exception:
        st.warning("依副表銷售點修復分社歸屬時發生錯誤，系統已保留原資料繼續載入。")
        return

    updated = int(repair_info.get("updated", 0) or 0)
    if updated <= 0:
        return

    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["SUBTABLE_BRANCH_REPAIR_NOTICE"] = f"已依副表銷售點修正 {updated} 筆歸屬。"


def _persistent_repair_token(
    generation_token: str,
    *,
    operator_rule_version: int | None,
    subtable_rule_version: int | None,
) -> str:
    return (
        f"{generation_token}|operator:{operator_rule_version or 0}"
        f"|subtable:{subtable_rule_version or 0}"
    )


def _should_run_persistent_repairs(current_token: str, checked_token: str | None) -> bool:
    return bool(current_token) and current_token != checked_token


def _refresh_generation_after_database_write(
    rollback_status: str,
    *,
    db_path: str | Path,
    refresher=refresh_cache_generation_signature,
) -> dict:
    """Refresh runtime DB signature after a legacy upload reaches a safe state."""
    if str(rollback_status) not in {"accepted", "rejected_rolled_back"}:
        return {"status": "skipped", "reason": "database_state_not_verified"}
    try:
        return dict(refresher(db_path=db_path))
    except Exception as exc:
        return {"status": "degraded", "error": f"{type(exc).__name__}: {exc}"}


def _load_persistent_repair_token(path: str | Path | None = None) -> str | None:
    target = Path(path or PERSISTENT_REPAIR_STATE_PATH)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    token = payload.get("checkToken") if isinstance(payload, dict) else None
    return str(token) if token else None


def _save_persistent_repair_token(token: str, path: str | Path | None = None) -> None:
    target = Path(path or PERSISTENT_REPAIR_STATE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"checkToken": str(token)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _run_persistent_repairs_before_load() -> None:
    generation = load_cache_generation(db_path=database_module.DB_FILE)
    current_token = _persistent_repair_token(
        str(generation.get("cacheToken") or "0:missing"),
        operator_rule_version=st.session_state.get("OPERATOR_REPAIR_RULE_VERSION"),
        subtable_rule_version=st.session_state.get("SUBTABLE_BRANCH_REPAIR_RULE_VERSION"),
    )
    checked_token = st.session_state.get("PERSISTENT_REPAIR_CHECK_TOKEN") or _load_persistent_repair_token()
    if not _should_run_persistent_repairs(current_token, checked_token):
        st.session_state["PERSISTENT_REPAIR_CHECK_TOKEN"] = current_token
        return

    _repair_subtable_branch_assignments_before_load()
    _repair_operator_assignments_before_load()
    if not st.session_state.get("SUBTABLE_BRANCH_REPAIR_NOTICE") and not st.session_state.get("OPERATOR_REPAIR_NOTICE"):
        st.session_state["PERSISTENT_REPAIR_CHECK_TOKEN"] = current_token
        _save_persistent_repair_token(current_token)
        return

    try:
        refreshed = refresh_cache_generation_signature(db_path=database_module.DB_FILE)
    except Exception as exc:
        st.warning(f"資料修復後更新 cache generation 失敗：{type(exc).__name__}: {exc}")
        return

    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["PERSISTENT_REPAIR_CHECK_TOKEN"] = _persistent_repair_token(
        str(refreshed.get("cacheToken") or "0:missing"),
        operator_rule_version=st.session_state.get("OPERATOR_REPAIR_RULE_VERSION"),
        subtable_rule_version=st.session_state.get("SUBTABLE_BRANCH_REPAIR_RULE_VERSION"),
    )
    _save_persistent_repair_token(st.session_state["PERSISTENT_REPAIR_CHECK_TOKEN"])

def _refresh_cache_and_rerun() -> None:
    st.session_state["DB_LOADED_FLAG"] = False
    _load_and_compute_cache()
    st.rerun()

def _clean_editor_list(df: pd.DataFrame, col: str) -> list[str]:
    if df.empty or col not in df.columns:
        return []
    return [str(v).replace("\u3000", " ").strip() for v in df[col].dropna().tolist() if str(v).strip()]

def _apply_filters(df: pd.DataFrame, date_col: str, y_sel: list[int], m_sel: list[str], dt_rng) -> pd.DataFrame:
    res = df.copy()
    if date_col not in res.columns:
        return res
    dt_series = pd.to_datetime(res[date_col], errors="coerce")
    mask = pd.Series(True, index=res.index)
    if y_sel:
        mask &= dt_series.dt.year.isin(y_sel)
    if m_sel:
        mask &= dt_series.dt.strftime("%Y-%m").isin(m_sel)
    if isinstance(dt_rng, (tuple, list)) and len(dt_rng) == 2:
        start_dt = pd.to_datetime(dt_rng[0], errors="coerce")
        end_dt = pd.to_datetime(dt_rng[1], errors="coerce")
        if pd.notna(start_dt) and pd.notna(end_dt):
            mask &= dt_series.between(start_dt, end_dt)
    elif dt_rng is not None and not isinstance(dt_rng, (tuple, list)):
        one_dt = pd.to_datetime(dt_rng, errors="coerce")
        if pd.notna(one_dt):
            mask &= dt_series.dt.normalize() == one_dt.normalize()
    return res[mask].copy()

def _metric_values(df: pd.DataFrame, selected_col: str) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    return pd.to_numeric(df.get(selected_col, 0), errors="coerce").fillna(0)

def _safe_option_list(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, pd.Series):
        iterable = values.tolist()
    else:
        iterable = list(values)
    return sorted({str(v).strip() for v in iterable if pd.notna(v) and str(v).strip()})

def _format_quality_display_value(value) -> str:
    if value is None or pd.isna(value):
        return "不適用"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
    return str(value)

def _build_data_quality_workbook(scorecard: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        scorecard["overview"].to_excel(writer, sheet_name="Scorecard Overview", index=False)
        scorecard["dimension_summary"].to_excel(writer, sheet_name="Dimension Summary", index=False)
        scorecard["field_completeness"].to_excel(writer, sheet_name="Field Completeness", index=False)
        scorecard["date_coverage"].to_excel(writer, sheet_name="Date Coverage", index=False)
        scorecard["entity_resolution"].to_excel(writer, sheet_name="Entity Resolution", index=False)
        scorecard["official_scope"].to_excel(writer, sheet_name="Official Scope", index=False)
        scorecard["amount_health"].to_excel(writer, sheet_name="Amount Health", index=False)
    buf.seek(0)
    return buf.getvalue()

def _build_entity_resolution_workbook(entity_audit: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        entity_audit.get("summary", pd.DataFrame()).to_excel(writer, sheet_name="匹配總覽", index=False)
        entity_audit.get("source_breakdown", pd.DataFrame()).to_excel(writer, sheet_name="來源拆解", index=False)
        entity_audit.get("duplicate_detail", pd.DataFrame()).to_excel(writer, sheet_name="重複單號明細", index=False)
        entity_audit.get("unmatched_detail", pd.DataFrame()).to_excel(writer, sheet_name="未匹配明細", index=False)
        entity_audit.get("id_cleaning_samples", pd.DataFrame()).to_excel(writer, sheet_name="ID清洗樣本", index=False)
    buf.seek(0)
    return buf.getvalue()

def _cleaning_candidate_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).replace("\u3000", " ").strip()
    if text.upper() in {"", "NAN", "NONE", "NULL"}:
        return ""
    return text

def _cleaning_id_prefix(value, length: int = 2) -> str:
    text = _cleaning_candidate_text(value).upper()
    cleaned = "".join(ch for ch in text if ch.isalnum())
    if len(cleaned) < length:
        return ""
    return cleaned[:length]

def _cleaning_suggestion_id(rule_type: str, candidate: str) -> str:
    digest = hashlib.sha1(f"{rule_type}|{candidate}".encode("utf-8")).hexdigest()[:10]
    return f"{rule_type}-{digest}"

def _cleaning_confidence_label(score: float | int | None) -> str:
    if score is None or pd.isna(score):
        return "觀察"
    value = float(score)
    if value >= 85:
        return "高信心"
    if value >= 70:
        return "中信心"
    return "觀察"

def _cleaning_new_suggestion(
    rows: list[dict],
    rule_type: str,
    candidate: str,
    evidence_source: str,
    impact_rows: int,
    confidence: float,
    risk: str,
    action: str,
    reason: str,
    *,
    suggested_name: str = "",
    actionable: bool | None = None,
) -> None:
    candidate = _cleaning_candidate_text(candidate)
    if not candidate:
        return
    confidence = float(max(0, min(100, confidence)))
    can_apply = bool(confidence >= 70 if actionable is None else actionable)
    rows.append(
        {
            "套用": False,
            "建議ID": _cleaning_suggestion_id(rule_type, candidate),
            "建議類型": rule_type,
            "候選值": candidate,
            "建議名稱": suggested_name,
            "證據來源": evidence_source,
            "影響行數": int(impact_rows or 0),
            "信心分": round(confidence, 1),
            "信心等級": _cleaning_confidence_label(confidence),
            "風險": risk,
            "建議動作": action,
            "可落規則": can_apply,
            "為什麼建議": reason,
        }
    )

def _cleaning_rule_exists(rule_type: str, candidate: str) -> bool:
    candidate_text = _cleaning_candidate_text(candidate)
    if not candidate_text:
        return True
    if rule_type == "BRANCH_MAPPING":
        return candidate_text.upper() in {str(k).upper() for k in st.session_state.get("BRANCH_MAPPING", {}).keys()}
    current_values = st.session_state.get(rule_type, [])
    return candidate_text in {_cleaning_candidate_text(v) for v in current_values}

def _cleaning_raw_frames(cache: dict) -> pd.DataFrame:
    raw_t = normalize_runtime_columns(cache.get("raw_t", pd.DataFrame()).copy())
    raw_o = normalize_runtime_columns(cache.get("raw_o", pd.DataFrame()).copy())
    if not raw_t.empty and "資料來源" not in raw_t.columns:
        raw_t["資料來源"] = "旅行團"
    if not raw_o.empty and "資料來源" not in raw_o.columns:
        raw_o["資料來源"] = raw_o.get("來源報表標籤", "其他業務")
    return _combine_quality_frames([raw_t, raw_o])

def _compute_ai_cleaning_suggestions(cache: dict) -> dict:
    rows: list[dict] = []
    raw_df = _cleaning_raw_frames(cache)
    anomaly_df = cache.get("anm", pd.DataFrame())
    entity_audit = _current_entity_resolution_audit(cache)
    branch_mapping = st.session_state.get("BRANCH_MAPPING", {})
    mapped_prefixes = {str(k).upper() for k in branch_mapping.keys()}
    excluded_prefixes = {str(v).upper() for v in st.session_state.get("EXCLUDE_PREFIXES", [])}

    if isinstance(anomaly_df, pd.DataFrame) and not anomaly_df.empty:
        if {"異常發生欄位", "原始異常值", "處理狀態"}.issubset(anomaly_df.columns):
            sales_unknown = anomaly_df[
                anomaly_df["異常發生欄位"].astype(str).eq(COL_SALESPERSON)
                & anomaly_df["處理狀態"].astype(str).str.contains("無法匹配|未匹配", na=False)
            ].copy()
            if not sales_unknown.empty:
                counts = sales_unknown["原始異常值"].map(_cleaning_candidate_text)
                counts = counts[counts.ne("")]
                for candidate, count in counts.value_counts().head(20).items():
                    if count < 3 or _cleaning_rule_exists("SALES_REP_LIST", candidate):
                        continue
                    _cleaning_new_suggestion(
                        rows,
                        "SALES_REP_LIST",
                        candidate,
                        "清洗異常日誌：銷售員 fuzzy 無法匹配",
                        int(count),
                        min(92, 58 + count * 6),
                        "中",
                        "人工確認是否加入專職銷售代表名單",
                        "同一銷售員名稱多次出現在無法匹配紀錄中，可能是新專職、別名或格式差異。",
                    )

    if not raw_df.empty and COL_ORDER_ID in raw_df.columns:
        ids = clean_invoice_number(raw_df[COL_ORDER_ID]).map(_cleaning_candidate_text)
        raw_work = raw_df.copy()
        raw_work["_clean_id"] = ids
        raw_work["_prefix2"] = raw_work["_clean_id"].map(lambda v: _cleaning_id_prefix(v, 2))
        source_col = "來源報表標籤" if "來源報表標籤" in raw_work.columns else "資料來源"
        source_series = raw_work.get(source_col, pd.Series("", index=raw_work.index)).fillna("").astype(str).str.strip()
        branch_series = raw_work.get(COL_BRANCH, pd.Series("", index=raw_work.index)).fillna("").astype(str).str.strip()
        unknown_mask = source_series.eq("未匹配") | branch_series.isin(["", "未知", "nan", "None"])
        prefix_counts = (
            raw_work.loc[unknown_mask & raw_work["_prefix2"].ne(""), "_prefix2"]
            .value_counts()
            .head(30)
        )
        for prefix, count in prefix_counts.items():
            prefix_upper = str(prefix).upper()
            if prefix_upper in mapped_prefixes or prefix_upper in excluded_prefixes:
                continue
            confidence = min(82, 45 + count * 5)
            _cleaning_new_suggestion(
                rows,
                "BRANCH_MAPPING",
                prefix_upper,
                "SQLite 已落地資料：未知銷售點 / 未匹配單號 prefix",
                int(count),
                confidence,
                "中",
                "先補上分社名稱，再套用到銷售點代碼對應表",
                "同一來源單據號 prefix 多次出現在未知歸屬或未匹配行中，可能需要補充分社 mapping。",
                suggested_name=AI_CLEANING_BRANCH_PLACEHOLDER,
                actionable=confidence >= 75,
            )
            if count >= 20:
                _cleaning_new_suggestion(
                    rows,
                    "EXCLUDE_PREFIXES",
                    prefix_upper,
                    "SQLite 已落地資料：高頻未知 / 未匹配單號 prefix",
                    int(count),
                    min(78, 42 + count * 2),
                    "高",
                    "僅在確認這是非營收或需排除單號族群後才加入排除前綴",
                    "此 prefix 高頻出現在未知/未匹配樣本中，但排除規則風險較高，必須人工確認業務含義。",
                    actionable=min(78, 42 + count * 2) >= 75 and prefix_upper not in mapped_prefixes,
                )

    if not raw_df.empty:
        dept_col = "團負責人部門"
        text_cols = [c for c in ["來源報表標籤", "資料來源", "團名稱", "目的地大類", "一級目的地", "二級目的地"] if c in raw_df.columns]
        if dept_col in raw_df.columns and text_cols:
            text_signal = pd.Series("", index=raw_df.index, dtype=object)
            for col in text_cols:
                text_signal = text_signal.str.cat(raw_df[col].fillna("").astype(str), sep=" ")
            cruise_mask = text_signal.str.contains("郵輪|游輪|cruise", case=False, na=False)
            dept_counts = raw_df.loc[cruise_mask, dept_col].map(_cleaning_candidate_text)
            dept_counts = dept_counts[dept_counts.ne("")]
            for candidate, count in dept_counts.value_counts().head(15).items():
                if count < 3 or _cleaning_rule_exists("CRUISE_DEPTS", candidate):
                    continue
                _cleaning_new_suggestion(
                    rows,
                    "CRUISE_DEPTS",
                    candidate,
                    "SQLite 已落地資料：郵輪關鍵字 + 團負責人部門",
                    int(count),
                    min(90, 60 + count * 5),
                    "低",
                    "確認後加入郵輪部門名單",
                    "此部門多次出現在含郵輪關鍵字的交易中，可能應納入現有郵輪部門規則。",
                )

    suggestions = pd.DataFrame(rows)
    if not suggestions.empty:
        suggestions = suggestions.drop_duplicates(subset=["建議類型", "候選值"], keep="first")
        suggestions = suggestions.sort_values(["可落規則", "信心分", "影響行數"], ascending=[False, False, False]).reset_index(drop=True)

    total = int(len(suggestions))
    actionable = int(suggestions["可落規則"].sum()) if not suggestions.empty else 0
    observation = total - actionable
    avg_confidence = float(pd.to_numeric(suggestions.get("信心分", pd.Series(dtype=float)), errors="coerce").mean()) if not suggestions.empty else None
    metrics = pd.DataFrame(
        [
            {"指標": "建議總數", "數值": total, "說明": "由本地規則從異常、匹配與欄位完整度派生。"},
            {"指標": "可一鍵套用建議", "數值": actionable, "說明": "信心與風險通過 v1 門檻，仍需人工勾選確認。"},
            {"指標": "觀察級建議", "數值": observation, "說明": "只供下載與排查，不允許直接落規則。"},
            {"指標": "平均信心分", "數值": round(avg_confidence, 2) if avg_confidence is not None and not pd.isna(avg_confidence) else None, "說明": "不是模型準確率，只表示本地證據強度。"},
            {"指標": "Entity Audit 未匹配行數", "數值": entity_audit.get("unmatched_rows", 0), "說明": "作為建議生成的其中一個稽核來源。"},
        ]
    )
    definitions = pd.DataFrame(
        [
            {"建議類型": "EXCLUDE_PREFIXES", "可落位置": "rules_config.json / EXCLUDE_PREFIXES", "說明": "疑似需要排除的來源單據號 prefix；高風險，需人工確認。"},
            {"建議類型": "SALES_REP_LIST", "可落位置": "rules_config.json / SALES_REP_LIST", "說明": "高頻未知或 fuzzy 未匹配的專職銷售代表候選。"},
            {"建議類型": "BRANCH_MAPPING", "可落位置": "rules_config.json / BRANCH_MAPPING", "說明": "高頻未知銷售點 prefix；必須人工填入分社名稱。"},
            {"建議類型": "CRUISE_DEPTS", "可落位置": "rules_config.json / CRUISE_DEPTS", "說明": "含郵輪訊號的部門候選，只追加到現有郵輪部門名單。"},
        ]
    )
    return {"suggestions": suggestions, "metrics": metrics, "definitions": definitions}

def _build_ai_cleaning_suggestions_workbook(ai_cleaning: dict) -> bytes:
    buf = io.BytesIO()
    suggestions = ai_cleaning.get("suggestions", pd.DataFrame()).copy()
    if "套用" in suggestions.columns:
        suggestions = suggestions.drop(columns=["套用"])
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        ai_cleaning.get("metrics", pd.DataFrame()).to_excel(writer, sheet_name="Suggestion Metrics", index=False)
        suggestions.to_excel(writer, sheet_name="Suggestion Inbox", index=False)
        ai_cleaning.get("definitions", pd.DataFrame()).to_excel(writer, sheet_name="Rule Type Definitions", index=False)
        pd.DataFrame(
            [
                {"安全邊界": "本地智能規則，不接外部 LLM，不外傳資料。"},
                {"安全邊界": "建議未被人工勾選並按下套用前，不會修改 rules_config.json。"},
                {"安全邊界": "套用後只更新現有規則類型，不寫 SQLite、不改已入庫資料。"},
            ]
        ).to_excel(writer, sheet_name="Guardrails", index=False)
    buf.seek(0)
    return buf.getvalue()

def _apply_ai_cleaning_suggestions(selected: pd.DataFrame) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    skipped: list[str] = []
    new_rules = {
        "BRANCH_MAPPING": dict(st.session_state.get("BRANCH_MAPPING", {})),
        "EXCLUDE_PREFIXES": list(st.session_state.get("EXCLUDE_PREFIXES", [])),
        "TARGET_BRANCHES_S3": list(st.session_state.get("TARGET_BRANCHES_S3", [])),
        "SALES_REP_LIST": list(st.session_state.get("SALES_REP_LIST", [])),
        "CRUISE_DEPTS": list(st.session_state.get("CRUISE_DEPTS", [])),
        "BRANCH_REASSIGNMENT_OVERRIDES": list(st.session_state.get("BRANCH_REASSIGNMENT_OVERRIDES", [])),
    }

    for _, row in selected.iterrows():
        rule_type = str(row.get("建議類型", "")).strip()
        candidate = _cleaning_candidate_text(row.get("候選值"))
        if rule_type not in AI_CLEANING_RULE_TYPES or not candidate:
            skipped.append(f"{rule_type or '未知'} / {candidate or '空白'}：規則類型或候選值無效")
            continue
        if not bool(row.get("可落規則", False)):
            skipped.append(f"{rule_type} / {candidate}：觀察級建議，不允許一鍵落規則")
            continue

        if rule_type == "BRANCH_MAPPING":
            prefix = candidate.upper()
            branch_name = _cleaning_candidate_text(row.get("建議名稱"))
            if not branch_name or branch_name == AI_CLEANING_BRANCH_PLACEHOLDER:
                skipped.append(f"BRANCH_MAPPING / {prefix}：請先填入分社名稱")
                continue
            if prefix in {str(k).upper() for k in new_rules["BRANCH_MAPPING"].keys()}:
                skipped.append(f"BRANCH_MAPPING / {prefix}：規則已存在")
                continue
            new_rules["BRANCH_MAPPING"][prefix] = branch_name
            applied.append(f"BRANCH_MAPPING：{prefix} → {branch_name}")
            continue

        current = {_cleaning_candidate_text(v) for v in new_rules[rule_type]}
        if candidate in current:
            skipped.append(f"{rule_type} / {candidate}：規則已存在")
            continue
        new_rules[rule_type].append(candidate)
        applied.append(f"{rule_type}：{candidate}")

    if applied:
        for key in SESSION_RULE_KEYS:
            st.session_state[key] = new_rules[key]
        if not save_business_rules(new_rules):
            return [], ["rules_config.json 寫入失敗，請檢查資料夾權限。"]
        st.session_state["PROCESSED_DATA_CACHE"] = None
        st.session_state["DB_LOADED_FLAG"] = False
    return applied, skipped

def _governance_health_label(score: float | int | None) -> str:
    if score is None or pd.isna(score):
        return "未評估"
    value = float(score)
    if value >= 90:
        return "優秀"
    if value >= 75:
        return "可接受"
    if value >= 60:
        return "需關注"
    return "需處理"

def _governance_score_from_error(value: float | int | None) -> float:
    if value is None or pd.isna(value):
        return 55.0
    return float(max(0, min(100, 100 - float(value) * 1.2)))

def _governance_bias_direction(bias: float | int | None) -> str:
    if bias is None or pd.isna(bias):
        return "未評估"
    if float(bias) > 0:
        return "偏高估"
    if float(bias) < 0:
        return "偏低估"
    return "中性"

def _governance_bias_score(row: pd.Series) -> float:
    mae = float(row.get("MAE", 0) or 0)
    bias = row.get("Bias")
    if mae <= 0 or bias is None or pd.isna(bias):
        return 60.0
    ratio = abs(float(bias)) / mae
    return float(max(0, min(100, 100 - ratio * 80)))

def _governance_sample_score(sample_n: float | int | None) -> float:
    if sample_n is None or pd.isna(sample_n):
        return 45.0
    value = float(sample_n)
    if value >= 30:
        return 100.0
    if value >= 20:
        return 85.0
    if value >= 10:
        return 65.0
    if value >= 5:
        return 45.0
    return 25.0

def _governance_stability_score(detail_df: pd.DataFrame, mask: pd.Series | None = None) -> tuple[float, float | None]:
    if detail_df.empty:
        return 55.0, None
    subset = detail_df.loc[mask].copy() if mask is not None else detail_df.copy()
    if subset.empty or "APE" not in subset.columns:
        return 55.0, None
    ape = pd.to_numeric(subset["APE"], errors="coerce").dropna()
    if len(ape) < 3:
        return 50.0, float(ape.std()) if not ape.empty else None
    ape_std = float(ape.std())
    score = max(0, min(100, 100 - ape_std * 1.35))
    return float(score), ape_std

def _governance_role_score(role: str) -> float:
    if role == "正式":
        return 100.0
    if role == "診斷":
        return 75.0
    if role == "實驗":
        return 55.0
    return 60.0

def _governance_action(row: dict) -> str:
    role = row.get("治理角色", "")
    health = row.get("治理燈號", "")
    bias_ratio = row.get("Bias/MAE")
    sample_n = row.get("樣本數")
    if role == "實驗":
        return "保留為實驗；未打贏正式模型且穩定性未確認前不接入正式 Forecast。"
    if role == "診斷":
        return "作為診斷最佳模型展示；不覆蓋正式 Daily Forecast。"
    if sample_n is not None and not pd.isna(sample_n) and float(sample_n) < 20:
        return "樣本不足，不建議切換或自動調權。"
    if bias_ratio is not None and not pd.isna(bias_ratio) and abs(float(bias_ratio)) > 0.8:
        return "需關注 Bias，先檢查長期高估/低估再調整模型。"
    if health in ("優秀", "可接受"):
        return "保持正式觀察；可作為管理層 forecast 健康參考。"
    if health == "需關注":
        return "保持正式但需追蹤；優先檢查偏差、波動與極端日影響。"
    return "需處理；不建議作為自動切換依據。"

def _build_governance_row(
    *,
    view: str,
    role: str,
    strategy: str,
    model: str,
    row: pd.Series,
    detail_df: pd.DataFrame,
    detail_mask: pd.Series | None,
    horizon: str | int | float = "",
) -> dict:
    metrics = [row.get(col) for col in ("WAPE", "MAPE", "MedianAPE", "SMAPE") if col in row.index and pd.notna(row.get(col))]
    avg_error = float(pd.Series(metrics).mean()) if metrics else None
    accuracy_score = _governance_score_from_error(avg_error)
    bias_score = _governance_bias_score(row)
    stability_score, ape_std = _governance_stability_score(detail_df, detail_mask)
    sample_n = row.get("樣本數")
    sample_score = _governance_sample_score(sample_n)
    role_score = _governance_role_score(role)
    governance_score = (
        accuracy_score * 0.35
        + bias_score * 0.20
        + stability_score * 0.20
        + sample_score * 0.15
        + role_score * 0.10
    )
    mae = float(row.get("MAE", 0) or 0)
    bias = row.get("Bias")
    bias_ratio = abs(float(bias)) / mae if mae > 0 and bias is not None and pd.notna(bias) else np.nan
    result = {
        "預測視角": view,
        "治理角色": role,
        "策略": strategy,
        "模型": model,
        "預測天期": horizon,
        "WAPE": row.get("WAPE", np.nan),
        "MAPE": row.get("MAPE", np.nan),
        "MedianAPE": row.get("MedianAPE", np.nan),
        "SMAPE": row.get("SMAPE", np.nan),
        "MAE": row.get("MAE", np.nan),
        "Bias": row.get("Bias", np.nan),
        "Bias方向": _governance_bias_direction(bias),
        "Bias/MAE": bias_ratio,
        "APE波動": ape_std,
        "樣本數": sample_n,
        "AccuracyScore": round(accuracy_score, 2),
        "BiasScore": round(bias_score, 2),
        "StabilityScore": round(stability_score, 2),
        "SampleScore": round(sample_score, 2),
        "RoleScore": round(role_score, 2),
        "GovernanceScore": round(governance_score, 2),
        "治理燈號": _governance_health_label(governance_score),
    }
    result["Action Recommendation"] = _governance_action(result)
    return result

def _compute_forecast_governance(cache: dict) -> dict:
    report = cache.get("bt") or {}
    macro_report = cache.get("bt_macro") or {}
    summary_df = report.get("summary", pd.DataFrame()).copy()
    detail_df = report.get("detail", pd.DataFrame()).copy()
    normal_df = report.get("daily_normal_day_experiment_summary", pd.DataFrame()).copy()
    normal_detail = report.get("daily_normal_day_experiment_detail", pd.DataFrame()).copy()
    two_lane_df = report.get("daily_two_lane_selector_summary", pd.DataFrame()).copy()
    two_lane_detail = report.get("daily_two_lane_selector_detail", pd.DataFrame()).copy()
    macro_summary_df = macro_report.get("summary", pd.DataFrame()).copy()
    macro_detail_df = macro_report.get("detail", pd.DataFrame()).copy()
    rows: list[dict] = []

    if not summary_df.empty:
        official = summary_df[summary_df.get("預測天期").eq(1) if "預測天期" in summary_df.columns else False].copy()
        if not official.empty:
            official = official.sort_values(["WAPE", "MAPE", "策略", "模型"])
            for _, item in official.iterrows():
                mask = (
                    detail_df["預測天期"].eq(item.get("預測天期"))
                    & detail_df["策略"].eq(item.get("策略"))
                    & detail_df["模型"].eq(item.get("模型"))
                ) if not detail_df.empty and {"預測天期", "策略", "模型"}.issubset(detail_df.columns) else None
                rows.append(
                    _build_governance_row(
                        view="Daily Forecast",
                        role="正式",
                        strategy=str(item.get("策略", "")),
                        model=str(item.get("模型", "")),
                        horizon=int(item.get("預測天期", 1) or 1),
                        row=item,
                        detail_df=detail_df,
                        detail_mask=mask,
                    )
                )

    if not normal_df.empty:
        for _, item in normal_df.sort_values(["WAPE", "MAPE", "模型"]).iterrows():
            mask = normal_detail["模型"].eq(item.get("模型")) if not normal_detail.empty and "模型" in normal_detail.columns else None
            rows.append(
                _build_governance_row(
                    view="Daily Forecast",
                    role="診斷",
                    strategy=str(item.get("策略", "Normal-Day Experiment")),
                    model=str(item.get("模型", "")),
                    horizon=int(item.get("預測天期", 1) or 1),
                    row=item,
                    detail_df=normal_detail,
                    detail_mask=mask,
                )
            )

    if not two_lane_df.empty:
        for _, item in two_lane_df.sort_values(["WAPE", "MAPE", "模型"]).iterrows():
            mask = two_lane_detail["模型"].eq(item.get("模型")) if not two_lane_detail.empty and "模型" in two_lane_detail.columns else None
            rows.append(
                _build_governance_row(
                    view="Daily Forecast",
                    role="實驗",
                    strategy=str(item.get("策略", "Daily Two-Lane Selector")),
                    model=str(item.get("模型", "")),
                    horizon=int(item.get("預測天期", 1) or 1),
                    row=item,
                    detail_df=two_lane_detail,
                    detail_mask=mask,
                )
            )

    if not macro_summary_df.empty:
        for _, item in macro_summary_df.sort_values(["聚合層級", "WAPE", "MAPE", "策略", "模型"]).iterrows():
            layer = str(item.get("聚合層級", "Macro Forecast"))
            mask = (
                macro_detail_df["聚合層級"].eq(item.get("聚合層級"))
                & macro_detail_df["策略"].eq(item.get("策略"))
                & macro_detail_df["模型"].eq(item.get("模型"))
            ) if not macro_detail_df.empty and {"聚合層級", "策略", "模型"}.issubset(macro_detail_df.columns) else None
            rows.append(
                _build_governance_row(
                    view=layer,
                    role="正式",
                    strategy=str(item.get("策略", "")),
                    model=str(item.get("模型", "")),
                    horizon=round(float(item.get("平均預測天數", np.nan)), 1) if pd.notna(item.get("平均預測天數", np.nan)) else "",
                    row=item,
                    detail_df=macro_detail_df,
                    detail_mask=mask,
                )
            )

    matrix = pd.DataFrame(rows)
    if matrix.empty:
        return {
            "summary": pd.DataFrame(),
            "matrix": pd.DataFrame(),
            "recommendations": pd.DataFrame(),
            "definitions": _forecast_governance_definitions(),
            "overall_score": np.nan,
            "overall_health": "未評估",
        }

    summary_rows = []
    for view, subset in matrix.groupby("預測視角", dropna=False):
        official = subset[subset["治理角色"] == "正式"].copy()
        reference = official if not official.empty else subset
        best = reference.sort_values(["GovernanceScore", "WAPE"], ascending=[False, True]).iloc[0]
        summary_rows.append(
            {
                "預測視角": view,
                "治理分數": float(best["GovernanceScore"]),
                "治理燈號": best["治理燈號"],
                "代表模型": f"{best['策略']} / {best['模型']}",
                "WAPE": best.get("WAPE", np.nan),
                "Bias方向": best.get("Bias方向", "未評估"),
                "樣本數": best.get("樣本數", np.nan),
                "治理說明": "正式模型代表列；診斷/實驗模型只作輔助參考。",
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values("預測視角").reset_index(drop=True)
    overall_score = float(summary["治理分數"].mean()) if not summary.empty else np.nan
    recommendations = (
        matrix[["預測視角", "治理角色", "策略", "模型", "治理燈號", "Action Recommendation"]]
        .drop_duplicates()
        .sort_values(["預測視角", "治理角色", "治理燈號", "策略", "模型"])
        .reset_index(drop=True)
    )
    return {
        "summary": summary,
        "matrix": matrix.sort_values(["預測視角", "治理角色", "GovernanceScore"], ascending=[True, True, False]).reset_index(drop=True),
        "recommendations": recommendations,
        "definitions": _forecast_governance_definitions(),
        "overall_score": overall_score,
        "overall_health": _governance_health_label(overall_score),
    }

def _forecast_governance_definitions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"項目": "AccuracyScore", "定義": "由 WAPE / MAPE / MedianAPE / SMAPE 綜合換算，避免只看單一 WAPE。", "權重": "35%"},
            {"項目": "BiasScore", "定義": "由 Bias / MAE 比例換算，標示長期高估或低估風險。", "權重": "20%"},
            {"項目": "StabilityScore", "定義": "由 rolling backtest APE 波動換算，波動越高分數越低。", "權重": "20%"},
            {"項目": "SampleScore", "定義": "由回測樣本數換算，樣本不足時降級。", "權重": "15%"},
            {"項目": "RoleScore", "定義": "正式 / 診斷 / 實驗角色治理成熟度；不代表模型準確率。", "權重": "10%"},
            {"項目": "治理燈號", "定義": ">=90 優秀；75-89 可接受；60-74 需關注；<60 需處理。", "權重": "不適用"},
        ]
    )

def _build_forecast_governance_workbook(governance: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        governance.get("summary", pd.DataFrame()).to_excel(writer, sheet_name="Governance Summary", index=False)
        governance.get("matrix", pd.DataFrame()).to_excel(writer, sheet_name="Model Health Matrix", index=False)
        governance.get("recommendations", pd.DataFrame()).to_excel(writer, sheet_name="Action Recommendations", index=False)
        governance.get("definitions", pd.DataFrame()).to_excel(writer, sheet_name="Metric Definitions", index=False)
    buf.seek(0)
    return buf.getvalue()

def _feature_catalog_template() -> pd.DataFrame:
    rows = [
        {
            "FeatureGroup": "Calendar / Event",
            "FeatureName": "香港假期 / 旅遊展窗口",
            "SourceTable": "business_calendar + forecasting event features",
            "Grain": "date",
            "Window": "cutoff 前已知日曆",
            "NoFutureLeakRequired": True,
            "UseCase": "節假日與旅遊展前後的需求波動識別",
            "Status": "已接入特徵工程 / 只讀稽核",
        },
        {
            "FeatureGroup": "Specialist Cadence",
            "FeatureName": "RecentSpecialistShare7D / SpecialistMomentumRatio",
            "SourceTable": "daily_spike_signal_detail",
            "Grain": "cutoff",
            "Window": "最近 7 / 14 日",
            "NoFutureLeakRequired": True,
            "UseCase": "專職成交節奏與短期需求 lead signal",
            "Status": "可回測",
        },
        {
            "FeatureGroup": "Longhaul Mix",
            "FeatureName": "RecentLonghaulShare7D / LonghaulMomentumRatio",
            "SourceTable": "daily_spike_signal_detail",
            "Grain": "cutoff",
            "Window": "最近 7 / 14 日",
            "NoFutureLeakRequired": True,
            "UseCase": "長線產品佔比變化對 Daily revenue 的先行影響",
            "Status": "可回測",
        },
        {
            "FeatureGroup": "Large Order",
            "FeatureName": "RecentLargeOrderCount7D / RecentLargeOrderAmount7D",
            "SourceTable": "daily_spike_signal_detail",
            "Grain": "cutoff",
            "Window": "最近 7 日",
            "NoFutureLeakRequired": True,
            "UseCase": "大單集中造成 spike / post-spike cooldown 的診斷",
            "Status": "可回測",
        },
        {
            "FeatureGroup": "Sales Concentration",
            "FeatureName": "RecentTopSalespersonShare7D",
            "SourceTable": "daily_spike_signal_detail",
            "Grain": "cutoff",
            "Window": "最近 7 日",
            "NoFutureLeakRequired": True,
            "UseCase": "銷售員集中度與單日波動風險",
            "Status": "可回測",
        },
        {
            "FeatureGroup": "Momentum",
            "FeatureName": "RecentTotalRevenue7D / RecentRevenueVs14DAvg",
            "SourceTable": "daily_spike_signal_detail",
            "Grain": "cutoff",
            "Window": "最近 7 / 14 日",
            "NoFutureLeakRequired": True,
            "UseCase": "收入動能、均值回歸與常規日 guardrail",
            "Status": "可回測",
        },
        {
            "FeatureGroup": "Volatility",
            "FeatureName": "RecentRevenueVolatility7D / RecentMaxDailyRevenueShare7D",
            "SourceTable": "daily_spike_signal_detail",
            "Grain": "cutoff",
            "Window": "最近 7 日",
            "NoFutureLeakRequired": True,
            "UseCase": "判斷近期是否高波動，避免盲目上調",
            "Status": "可回測",
        },
        {
            "FeatureGroup": "Month Edge",
            "FeatureName": "IsMonthEndForecastDate / 月初月末",
            "SourceTable": "daily_spike_signal_detail + date feature",
            "Grain": "date",
            "Window": "cutoff 前已知日曆",
            "NoFutureLeakRequired": True,
            "UseCase": "月初 / 月末 pacing 與收款節奏差異",
            "Status": "已接入診斷",
        },
        {
            "FeatureGroup": "Spike Class",
            "FeatureName": "SpikeRiskLevel / SpikeSignalClass / EventLeadClass",
            "SourceTable": "daily_spike_signal_detail + daily_event_lead_signal_detail",
            "Grain": "cutoff",
            "Window": "cutoff 前資訊",
            "NoFutureLeakRequired": True,
            "UseCase": "Normal / Extreme lane 分流與 Daily WAPE 長期優化",
            "Status": "實驗觀察",
        },
        {
            "FeatureGroup": "Selector",
            "FeatureName": "Daily Two-Lane Selector decision features",
            "SourceTable": "daily_two_lane_selector_detail",
            "Grain": "cutoff",
            "Window": "cutoff 前資訊",
            "NoFutureLeakRequired": True,
            "UseCase": "未來模型選擇器治理與接入前稽核",
            "Status": "實驗，不覆蓋正式 Forecast",
        },
    ]
    return pd.DataFrame(rows)

def _compute_feature_store_lead_signals(cache: dict) -> dict:
    report = cache.get("bt") or {}
    spike_detail = report.get("daily_spike_signal_detail", pd.DataFrame()).copy()
    event_detail = report.get("daily_event_lead_signal_detail", pd.DataFrame()).copy()
    spike_summary = report.get("daily_spike_signal_summary", pd.DataFrame()).copy()
    event_summary = report.get("daily_event_lead_signal_summary", pd.DataFrame()).copy()
    two_lane_summary = report.get("daily_two_lane_selector_summary", pd.DataFrame()).copy()
    catalog = _feature_catalog_template()

    snapshot_cols = [
        "Cutoff",
        "ActualDate",
        "FeatureMaxDate",
        "NoFutureLeak",
        "RecentTotalRevenue7D",
        "RecentTotalRevenue14D",
        "RecentSpecialistShare7D",
        "SpecialistMomentumRatio",
        "RecentLonghaulShare7D",
        "LonghaulMomentumRatio",
        "RecentLargeOrderCount7D",
        "RecentLargeOrderAmount7D",
        "RecentTopSalespersonShare7D",
        "RecentRevenueVolatility7D",
        "RecentRevenueVs14DAvg",
        "SpikeRiskLevel",
        "SpikeSignalClass",
        "SpikeSignalAction",
    ]
    snapshot = pd.DataFrame(columns=snapshot_cols + ["EventLeadClass", "EventLeadAction", "EventOrderMomentum7v14"])
    if not spike_detail.empty:
        available = [c for c in snapshot_cols if c in spike_detail.columns]
        snapshot = spike_detail[available].copy()
    if not event_detail.empty:
        event_cols = [c for c in ["Cutoff", "ActualDate", "EventLeadClass", "EventLeadAction", "EventOrderMomentum7v14"] if c in event_detail.columns]
        if event_cols:
            event_snapshot = event_detail[event_cols].copy()
            if snapshot.empty:
                snapshot = event_snapshot
            elif {"Cutoff", "ActualDate"}.issubset(snapshot.columns) and {"Cutoff", "ActualDate"}.issubset(event_snapshot.columns):
                snapshot = snapshot.merge(event_snapshot, on=["Cutoff", "ActualDate"], how="left")

    for col in ["Cutoff", "ActualDate", "FeatureMaxDate"]:
        if col in snapshot.columns:
            snapshot[col] = pd.to_datetime(snapshot[col], errors="coerce")
    if not snapshot.empty:
        sort_cols = [c for c in ["ActualDate", "Cutoff"] if c in snapshot.columns]
        snapshot = snapshot.sort_values(sort_cols).tail(30).reset_index(drop=True) if sort_cols else snapshot.tail(30).reset_index(drop=True)
        if "NoFutureLeak" not in snapshot.columns and {"FeatureMaxDate", "Cutoff"}.issubset(snapshot.columns):
            snapshot["NoFutureLeak"] = snapshot["FeatureMaxDate"] <= snapshot["Cutoff"]

    feature_columns = [
        col
        for col in [
            "RecentSpecialistShare7D",
            "SpecialistMomentumRatio",
            "RecentLonghaulShare7D",
            "LonghaulMomentumRatio",
            "RecentLargeOrderCount7D",
            "RecentLargeOrderAmount7D",
            "RecentTopSalespersonShare7D",
            "RecentRevenueVolatility7D",
            "RecentRevenueVs14DAvg",
            "SpikeRiskLevel",
            "SpikeSignalClass",
            "EventLeadClass",
            "EventOrderMomentum7v14",
        ]
        if col in snapshot.columns
    ]
    health_rows = []
    leak_pass_rate = np.nan
    if "NoFutureLeak" in snapshot.columns and not snapshot.empty:
        leak_values = snapshot["NoFutureLeak"].dropna()
        if not leak_values.empty:
            leak_pass_rate = float(leak_values.astype(bool).mean() * 100)
    latest_feature_date = ""
    if "FeatureMaxDate" in snapshot.columns and not snapshot["FeatureMaxDate"].dropna().empty:
        latest_feature_date = pd.Timestamp(snapshot["FeatureMaxDate"].max()).strftime("%Y-%m-%d")
    for col in feature_columns:
        nonnull = int(snapshot[col].notna().sum()) if col in snapshot.columns else 0
        total = int(len(snapshot))
        missing_rate = _safe_rate(total - nonnull, total)
        readiness = "可用" if total >= 20 and missing_rate <= 20 and (pd.isna(leak_pass_rate) or leak_pass_rate >= 100) else "觀察"
        if missing_rate > 50 or (pd.notna(leak_pass_rate) and leak_pass_rate < 100):
            readiness = "需稽核"
        health_rows.append(
            {
                "FeatureName": col,
                "樣本數": total,
                "非空樣本": nonnull,
                "缺失率": missing_rate,
                "NoFutureLeakPassRate": leak_pass_rate,
                "最新特徵日期": latest_feature_date,
                "Readiness": readiness,
            }
        )
    health = pd.DataFrame(health_rows)

    readiness_rows = []
    for label, df, evidence in [
        ("Spike Signal", spike_summary, "daily_spike_signal_summary"),
        ("Event Lead Signal", event_summary, "daily_event_lead_signal_summary"),
        ("Two-Lane Selector", two_lane_summary, "daily_two_lane_selector_summary"),
    ]:
        if df.empty:
            readiness_rows.append(
                {
                    "SignalFamily": label,
                    "EvidenceTable": evidence,
                    "樣本數": 0,
                    "最佳WAPE": np.nan,
                    "狀態": "暫無資料",
                    "建議": "等待更多回測樣本。",
                }
            )
            continue
        sample_col = "樣本數" if "樣本數" in df.columns else None
        wape_col = "WAPE" if "WAPE" in df.columns else ("最佳WAPE" if "最佳WAPE" in df.columns else None)
        best_wape = pd.to_numeric(df[wape_col], errors="coerce").min() if wape_col else np.nan
        samples = pd.to_numeric(df[sample_col], errors="coerce").max() if sample_col else len(df)
        status = "可用於診斷" if pd.notna(best_wape) and samples >= 20 else "觀察"
        recommendation = "只作長期特徵治理，不覆蓋正式 Forecast。"
        if pd.notna(best_wape) and best_wape < 35:
            recommendation = "具備進一步做 Normal/Extreme lane 實驗的價值。"
        readiness_rows.append(
            {
                "SignalFamily": label,
                "EvidenceTable": evidence,
                "樣本數": int(samples) if pd.notna(samples) else 0,
                "最佳WAPE": best_wape,
                "狀態": status,
                "建議": recommendation,
            }
        )
    readiness = pd.DataFrame(readiness_rows)
    overview = pd.DataFrame(
        [
            {"指標": "Feature Catalog 數量", "數值": len(catalog), "說明": "目前納入治理的特徵 / lead signal 條目。"},
            {"指標": "Lead Signal Snapshot 行數", "數值": len(snapshot), "說明": "最近可稽核 cutoff snapshot。"},
            {"指標": "NoFutureLeak 通過率", "數值": leak_pass_rate, "說明": "FeatureMaxDate 必須小於等於 Cutoff。"},
            {"指標": "最新特徵日期", "數值": latest_feature_date or "未評估", "說明": "最近一次特徵最大日期。"},
        ]
    )
    return {
        "catalog": catalog,
        "snapshot": snapshot,
        "health": health,
        "readiness": readiness,
        "overview": overview,
        "no_future_leak_ok": bool(pd.isna(leak_pass_rate) or leak_pass_rate >= 100),
    }

def _build_feature_store_workbook(feature_store: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        feature_store.get("overview", pd.DataFrame()).to_excel(writer, sheet_name="Feature Overview", index=False)
        feature_store.get("catalog", pd.DataFrame()).to_excel(writer, sheet_name="Feature Catalog", index=False)
        feature_store.get("snapshot", pd.DataFrame()).to_excel(writer, sheet_name="Lead Signal Snapshot", index=False)
        feature_store.get("health", pd.DataFrame()).to_excel(writer, sheet_name="Lead Signal Health", index=False)
        feature_store.get("readiness", pd.DataFrame()).to_excel(writer, sheet_name="Readiness Matrix", index=False)
    buf.seek(0)
    return buf.getvalue()

def _causal_product_line(row: pd.Series) -> str:
    text = " ".join(
        str(row.get(col, ""))
        for col in ["來源報表標籤", "資料來源", "線路種類", "產品分類", "目的地大類", "團名稱"]
        if col in row.index
    )
    if "郵輪" in text:
        return "郵輪"
    if any(key in text for key in ["套票", "酒店", "門券", "交通", "票務"]):
        if "酒店" in text:
            return "酒店"
        if "門券" in text:
            return "門券"
        if "交通" in text:
            return "交通"
        if "套票" in text:
            return "套票"
        return "票務"
    return "旅行團"

def _causal_source_frame(cache: dict) -> pd.DataFrame:
    frames = []
    for label, frame in [("旅行團", cache.get("t", pd.DataFrame())), ("其他業務", cache.get("o", pd.DataFrame()))]:
        if frame is None or frame.empty:
            continue
        tmp = frame.copy()
        tmp["資料表"] = label
        frames.append(tmp)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True, sort=False)
    if COL_DATE not in df.columns or COL_MONEY not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df[COL_DATE], errors="coerce").dt.normalize()
    df["Amount"] = pd.to_numeric(df[COL_MONEY], errors="coerce").fillna(0)
    df = df[df["Date"].notna()].copy()
    df["ProductLine"] = df.apply(_causal_product_line, axis=1)
    if COL_BRANCH in df.columns:
        df["Channel"] = np.where(df[COL_BRANCH].astype(str).eq(TARGET_DEPT_FOR_REP), "專職通路", "分社通路")
    else:
        df["Channel"] = "未分類通路"
    if "目的地大類" not in df.columns:
        df["目的地大類"] = df.get("線路種類", "未分類")
    df["目的地大類"] = df["目的地大類"].fillna("未分類").replace("", "未分類")
    day = df["Date"].dt.day
    df["MonthWindow"] = np.select([day <= 3, day >= 26], ["月初", "月末"], default="月中")
    df["WeekWindow"] = np.where(df["Date"].dt.dayofweek >= 5, "週末", "平日")
    df["EventWindow"] = df["MonthWindow"] + " / " + df["WeekWindow"]
    return df

def _default_causal_period_config(df: pd.DataFrame) -> dict:
    if df.empty or "Date" not in df.columns:
        return {}
    min_date = pd.Timestamp(df["Date"].min())
    max_date = pd.Timestamp(df["Date"].max())
    current_end = max_date
    current_start = max(min_date, current_end - pd.Timedelta(days=29))
    baseline_end = current_start - pd.Timedelta(days=1)
    baseline_start = max(min_date, baseline_end - (current_end - current_start))
    if baseline_end < min_date or df[(df["Date"] >= baseline_start) & (df["Date"] <= baseline_end)].empty:
        unique_dates = sorted(pd.to_datetime(df["Date"].dropna().unique()))
        if len(unique_dates) >= 2:
            midpoint = len(unique_dates) // 2
            baseline_start = pd.Timestamp(unique_dates[0])
            baseline_end = pd.Timestamp(unique_dates[midpoint - 1])
            current_start = pd.Timestamp(unique_dates[midpoint])
            current_end = pd.Timestamp(unique_dates[-1])
    return {
        "current_start": pd.Timestamp(current_start),
        "current_end": pd.Timestamp(current_end),
        "baseline_start": pd.Timestamp(baseline_start),
        "baseline_end": pd.Timestamp(baseline_end),
        "comparison": "目前期間 vs 前一期間",
    }

def _driver_contribution(df: pd.DataFrame, dimension: str, period_config: dict) -> tuple[pd.DataFrame, float]:
    if df.empty or dimension not in df.columns or not period_config:
        return pd.DataFrame(), np.nan
    cur_mask = (df["Date"] >= period_config["current_start"]) & (df["Date"] <= period_config["current_end"])
    base_mask = (df["Date"] >= period_config["baseline_start"]) & (df["Date"] <= period_config["baseline_end"])
    current = df.loc[cur_mask].assign(_dim=lambda x: x[dimension].fillna("未分類").astype(str).replace("", "未分類"))
    baseline = df.loc[base_mask].assign(_dim=lambda x: x[dimension].fillna("未分類").astype(str).replace("", "未分類"))
    cur_g = current.groupby("_dim", dropna=False)["Amount"].sum().rename("CurrentAmount")
    base_g = baseline.groupby("_dim", dropna=False)["Amount"].sum().rename("BaselineAmount")
    out = pd.concat([cur_g, base_g], axis=1).fillna(0).reset_index().rename(columns={"_dim": dimension})
    out["Delta"] = out["CurrentAmount"] - out["BaselineAmount"]
    total_delta = float(out["Delta"].sum())
    out["ContributionRate"] = out["Delta"] / total_delta * 100 if abs(total_delta) > 1e-9 else np.nan
    out["AbsDelta"] = out["Delta"].abs()
    out["Direction"] = np.where(out["Delta"] > 0, "拉升", np.where(out["Delta"] < 0, "拖累", "持平"))
    out["Dimension"] = dimension
    out = out.sort_values("AbsDelta", ascending=False).reset_index(drop=True)
    return out, total_delta

def _order_concentration(df: pd.DataFrame, period_config: dict, current: bool = True) -> dict:
    if df.empty or COL_ORDER_ID not in df.columns:
        return {"期間": "目前期間" if current else "比較期間", "Top5訂單佔比": np.nan, "訂單數": 0}
    start_key = "current_start" if current else "baseline_start"
    end_key = "current_end" if current else "baseline_end"
    subset = df[(df["Date"] >= period_config[start_key]) & (df["Date"] <= period_config[end_key])].copy()
    if subset.empty:
        return {"期間": "目前期間" if current else "比較期間", "Top5訂單佔比": np.nan, "訂單數": 0}
    order_amount = subset.groupby(COL_ORDER_ID)["Amount"].sum().sort_values(ascending=False)
    total = float(order_amount.sum())
    return {
        "期間": "目前期間" if current else "比較期間",
        "Top5訂單佔比": _safe_rate(float(order_amount.head(5).sum()), total),
        "訂單數": int(order_amount.size),
    }

def _compute_causal_driver_analytics(cache: dict, period_config: dict | None = None) -> dict:
    df = _causal_source_frame(cache)
    if df.empty:
        return {
            "change_summary": pd.DataFrame(),
            "drivers": {},
            "top_drivers": pd.DataFrame(),
            "event_window": pd.DataFrame(),
            "definitions": pd.DataFrame(),
            "period_config": {},
        }
    period_config = period_config or _default_causal_period_config(df)
    cur_mask = (df["Date"] >= period_config["current_start"]) & (df["Date"] <= period_config["current_end"])
    base_mask = (df["Date"] >= period_config["baseline_start"]) & (df["Date"] <= period_config["baseline_end"])
    current_amount = float(df.loc[cur_mask, "Amount"].sum())
    baseline_amount = float(df.loc[base_mask, "Amount"].sum())
    delta = current_amount - baseline_amount
    dimensions = [col for col in ["ProductLine", "Channel", COL_BRANCH, COL_SALESPERSON, "目的地大類", "來源報表標籤", "EventWindow"] if col in df.columns]
    drivers = {}
    reconciliation_rows = []
    for dim in dimensions:
        table, table_delta = _driver_contribution(df, dim, period_config)
        if not table.empty:
            drivers[dim] = table
            reconciliation_rows.append({"Dimension": dim, "DriverDeltaSum": table_delta, "TotalDelta": delta, "ReconciliationDiff": table_delta - delta})
    top_frames = []
    for dim, table in drivers.items():
        tmp = table.head(5).copy()
        tmp["Dimension"] = dim
        tmp = tmp.rename(columns={dim: "Driver"})
        top_frames.append(tmp[["Dimension", "Driver", "CurrentAmount", "BaselineAmount", "Delta", "ContributionRate", "Direction"]])
    top_drivers = pd.concat(top_frames, ignore_index=True) if top_frames else pd.DataFrame()
    event_table = drivers.get("EventWindow", pd.DataFrame()).copy()
    concentration = pd.DataFrame([_order_concentration(df, period_config, current=False), _order_concentration(df, period_config, current=True)])
    if not event_table.empty:
        event_table["說明"] = event_table.apply(
            lambda r: f"{r.get('EventWindow', '窗口')} 對期間差額影響 {r.get('Delta', 0):,.0f}，方向：{r.get('Direction', '未評估')}。",
            axis=1,
        )
    change_summary = pd.DataFrame(
        [
            {"指標": "分析類型", "數值": "解釋型 Driver Analytics", "說明": "不是嚴格因果結論。"},
            {"指標": "目前期間", "數值": f"{period_config['current_start']:%Y-%m-%d} ~ {period_config['current_end']:%Y-%m-%d}", "說明": "預設使用最新 30 日或可用最新期間。"},
            {"指標": "比較期間", "數值": f"{period_config['baseline_start']:%Y-%m-%d} ~ {period_config['baseline_end']:%Y-%m-%d}", "說明": "預設使用前一等長期間。"},
            {"指標": "目前期間收入", "數值": current_amount, "說明": REVENUE_SCOPE_LABEL},
            {"指標": "比較期間收入", "數值": baseline_amount, "說明": REVENUE_SCOPE_LABEL},
            {"指標": "收入差額", "數值": delta, "說明": "Current - Baseline。"},
            {"指標": "變動率", "數值": _safe_rate(delta, baseline_amount), "說明": "收入差額 / 比較期間收入。"},
        ]
    )
    definitions = pd.DataFrame(
        [
            {"項目": "Driver Contribution", "說明": "按維度拆解目前期間與比較期間收入差額，貢獻率以總差額為分母。"},
            {"項目": "Event Window", "說明": "v1 使用月初 / 月中 / 月末與平日 / 週末做解釋型窗口，不宣稱嚴格因果。"},
            {"項目": "Reconciliation", "說明": "每個單一維度的 Delta 加總應等於總收入差額；多維度表不可互相相加。"},
            {"項目": "正式口徑", "說明": REVENUE_SCOPE_LABEL},
        ]
    )
    return {
        "change_summary": change_summary,
        "drivers": drivers,
        "top_drivers": top_drivers,
        "event_window": event_table,
        "order_concentration": concentration,
        "reconciliation": pd.DataFrame(reconciliation_rows),
        "definitions": definitions,
        "period_config": period_config,
    }

def _build_causal_analytics_workbook(causal: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        causal.get("change_summary", pd.DataFrame()).to_excel(writer, sheet_name="Change Summary", index=False)
        causal.get("top_drivers", pd.DataFrame()).to_excel(writer, sheet_name="Top Drivers", index=False)
        causal.get("event_window", pd.DataFrame()).to_excel(writer, sheet_name="Event Window", index=False)
        causal.get("order_concentration", pd.DataFrame()).to_excel(writer, sheet_name="Order Concentration", index=False)
        causal.get("reconciliation", pd.DataFrame()).to_excel(writer, sheet_name="Reconciliation", index=False)
        sheet_map = {
            "ProductLine": "ProductLine Drivers",
            "Channel": "Channel Drivers",
            COL_BRANCH: "Branch Drivers",
            COL_SALESPERSON: "Salesperson Drivers",
            "目的地大類": "Destination Drivers",
        }
        for dim, sheet in sheet_map.items():
            table = causal.get("drivers", {}).get(dim, pd.DataFrame())
            if not table.empty:
                table.to_excel(writer, sheet_name=sheet[:31], index=False)
        causal.get("definitions", pd.DataFrame()).to_excel(writer, sheet_name="Definitions", index=False)
    buf.seek(0)
    return buf.getvalue()

def _weight_bucket_for_horizon(horizon: int) -> int:
    if horizon <= 1:
        return 1
    if horizon <= 7:
        return 7
    return 30

def _weight_schedule_from_backtest(report: dict | None) -> dict[int, dict[str, float | str]]:
    if not report:
        return {}
    weights_df = report.get("weights", pd.DataFrame())
    if weights_df.empty:
        return {}
    schedule: dict[int, dict[str, float | str]] = {}
    for _, row in weights_df.iterrows():
        version = str(row.get("權重版本", ""))
        horizon_text = version.split(" ")[0].strip()
        try:
            horizon = int(horizon_text)
        except ValueError:
            continue
        raw_weights = {
            "ARIMA": float(row.get("ARIMA", 0) or 0),
            "Prophet": float(row.get("Prophet", 0) or 0),
            "LightGBM": float(row.get("LightGBM", 0) or 0),
        }
        total = sum(raw_weights.values())
        if total <= 0:
            continue
        schedule[horizon] = {
            "策略": str(row.get("推薦策略", "")).strip() or "總額模型",
            "ARIMA": raw_weights["ARIMA"] / total,
            "Prophet": raw_weights["Prophet"] / total,
            "LightGBM": raw_weights["LightGBM"] / total,
        }
    return schedule

def _build_horizon_weighted_consensus(
    ar: pd.Series,
    pr: pd.Series,
    lgb_trk: pd.Series,
    schedule: dict[int, dict[str, float | str]],
) -> pd.Series:
    if not schedule:
        return (ar + pr + lgb_trk) / 3
    values = []
    for idx, date_value in enumerate(ar.index):
        bucket = _weight_bucket_for_horizon(idx + 1)
        weights = schedule.get(bucket) or schedule.get(30) or schedule.get(7) or schedule.get(1)
        if not weights:
            values.append(float((ar.loc[date_value] + pr.loc[date_value] + lgb_trk.loc[date_value]) / 3))
            continue
        values.append(
            float(weights["ARIMA"]) * float(ar.loc[date_value])
            + float(weights["Prophet"]) * float(pr.loc[date_value])
            + float(weights["LightGBM"]) * float(lgb_trk.loc[date_value])
        )
    return pd.Series(values, index=ar.index)

def _build_dashboard_kpis(
    s1: pd.DataFrame,
    t_df: pd.DataFrame,
    o_df: pd.DataFrame,
    year_sel: list[int],
    month_sel: list[str],
    date_rng,
) -> list[dict]:
    s1_f = _apply_filters(s1, "日期", year_sel, month_sel, date_rng)
    t_f = _apply_filters(t_df, "統一日期", year_sel, month_sel, date_rng)
    o_f = _apply_filters(o_df, "統一日期", year_sel, month_sel, date_rng)

    tour_series = pd.to_numeric(s1_f["旅行團"], errors="coerce").fillna(0) if "旅行團" in s1_f.columns else pd.Series(dtype=float)
    cruise_series = pd.to_numeric(s1_f["郵輪"], errors="coerce").fillna(0) if "郵輪" in s1_f.columns else pd.Series(dtype=float)
    ticket_series = pd.to_numeric(s1_f["票務"], errors="coerce").fillna(0) if "票務" in s1_f.columns else pd.Series(dtype=float)
    tour = float(tour_series.sum())
    cruise = float(cruise_series.sum())
    ticket = float(ticket_series.sum())
    total = tour + cruise + ticket
    branch_count = int(s1_f["文本"].astype(str).nunique()) if "文本" in s1_f.columns else 0
    sales_join = pd.concat([t_f.get(COL_SALESPERSON, pd.Series(dtype=str)), o_f.get(COL_SALESPERSON, pd.Series(dtype=str))], ignore_index=True)
    sales_count = int(sales_join.astype(str).str.strip().replace("", pd.NA).dropna().nunique())

    return [
        {
            "label": "淨營收",
            "value": _money_text(total),
            "delta": "營運總覽與管理層 KPI 視角",
            "note": f"{REVENUE_SCOPE_LABEL}；含旅行團、郵輪與票務",
            "accent": "#118DFF",
        },
        {
            "label": "旅行團營收",
            "value": _money_text(tour),
            "delta": f"佔比 {tour / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"旅行團產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#12239E",
        },
        {
            "label": "郵輪營收",
            "value": _money_text(cruise),
            "delta": f"佔比 {cruise / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"郵輪產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#E66C37",
        },
        {
            "label": "票務營收",
            "value": _money_text(ticket),
            "delta": f"佔比 {ticket / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"票務產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#6B007B",
        },
        {
            "label": "可見分社 / 專員",
            "value": f"{branch_count} / {sales_count}",
            "delta": "以 KPI 篩選條件計算",
            "note": "用來確認當前視角覆蓋範圍",
            "accent": "#197278",
        },
    ]

def _render_sidebar_shell() -> None:
    _render_sidebar_navigation()
    st.sidebar.radio(
        "介面主題",
        ["light", "dark"],
        key="NBS_UI_THEME",
        format_func=lambda value: "淺色" if value == "light" else "深色",
        horizontal=True,
    )


def _filter_option_context(cache: dict) -> dict:
    s1 = cache["s1"].copy()
    t_df = cache["t"].copy()
    o_df = cache["o"].copy()
    if s1.empty and t_df.empty and o_df.empty:
        today = pd.Timestamp.today().normalize()
        return {
            "ready": False,
            "year_opts": [],
            "month_opts": [],
            "min_dt": today,
            "max_dt": today,
            "branch_opts": [],
            "sales_opts": [],
        }

    date_pool = pd.concat(
        [
            pd.to_datetime(s1.get("日期"), errors="coerce"),
            pd.to_datetime(t_df.get("統一日期"), errors="coerce"),
            pd.to_datetime(o_df.get("統一日期"), errors="coerce"),
        ],
        ignore_index=True,
    ).dropna()
    if date_pool.empty:
        min_dt = max_dt = pd.Timestamp.today().normalize()
        year_opts: list[int] = []
        month_opts: list[str] = []
    else:
        min_dt = date_pool.min().normalize()
        max_dt = date_pool.max().normalize()
        year_opts = sorted(date_pool.dt.year.dropna().astype(int).unique().tolist())
        month_opts = sorted(date_pool.dt.strftime("%Y-%m").dropna().unique().tolist())

    branch_opts = _safe_option_list(s1.get("文本", pd.Series(dtype=str)))
    sales_opts = _safe_option_list(pd.concat([t_df.get(COL_SALESPERSON, pd.Series(dtype=str)), o_df.get(COL_SALESPERSON, pd.Series(dtype=str))], ignore_index=True))
    return {
        "ready": True,
        "year_opts": year_opts,
        "month_opts": month_opts,
        "min_dt": min_dt,
        "max_dt": max_dt,
        "branch_opts": branch_opts,
        "sales_opts": sales_opts,
    }


def _safe_multiselect_default(key: str, options: list) -> list:
    current = st.session_state.get(key, options)
    if not isinstance(current, list):
        current = list(current) if isinstance(current, (tuple, set)) else []
    return [value for value in current if value in options] or options


def _safe_date_default(key: str, min_dt: pd.Timestamp, max_dt: pd.Timestamp) -> tuple:
    current = st.session_state.get(key, (min_dt.date(), max_dt.date()))
    if not isinstance(current, tuple) or len(current) != 2:
        return (min_dt.date(), max_dt.date())
    return current


def _render_period_filter_form(
    *,
    title: str,
    help_text: str,
    form_key: str,
    year_key: str,
    month_key: str,
    date_key: str,
    reset_key: str,
    context: dict,
) -> tuple[list[int], list[str], tuple[pd.Timestamp, pd.Timestamp] | tuple]:
    if not context.get("ready"):
        st.info("目前尚無可篩選資料，請先上傳主副表。")
        return [], [], ()

    year_opts = context["year_opts"]
    month_opts = context["month_opts"]
    min_dt = context["min_dt"]
    max_dt = context["max_dt"]
    st.markdown(f"#### {title}")
    st.caption(help_text)
    year_default = _safe_multiselect_default(year_key, year_opts)
    month_default = _safe_multiselect_default(month_key, month_opts)
    with st.form(form_key, clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            year_sel = st.multiselect("年份", year_opts, default=year_default, key=year_key)
        with c2:
            month_sel = st.multiselect("月份", month_opts, default=month_default, key=month_key)
        date_default = _safe_date_default(date_key, min_dt, max_dt)
        date_rng = st.date_input(
            "日期範圍",
            value=date_default,
            min_value=min_dt.date(),
            max_value=max_dt.date(),
            key=date_key,
        )
        st.form_submit_button("套用篩選", use_container_width=True)

    if st.button("重設為全部", key=reset_key, use_container_width=True):
        st.session_state[year_key] = year_opts
        st.session_state[month_key] = month_opts
        st.session_state[date_key] = (min_dt.date(), max_dt.date())
        st.rerun()

    return (
        st.session_state.get(year_key, year_opts),
        st.session_state.get(month_key, month_opts),
        st.session_state.get(date_key, (min_dt.date(), max_dt.date())),
    )


def _render_kpi_filter_center(cache: dict) -> tuple[list[int], list[str], tuple[pd.Timestamp, pd.Timestamp] | tuple]:
    context = _filter_option_context(cache)
    return _render_period_filter_form(
        title="營運總覽與管理層 KPI 篩選",
        help_text="只影響上方 KPI 總覽，不影響門店排行榜、產品下鑽、AI Forecast 或 Export。",
        form_key="kpi_filter_form",
        year_key="KPI_YEAR_SEL",
        month_key="KPI_MONTH_SEL",
        date_key="KPI_DATE_RANGE",
        reset_key="KPI_FILTER_RESET",
        context=context,
    )


def _render_rank_filter_center(cache: dict) -> tuple[list[int], list[str], tuple[pd.Timestamp, pd.Timestamp] | tuple, str, str]:
    context = _filter_option_context(cache)
    if not context.get("ready"):
        st.info("目前尚無可篩選資料，請先上傳主副表。")
        return [], [], (), "全部分社", "全部銷售組"

    year_opts = context["year_opts"]
    month_opts = context["month_opts"]
    min_dt = context["min_dt"]
    max_dt = context["max_dt"]
    branch_opts = context["branch_opts"]
    sales_opts = context["sales_opts"]
    st.markdown("#### 門店與產品分析篩選")
    st.caption("只影響門店業績排行榜與產品佔比下鑽分析，不改變營運 KPI、年度總覽、AI Forecast 或 Export。")
    year_default = _safe_multiselect_default("RANK_YEAR_SEL", year_opts)
    month_default = _safe_multiselect_default("RANK_MONTH_SEL", month_opts)
    with st.form("rank_filter_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            year_sel = st.multiselect("年份", year_opts, default=year_default, key="RANK_YEAR_SEL")
        with c2:
            month_sel = st.multiselect("月份", month_opts, default=month_default, key="RANK_MONTH_SEL")
        date_default = _safe_date_default("RANK_DATE_RANGE", min_dt, max_dt)
        date_rng = st.date_input(
            "日期範圍",
            value=date_default,
            min_value=min_dt.date(),
            max_value=max_dt.date(),
            key="RANK_DATE_RANGE",
        )

        branch_current = st.session_state.get("RANK_BRANCH_SEL", "全部分社")
        branch_index = branch_opts.index(branch_current) + 1 if branch_current in branch_opts else 0
        branch_sel = st.selectbox("分社視角", ["全部分社"] + branch_opts, index=branch_index, key="RANK_BRANCH_SEL")

        sales_current = st.session_state.get("RANK_SALES_SEL", "全部銷售組")
        sales_index = sales_opts.index(sales_current) + 1 if sales_current in sales_opts else 0
        sales_sel = st.selectbox("專職視角", ["全部銷售組"] + sales_opts, index=sales_index, key="RANK_SALES_SEL")

        st.form_submit_button("套用篩選", use_container_width=True)

    if st.button("重設為全部", key="RANK_FILTER_RESET", use_container_width=True):
        st.session_state["RANK_YEAR_SEL"] = year_opts
        st.session_state["RANK_MONTH_SEL"] = month_opts
        st.session_state["RANK_DATE_RANGE"] = (min_dt.date(), max_dt.date())
        st.session_state["RANK_BRANCH_SEL"] = "全部分社"
        st.session_state["RANK_SALES_SEL"] = "全部銷售組"
        st.rerun()

    year_sel = st.session_state.get("RANK_YEAR_SEL", year_opts)
    month_sel = st.session_state.get("RANK_MONTH_SEL", month_opts)
    date_rng = st.session_state.get("RANK_DATE_RANGE", (min_dt.date(), max_dt.date()))
    branch_sel = st.session_state.get("RANK_BRANCH_SEL", "全部分社")
    sales_sel = st.session_state.get("RANK_SALES_SEL", "全部銷售組")
    return year_sel, month_sel, date_rng, branch_sel, sales_sel

def _governance_summary_value(summary_df: pd.DataFrame, view: str) -> tuple[str, str]:
    if summary_df.empty or "預測視角" not in summary_df.columns:
        return "未評估", "暫無治理資料"
    subset = summary_df[summary_df["預測視角"] == view]
    if subset.empty:
        return "未評估", "暫無治理資料"
    row = subset.iloc[0]
    score = row.get("治理分數")
    health = str(row.get("治理燈號", "未評估"))
    model = str(row.get("代表模型", "—"))
    if score is None or pd.isna(score):
        return health, model
    return f"{health} {float(score):.1f}", model

def _change_summary_value(change_summary: pd.DataFrame, metric: str):
    if change_summary.empty or "指標" not in change_summary.columns:
        return np.nan
    hit = change_summary[change_summary["指標"].eq(metric)]
    if hit.empty:
        return np.nan
    return hit.iloc[0].get("數值", np.nan)

def _ensure_export_workbooks(cache: dict) -> bool:
    cache_key = cache.get("export_cache_key")
    if not cache_key:
        return False
    export_payload = _load_export_runtime_cache(cache_key)
    if export_payload is None:
        export_payload = _compute_export_workbooks(cache.get("raw_t", pd.DataFrame()), cache.get("raw_o", pd.DataFrame()))
        if not _save_export_runtime_cache(cache_key, export_payload):
            return False

    cache["ex"] = export_payload.get("ex")
    cache["ex_no_writeoff"] = export_payload.get("ex_no_writeoff")
    cache["ex_no_writeoff_refund_transfer"] = export_payload.get("ex_no_writeoff_refund_transfer")
    cache["export_cache_status"] = "ready"
    cache["export_cache_path"] = str(_export_cache_path(cache_key))
    cache["export_cache_version"] = export_payload.get("export_cache_version") or EXPORT_CACHE_VERSION
    cache["official_export_schema"] = export_payload.get("official_export_schema") or OFFICIAL_EXPORT_SCHEMA_CONTRACT
    st.session_state["PROCESSED_DATA_CACHE"] = cache
    return all(cache.get(k) for k in ("ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer"))


__all__ = [name for name in globals() if not name.startswith("__")]
