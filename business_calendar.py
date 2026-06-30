"""香港業務日曆與旅遊展特徵生成。

此模組只負責把可審核日期資料轉成模型可用的靜態特徵；不訓練模型、不改預測邏輯。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CALENDAR_FILE = BASE_DIR / "data" / "business_calendar_events.json"


def load_business_calendar_events(path: str | Path = DEFAULT_CALENDAR_FILE) -> dict:
    """讀取本地可審核的香港假期與旅遊展資料。"""

    calendar_path = Path(path)
    with calendar_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _date_range(start_date: str, end_date: str) -> list[pd.Timestamp]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError(f"event end_date is earlier than start_date: {start_date} > {end_date}")
    return list(pd.date_range(start, end, freq="D"))


def _window_set(event_dates: Iterable[pd.Timestamp], before: int, after: int) -> set[pd.Timestamp]:
    dates = {pd.Timestamp(d).normalize() for d in event_dates}
    window: set[pd.Timestamp] = set()
    for event_date in dates:
        for delta in range(-before, after + 1):
            if delta == 0:
                continue
            window.add(event_date + pd.Timedelta(days=delta))
    return window - dates


def expand_business_calendar(events: dict | None = None) -> dict[str, object]:
    """把 JSON 事件展開為便於查詢的日期集合與每日事件明細。"""

    events = events or load_business_calendar_events()
    holiday_rows: list[dict] = []
    expo_rows: list[dict] = []

    for year_block in events.get("public_holidays", []):
        for item in year_block.get("events", []):
            date = pd.Timestamp(item["date"]).normalize()
            holiday_rows.append(
                {
                    "date": date,
                    "name": item["name"],
                    "year": int(year_block["year"]),
                    "source_name": year_block.get("source_name", ""),
                    "source_url": year_block.get("source_url", ""),
                }
            )

    for expo in events.get("travel_expos", []):
        for date in _date_range(expo["start_date"], expo["end_date"]):
            expo_rows.append(
                {
                    "date": date,
                    "name": expo["name"],
                    "event_type": expo.get("event_type", "travel_expo"),
                    "venue": expo.get("venue", ""),
                    "source_name": expo.get("source_name", ""),
                    "source_url": expo.get("source_url", ""),
                }
            )

    holidays = pd.DataFrame(holiday_rows)
    expos = pd.DataFrame(expo_rows)
    holiday_dates = set(holidays["date"]) if not holidays.empty else set()
    expo_dates = set(expos["date"]) if not expos.empty else set()
    return {
        "holidays": holidays,
        "expos": expos,
        "holiday_dates": holiday_dates,
        "expo_dates": expo_dates,
    }


def build_business_calendar_features(
    dates: Iterable,
    *,
    events: dict | None = None,
    holiday_window_days: int = 3,
    expo_window_days: int = 7,
) -> pd.DataFrame:
    """為日期序列生成香港假期、旅遊展與基礎業務日曆特徵。"""

    date_index = pd.to_datetime(pd.Series(list(dates)), errors="coerce").dropna().dt.normalize()
    if date_index.empty:
        return pd.DataFrame()

    calendar = expand_business_calendar(events)
    holiday_dates: set[pd.Timestamp] = calendar["holiday_dates"]  # type: ignore[assignment]
    expo_dates: set[pd.Timestamp] = calendar["expo_dates"]  # type: ignore[assignment]
    pre_post_holiday_dates = _window_set(holiday_dates, holiday_window_days, holiday_window_days)
    pre_post_expo_dates = _window_set(expo_dates, expo_window_days, expo_window_days)

    df = pd.DataFrame({"Date": date_index})
    df["weekday"] = df["Date"].dt.dayofweek
    df["is_weekend"] = df["weekday"].isin([5, 6]).astype(int)
    df["month"] = df["Date"].dt.month
    df["quarter"] = df["Date"].dt.quarter
    df["day_of_month"] = df["Date"].dt.day
    df["days_to_month_end"] = (df["Date"].dt.days_in_month - df["Date"].dt.day).astype(int)
    df["is_month_start_window"] = (df["day_of_month"] <= 3).astype(int)
    df["is_month_mid_window"] = df["day_of_month"].between(14, 16).astype(int)
    df["is_month_end_window"] = (df["days_to_month_end"] <= 3).astype(int)
    df["is_public_holiday"] = df["Date"].isin(holiday_dates).astype(int)
    df["is_near_public_holiday"] = df["Date"].isin(pre_post_holiday_dates).astype(int)
    df["is_travel_expo"] = df["Date"].isin(expo_dates).astype(int)
    df["is_near_travel_expo"] = df["Date"].isin(pre_post_expo_dates).astype(int)
    df["is_business_day"] = ((df["is_weekend"] == 0) & (df["is_public_holiday"] == 0)).astype(int)
    return df


def summarize_business_calendar(events: dict | None = None) -> pd.DataFrame:
    """輸出年度假期與旅遊展日數摘要，供驗證與報表顯示使用。"""

    calendar = expand_business_calendar(events)
    holidays: pd.DataFrame = calendar["holidays"]  # type: ignore[assignment]
    expos: pd.DataFrame = calendar["expos"]  # type: ignore[assignment]
    years = sorted(set(holidays["date"].dt.year.tolist()) | set(expos["date"].dt.year.tolist()))
    rows = []
    for year in years:
        holiday_days = holidays[holidays["date"].dt.year == year]["date"].nunique()
        expo_days = expos[expos["date"].dt.year == year]["date"].nunique()
        expo_events = expos[expos["date"].dt.year == year]["name"].nunique()
        rows.append(
            {
                "year": year,
                "public_holiday_days": int(holiday_days),
                "travel_expo_days": int(expo_days),
                "travel_expo_events": int(expo_events),
            }
        )
    return pd.DataFrame(rows)
