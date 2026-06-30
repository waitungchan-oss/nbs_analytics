from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from backend.services.dashboard_analytics_service import build_dashboard_analytics
from backend.services.dashboard_service import build_dashboard_summary
from backend.services.data_quality_service import build_data_quality
from backend.services.forecast_read_service import build_forecast_read_model


def _to_frame(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return pd.DataFrame(value)
    if isinstance(value, dict):
        return pd.DataFrame([value])
    if value is None:
        return pd.DataFrame()
    return pd.DataFrame([{"value": value}])


def _write_sheet(writer: pd.ExcelWriter, sheet_name: str, value: Any) -> None:
    frame = _to_frame(value)
    frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)


def _flatten_mapping(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            rows.append({"key": key, "value": value, "type": "object"})
        elif isinstance(value, list):
            rows.append({"key": key, "value": f"{len(value)} rows", "type": "list"})
        else:
            rows.append({"key": key, "value": value, "type": type(value).__name__})
    return rows


def _write_mapping_sheet(writer: pd.ExcelWriter, sheet_name: str, mapping: dict[str, Any]) -> None:
    _write_sheet(writer, sheet_name, _flatten_mapping(mapping))


def build_dashboard_report_workbook(filters: dict | None = None) -> bytes:
    filters = filters or {}
    summary = build_dashboard_summary(filters)
    analytics = build_dashboard_analytics(filters)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _write_sheet(writer, "Dashboard KPIs", summary.get("kpis") or [])
        _write_mapping_sheet(writer, "Revenue Totals", summary.get("revenueTotals") or {})
        _write_mapping_sheet(writer, "Data Freshness", summary.get("dataFreshness") or {})
        _write_sheet(writer, "Branch Ranking", summary.get("branchRanking") or [])
        _write_sheet(writer, "Specialist Ranking", summary.get("specialistRanking") or [])
        _write_sheet(writer, "Product Mix", summary.get("productMix") or [])
        _write_sheet(writer, "Annual Summary", analytics.get("annualSummary") or [])
        _write_sheet(writer, "Monthly Trend", analytics.get("monthlyTrend") or [])
        _write_sheet(writer, "Product Drilldown Branch", (analytics.get("productDrilldown") or {}).get("branch") or [])
        _write_sheet(writer, "Product Drilldown Specialist", (analytics.get("productDrilldown") or {}).get("specialist") or [])
        _write_sheet(writer, "Reconciliation", analytics.get("reconciliation") or {})
        baseline = summary.get("stabilityBaseline") or {}
        _write_mapping_sheet(writer, "Stability Baseline", baseline)
        _write_mapping_sheet(writer, "Core Validation", baseline.get("coreValidation") or {})
        _write_mapping_sheet(writer, "Freshness Update", baseline.get("freshnessUpdate") or {})
    buf.seek(0)
    return buf.getvalue()


def build_quality_report_workbook() -> bytes:
    scorecard = build_data_quality()
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _write_sheet(writer, "Scorecard Overview", _flatten_mapping({k: v for k, v in scorecard.items() if k not in {"dimensions", "fieldCompleteness"}}))
        _write_sheet(writer, "Dimension Summary", scorecard.get("dimensions") or [])
        _write_sheet(writer, "Field Completeness", scorecard.get("fieldCompleteness") or [])
    buf.seek(0)
    return buf.getvalue()


def build_forecast_report_workbook() -> bytes:
    forecast = build_forecast_read_model()
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _write_sheet(writer, "Forecast Overview", _flatten_mapping({k: v for k, v in forecast.items() if k not in {"daily", "weights", "cache", "sevenDay", "monthEnd", "health"}}))
        _write_sheet(writer, "Daily Forecast", forecast.get("daily") or [])
        _write_sheet(writer, "7-Day Macro", forecast.get("sevenDay") or {})
        _write_sheet(writer, "Month-End Macro", forecast.get("monthEnd") or {})
        _write_sheet(writer, "Weight Schedule", forecast.get("weights") or [])
        _write_sheet(writer, "Model Health", forecast.get("health") or {})
        _write_sheet(writer, "Cache Snapshot", forecast.get("cache") or {})
    buf.seek(0)
    return buf.getvalue()
