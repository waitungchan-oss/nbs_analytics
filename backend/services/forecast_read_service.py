from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path

import pandas as pd

from forecasting import build_macro_forecast_summary
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL

AI_CACHE_VERSION = "daily-macro-normal-tight-v1"


def _weight_schedule(report: dict | None) -> dict[int, dict]:
    weights = (report or {}).get("weights", pd.DataFrame())
    schedule = {}
    for _, row in weights.iterrows():
        try:
            horizon = int(str(row.get("權重版本", "")).split(" ")[0])
        except ValueError:
            continue
        values = {key: float(row.get(key, 0) or 0) for key in ("ARIMA", "Prophet", "LightGBM")}
        total = sum(values.values())
        if total:
            schedule[horizon] = {"strategy": str(row.get("推薦策略", "") or "總額模型"), **{key: value / total for key, value in values.items()}}
    return schedule


def _consensus(ar, pr, lgb, schedule):
    values = []
    for index, date in enumerate(ar.index):
        horizon = 1 if index < 1 else 7 if index < 7 else 30
        weights = schedule.get(horizon) or schedule.get(30) or schedule.get(7) or schedule.get(1) or {"ARIMA": 1 / 3, "Prophet": 1 / 3, "LightGBM": 1 / 3}
        values.append(float(ar.loc[date]) * weights["ARIMA"] + float(pr.loc[date]) * weights["Prophet"] + float(lgb.loc[date]) * weights["LightGBM"])
    return pd.Series(values, index=ar.index)


def _best_macro(report, layer):
    frame = (report or {}).get("summary", pd.DataFrame())
    if frame.empty or not {"聚合層級", "WAPE"}.issubset(frame.columns):
        return {"wape": None, "health": "未評估", "model": "—"}
    subset = frame[(frame["聚合層級"] == layer) & frame["WAPE"].notna()].sort_values("WAPE")
    if subset.empty:
        return {"wape": None, "health": "未評估", "model": "—"}
    row = subset.iloc[0]
    value = float(row["WAPE"])
    health = "優秀" if value < 10 else "可接受" if value < 20 else "可參考" if value < 30 else "需謹慎"
    return {"wape": value, "health": health, "model": f"{row.get('策略', '')} / {row.get('模型', '')}".strip(" /")}


def build_forecast_from_cache(cache_path: Path) -> dict:
    with cache_path.open("rb") as handle:
        wrapper = pickle.load(handle)
    if wrapper.get("version") != AI_CACHE_VERSION:
        raise ValueError(f"unsupported cache version: {wrapper.get('version')}")
    data = wrapper.get("data") or {}
    tracks = data.get("ptrk")
    if not tracks:
        raise ValueError(data.get("err") or "forecast tracks unavailable")
    ts, ar, pr, lgb = tracks
    schedule = _weight_schedule(data.get("bt"))
    consensus = _consensus(ar, pr, lgb, schedule)
    lower, upper = consensus * 0.85, consensus * 1.15
    daily = []
    for index, date in enumerate(consensus.index[:30]):
        bucket = 1 if index < 1 else 7 if index < 7 else 30
        weights = schedule.get(bucket) or {}
        daily.append({
            "date": pd.Timestamp(date).date().isoformat(), "weightVersion": f"{bucket}日",
            "strategy": weights.get("strategy", "平均權重"), "arima": round(float(ar.loc[date]), 2),
            "prophet": round(float(pr.loc[date]), 2), "lightgbm": round(float(lgb.loc[date]), 2),
            "consensus": round(float(consensus.loc[date]), 2), "lower": round(float(lower.loc[date]), 2),
            "upper": round(float(upper.loc[date]), 2),
        })
    macro = build_macro_forecast_summary(ts, ar, pr, lgb, consensus, lower, upper)
    seven = macro["seven_day"].iloc[0].to_dict() if not macro["seven_day"].empty else None
    month = macro["month_end"].iloc[0].to_dict() if not macro["month_end"].empty else None
    return {
        "status": "ready", "message": "", "scope": REVENUE_SCOPE_LABEL,
        "cache": {"path": str(cache_path), "modifiedAt": datetime.fromtimestamp(cache_path.stat().st_mtime).astimezone().isoformat(timespec="seconds"), "version": AI_CACHE_VERSION},
        "weights": [{"horizon": key, **value} for key, value in sorted(schedule.items())],
        "daily": daily,
        "sevenDay": None if seven is None else {"windowStart": seven["WindowStart"], "windowEnd": seven["WindowEnd"], "consensus": round(float(seven["Consensus (共識)"]), 2), "lower": round(float(seven["Lower"]), 2), "upper": round(float(seven["Upper"]), 2)},
        "monthEnd": None if month is None else {"month": month["Month"], "mtdActual": round(float(month["MTDActual"]), 2), "remainingDays": int(month["RemainingDays"]), "remainingPrediction": round(float(month["RemainingPrediction"]), 2), "consensus": round(float(month["MonthEnd Consensus"]), 2), "lower": round(float(month["Lower"]), 2), "upper": round(float(month["Upper"]), 2)},
        "health": {"sevenDay": _best_macro(data.get("bt_macro"), "7-Day Macro"), "monthEnd": _best_macro(data.get("bt_macro"), "Month-End Macro")},
    }


def build_forecast_read_model(cache_dir: Path = Path(".nbs_runtime_cache")) -> dict:
    candidates = sorted(cache_dir.glob("ai_*.pkl"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            return build_forecast_from_cache(path)
        except Exception:
            continue
    return {"status": "not_ready", "message": "No valid forecast cache is available.", "scope": REVENUE_SCOPE_LABEL, "cache": {}, "weights": [], "daily": [], "sevenDay": None, "monthEnd": None, "health": {}}

