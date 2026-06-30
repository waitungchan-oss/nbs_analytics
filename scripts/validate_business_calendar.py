from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from business_calendar import (  # noqa: E402
    build_business_calendar_features,
    expand_business_calendar,
    load_business_calendar_events,
    summarize_business_calendar,
)


def _assert_hit(features: pd.DataFrame, date: str, column: str) -> None:
    target = pd.Timestamp(date)
    match = features[features["Date"] == target]
    if match.empty:
        raise AssertionError(f"Missing feature row for {date}")
    value = int(match.iloc[0][column])
    if value != 1:
        raise AssertionError(f"{column} should be 1 on {date}, got {value}")


def main() -> int:
    events = load_business_calendar_events()
    calendar = expand_business_calendar(events)
    holidays = calendar["holidays"]
    expos = calendar["expos"]

    if holidays.empty:
        raise AssertionError("No public holidays loaded")
    if expos.empty:
        raise AssertionError("No travel expos loaded")

    summary = summarize_business_calendar(events)
    if set(summary["year"]) != {2024, 2025, 2026}:
        raise AssertionError(f"Unexpected calendar years: {summary['year'].tolist()}")
    if not (summary["public_holiday_days"] == 17).all():
        raise AssertionError(f"Public holiday counts should be 17 per year:\n{summary}")

    feature_dates = pd.date_range("2024-01-01", "2026-12-31", freq="D")
    features = build_business_calendar_features(feature_dates, events=events)

    spot_checks = [
        ("2024-02-10", "is_public_holiday", "2024 Lunar New Year"),
        ("2024-03-29", "is_public_holiday", "2024 Good Friday"),
        ("2024-06-13", "is_travel_expo", "ITE Hong Kong 2024"),
        ("2024-09-27", "is_travel_expo", "Holiday & Travel Expo 2024 Autumn"),
        ("2025-01-29", "is_public_holiday", "2025 Lunar New Year"),
        ("2025-04-18", "is_public_holiday", "2025 Good Friday"),
        ("2025-06-12", "is_travel_expo", "ITE Hong Kong 2025"),
        ("2025-09-25", "is_travel_expo", "Holiday & Travel Expo 2025 Autumn"),
        ("2026-02-17", "is_public_holiday", "2026 Lunar New Year"),
        ("2026-04-03", "is_public_holiday", "2026 Good Friday"),
        ("2026-01-29", "is_travel_expo", "Holiday & Travel Expo 2026"),
        ("2026-06-11", "is_travel_expo", "ITE Hong Kong 2026"),
    ]
    for date, column, _label in spot_checks:
        _assert_hit(features, date, column)

    print("Business calendar validation passed.")
    print(summary.to_string(index=False))
    print("Holiday rows:", len(holidays))
    print("Travel expo day rows:", len(expos))
    print("Feature rows:", len(features))
    print("Spot checks:", len(spot_checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
