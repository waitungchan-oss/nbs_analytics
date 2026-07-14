from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from config import COL_BRANCH, COL_DATE, COL_MONEY, COL_ORDER_ID, COL_SALESPERSON, COL_TRANS_TIME
from database import load_all_data_from_db
from pipeline import clean_invoice_number, normalize_runtime_columns
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL, build_revenue_scope_frames


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_QUALITY_CACHE_DIR = PROJECT_ROOT / ".nbs_runtime_cache"
DATA_QUALITY_SERVICE_VERSION = "data-quality-v1"
DATA_QUALITY_CACHE_PREFIX = "data_quality_"


def _rate(numerator, denominator) -> float:
    return float(numerator or 0) / float(denominator) if float(denominator or 0) > 0 else 0.0


def _score(value) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _health(score: float) -> str:
    if score >= 90:
        return "優秀"
    if score >= 75:
        return "可接受"
    if score >= 60:
        return "需關注"
    return "需處理"


def _combine(*frames) -> pd.DataFrame:
    values = [normalize_runtime_columns(frame.copy()) for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    return pd.concat(values, ignore_index=True, sort=False) if values else pd.DataFrame()


def build_data_quality_from_frames(raw_tour, raw_others, analysis_tour, analysis_others, scope_audit) -> dict:
    raw = _combine(raw_tour, raw_others)
    analysis = _combine(analysis_tour, analysis_others)
    total = len(raw)
    dates = pd.to_datetime(raw.get(COL_DATE, pd.Series(dtype=object)), errors="coerce")
    valid_dates = dates.dropna()
    latest = valid_dates.max().date().isoformat() if not valid_dates.empty else None
    if valid_dates.empty:
        missing_days = 0
        date_score = 0.0
    else:
        expected = pd.date_range(valid_dates.min().normalize(), valid_dates.max().normalize(), freq="D")
        actual = pd.DatetimeIndex(valid_dates.dt.normalize().unique())
        missing_days = len(expected.difference(actual))
        date_score = _score(100 - _rate(total - len(valid_dates), total) * 80 - _rate(missing_days, len(expected)) * 20)

    required = [COL_ORDER_ID, COL_DATE, COL_MONEY, COL_BRANCH, COL_SALESPERSON, COL_TRANS_TIME, "來源報表標籤"]
    field_rows = []
    field_scores = []
    for column in required:
        if column not in raw.columns:
            field_rows.append({"field": column, "status": "not_applicable", "completeRows": None, "totalRows": total, "completeRate": None})
            continue
        series = raw[column]
        valid = pd.to_numeric(series, errors="coerce").notna() if column == COL_MONEY else series.notna() & series.astype(str).str.strip().ne("")
        rate = _rate(int(valid.sum()), total)
        field_scores.append(rate * 100)
        field_rows.append({"field": column, "status": "evaluated", "completeRows": int(valid.sum()), "totalRows": total, "completeRate": round(rate, 6)})
    field_score = _score(sum(field_scores) / len(field_scores)) if field_scores else 0.0

    ids = clean_invoice_number(raw.get(COL_ORDER_ID, pd.Series(dtype=object))).replace({"": pd.NA, "NAN": pd.NA}).dropna()
    duplicates = int(ids.duplicated(keep=False).sum())
    unmatched = int(raw.get("來源報表標籤", pd.Series("", index=raw.index)).astype(str).str.strip().eq("未匹配").sum())
    entity_score = _score(100 - _rate(unmatched, total) * 60 - _rate(duplicates, total) * 40)
    raw_amount = float(pd.to_numeric(raw.get(COL_MONEY, 0), errors="coerce").fillna(0).sum())
    excluded_rate = _rate(scope_audit.get("excluded_amount", 0), scope_audit.get("raw_amount", raw_amount))
    scope_score = _score(100 - min(excluded_rate / 0.25, 1.0) * 20)
    amount = pd.to_numeric(raw.get(COL_MONEY, pd.Series(dtype=float)), errors="coerce")
    missing_amount = int(amount.isna().sum())
    zero_negative = int(amount.fillna(0).le(0).sum())
    amount_score = _score(100 - _rate(missing_amount, total) * 50 - _rate(zero_negative, total) * 40)

    dimensions = [
        {"dimension": "Date Coverage", "score": date_score, "health": _health(date_score), "metric": f"最新日期 {latest or '不適用'} / 缺失日期 {missing_days}"},
        {"dimension": "Field Completeness", "score": field_score, "health": _health(field_score), "metric": "核心欄位完整率"},
        {"dimension": "Entity Resolution", "score": entity_score, "health": _health(entity_score), "metric": f"未匹配 {unmatched:,} / 重複 {duplicates:,}"},
        {"dimension": "Official Scope Health", "score": scope_score, "health": _health(scope_score), "metric": f"排除金額占比 {excluded_rate:.2%}"},
        {"dimension": "Amount Health", "score": amount_score, "health": _health(amount_score), "metric": f"零值或負值 {zero_negative:,}"},
    ]
    overall = _score(sum(row["score"] for row in dimensions) / len(dimensions))
    return {
        "status": "ready",
        "scope": REVENUE_SCOPE_LABEL,
        "overallScore": overall,
        "overallHealth": _health(overall),
        "latestDate": latest,
        "missingDays": int(missing_days),
        "unmatchedRows": unmatched,
        "excludedAmountRate": round(excluded_rate, 6),
        "rawRows": int(total),
        "officialRows": int(len(analysis)),
        "dimensions": dimensions,
        "fieldCompleteness": field_rows,
    }


def build_data_quality() -> dict:
    raw_tour, raw_others = load_all_data_from_db()
    analysis_tour, analysis_others, audit = build_revenue_scope_frames(raw_tour, raw_others)
    return build_data_quality_from_frames(raw_tour, raw_others, analysis_tour, analysis_others, audit)


def _data_quality_cache_key(generation_token: str) -> str:
    contract = {
        "serviceVersion": DATA_QUALITY_SERVICE_VERSION,
        "generationToken": str(generation_token),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _data_quality_cache_path(cache_dir: str | Path | None, cache_key: str) -> Path:
    directory = Path(cache_dir) if cache_dir is not None else DEFAULT_DATA_QUALITY_CACHE_DIR
    return directory / f"{DATA_QUALITY_CACHE_PREFIX}{cache_key}.json"


def _load_data_quality_cache(path: Path, cache_key: str, generation_token: str) -> dict | None:
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(wrapper, dict):
        return None
    if (
        wrapper.get("serviceVersion") != DATA_QUALITY_SERVICE_VERSION
        or wrapper.get("cacheKey") != cache_key
        or wrapper.get("generationToken") != str(generation_token)
        or not isinstance(wrapper.get("payload"), dict)
    ):
        return None
    return {
        **wrapper["payload"],
        "cacheStatus": "hit",
        "generationToken": str(generation_token),
    }


def _save_data_quality_cache(
    path: Path,
    cache_key: str,
    generation_token: str,
    payload: dict,
) -> None:
    wrapper = {
        "serviceVersion": DATA_QUALITY_SERVICE_VERSION,
        "cacheKey": cache_key,
        "generationToken": str(generation_token),
        "payload": payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(wrapper, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def build_data_quality_cached(
    *,
    db_path: str | Path,
    generation_token: str,
    cache_dir: str | Path | None = None,
) -> dict:
    cache_key = _data_quality_cache_key(generation_token)
    cache_path = _data_quality_cache_path(cache_dir, cache_key)
    cached = _load_data_quality_cache(cache_path, cache_key, generation_token)
    if cached is not None:
        return cached

    raw_tour, raw_others = load_all_data_from_db(db_path=Path(db_path))
    analysis_tour, analysis_others, audit = build_revenue_scope_frames(raw_tour, raw_others)
    payload = build_data_quality_from_frames(
        raw_tour,
        raw_others,
        analysis_tour,
        analysis_others,
        audit,
    )
    _save_data_quality_cache(cache_path, cache_key, generation_token, payload)
    return {
        **payload,
        "cacheStatus": "rebuilt",
        "generationToken": str(generation_token),
    }
