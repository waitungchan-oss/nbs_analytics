"""Prewarm or inspect NBS Analytics AI runtime cache.

This script intentionally mirrors the Streamlit app cache key rules without
importing app.py, so it can run from the terminal without starting Streamlit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    COL_BRANCH,
    COL_MONEY,
    COL_ORDER_ID,
    COL_QTY,
    COL_SALESPERSON,
    CONFIG_FILE,
    DB_FILE,
    load_business_rules,
)
from database import load_all_data_from_db  # noqa: E402
from forecasting import run_ai_backtest_report, run_ai_macro_backtest_report, run_ai_prediction_tracks  # noqa: E402
from pipeline import normalize_runtime_columns  # noqa: E402

REVENUE_SCOPE_LABEL = "不含掛賬核銷與TT退款轉團款"
REVENUE_SCOPE_EXCLUDED_RECEIPT_TYPES = ("掛賬核銷",)
REVENUE_SCOPE_EXCLUDED_PAYMENT_METHODS = ("TT 退款轉團款",)
AI_CACHE_VERSION = "daily-macro-normal-tight-v1"
AI_CACHE_DIR = PROJECT_ROOT / ".nbs_runtime_cache"


def _sum_money(df: pd.DataFrame) -> float:
    if df.empty or COL_MONEY not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[COL_MONEY], errors="coerce").fillna(0).sum())


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
    return {"exists": True, "path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


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


def _load_ai_cache(cache_key: str) -> dict | None:
    path = _ai_cache_path(cache_key)
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            payload = pickle.load(f)
        if payload.get("version") != AI_CACHE_VERSION:
            return None
        return payload.get("data")
    except Exception:
        return None


def _save_ai_cache(cache_key: str, data: dict) -> None:
    AI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _ai_cache_path(cache_key).open("wb") as f:
        pickle.dump({"version": AI_CACHE_VERSION, "data": data}, f, protocol=pickle.HIGHEST_PROTOCOL)


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


def _is_complete_payload(data: dict | None) -> bool:
    if not data:
        return False
    return all(data.get(key) is not None for key in ("ptrk", "bt", "bt_macro"))


def _payload_summary(data: dict | None) -> dict:
    if not data:
        return {"complete": False, "ptrk": False, "bt": False, "bt_macro": False}
    return {
        "complete": _is_complete_payload(data),
        "ptrk": data.get("ptrk") is not None,
        "bt": data.get("bt") is not None,
        "bt_macro": data.get("bt_macro") is not None,
        "err": data.get("err"),
        "bt_err": data.get("bt_err"),
        "bt_macro_err": data.get("bt_macro_err"),
    }


def _print_result(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or prewarm NBS Analytics AI cache.")
    parser.add_argument("--status", action="store_true", help="Only inspect cache status; do not rebuild.")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if cache exists.")
    args = parser.parse_args()

    started = time.perf_counter()
    _ = load_business_rules()
    db_tour, db_others = load_all_data_from_db()
    analysis_tour, analysis_others, scope_audit = _build_revenue_scope_frames(db_tour, db_others)
    cache_key = _ai_cache_key(scope_audit, analysis_tour, analysis_others)
    legacy_key = _legacy_ai_cache_key(scope_audit)
    data = None if args.force else _load_ai_cache(cache_key)
    status = "hit" if data is not None else "miss"
    source_key = cache_key if data is not None else None

    if data is None and not args.force and legacy_key != cache_key:
        legacy_data = _load_ai_cache(legacy_key)
        if legacy_data is not None:
            data = legacy_data
            status = "legacy_hit"
            source_key = legacy_key
            if not args.status:
                _save_ai_cache(cache_key, legacy_data)
                status = "legacy_hit_migrated"

    if args.status:
        _print_result(
            {
                "status": status,
                "cache_key": cache_key,
                "legacy_key": legacy_key,
                "source_key": source_key,
                "cache_path": str(_ai_cache_path(cache_key)),
                "legacy_cache_path": str(_ai_cache_path(legacy_key)),
                "scope": scope_audit,
                "payload": _payload_summary(data),
                "elapsed_sec": round(time.perf_counter() - started, 2),
            }
        )
        return 0 if data is not None else 1

    if data is None or args.force:
        data = _compute_ai_runtime_outputs(analysis_tour, analysis_others)
        _save_ai_cache(cache_key, data)
        status = "rebuilt"
        source_key = cache_key

    _print_result(
        {
            "status": status,
            "cache_key": cache_key,
            "legacy_key": legacy_key,
            "source_key": source_key,
            "cache_path": str(_ai_cache_path(cache_key)),
            "scope": scope_audit,
            "payload": _payload_summary(data),
            "elapsed_sec": round(time.perf_counter() - started, 2),
        }
    )
    return 0 if _is_complete_payload(data) else 2


if __name__ == "__main__":
    raise SystemExit(main())
