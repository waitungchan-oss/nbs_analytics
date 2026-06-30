from __future__ import annotations

import pandas as pd

from database import load_all_data_from_db
from pipeline import build_dashboard_data
from backend.services.dashboard_service import _current_rules
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL, build_revenue_scope_frames

PRODUCT_COLUMNS = ("旅行團", "郵輪", "票務")


def _filtered(frame: pd.DataFrame, filters: dict, text_filter: str, all_value: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    dates = pd.to_datetime(work.get("日期"), errors="coerce")
    mask = pd.Series(True, index=work.index)
    years = filters.get("years") or []
    months = filters.get("months") or []
    date_range = filters.get("dateRange") or []
    if years:
        mask &= dates.dt.year.isin(years)
    if months:
        year_months = {str(value) for value in months if "-" in str(value)}
        month_numbers = {str(value).zfill(2) for value in months if "-" not in str(value)}
        month_mask = pd.Series(False, index=work.index)
        if year_months:
            month_mask |= dates.dt.strftime("%Y-%m").isin(year_months)
        if month_numbers:
            month_mask |= dates.dt.strftime("%m").isin(month_numbers)
        mask &= month_mask
    if len(date_range) == 2:
        start = pd.to_datetime(date_range[0], errors="coerce")
        end = pd.to_datetime(date_range[1], errors="coerce")
        if pd.notna(start) and pd.notna(end):
            mask &= dates.between(start, end)
    work = work.loc[mask].copy()
    if text_filter != all_value and "文本" in work.columns:
        work = work[work["文本"].astype(str).str.strip() == str(text_filter).strip()].copy()
    for column in PRODUCT_COLUMNS:
        work[column] = pd.to_numeric(work.get(column, 0), errors="coerce").fillna(0)
    work["_date"] = pd.to_datetime(work.get("日期"), errors="coerce")
    work["_total"] = work[list(PRODUCT_COLUMNS)].sum(axis=1)
    return work


def _ranking(frame: pd.DataFrame, label_key: str) -> list[dict]:
    if frame.empty or "文本" not in frame.columns:
        return []
    grouped = (
        frame.groupby("文本", as_index=False)[list(PRODUCT_COLUMNS)]
        .sum()
        .assign(totalRevenue=lambda value: value[list(PRODUCT_COLUMNS)].sum(axis=1))
        .sort_values("totalRevenue", ascending=False)
        .reset_index(drop=True)
    )
    total = float(grouped["totalRevenue"].sum())
    return [
        {
            "rank": index + 1,
            label_key: str(row["文本"]),
            "tourRevenue": float(row["旅行團"]),
            "cruiseRevenue": float(row["郵輪"]),
            "ticketRevenue": float(row["票務"]),
            "totalRevenue": float(row["totalRevenue"]),
            "sharePct": round(float(row["totalRevenue"]) / total * 100, 2) if total else 0.0,
        }
        for index, row in grouped.iterrows()
    ]


def _product_rows(frame: pd.DataFrame) -> list[dict]:
    values = {column: float(frame[column].sum()) if not frame.empty else 0.0 for column in PRODUCT_COLUMNS}
    total = sum(values.values())
    return [
        {
            "product": product,
            "revenue": revenue,
            "sharePct": round(revenue / total * 100, 2) if total else 0.0,
        }
        for product, revenue in values.items()
    ]


def _check(key: str, expected: float, actual: float) -> dict:
    delta = round(float(actual) - float(expected), 2)
    return {
        "key": key,
        "expected": float(expected),
        "actual": float(actual),
        "delta": delta,
        "status": "matched" if abs(delta) < 1 else "drift",
    }


def build_analytics_from_facts(branch_facts: pd.DataFrame, specialist_facts: pd.DataFrame, filters: dict) -> dict:
    branch = _filtered(
        branch_facts,
        filters,
        filters.get("branch", "全部分社"),
        "全部分社",
    )
    specialist = _filtered(
        specialist_facts,
        filters,
        filters.get("salesGroup", "全部銷售組"),
        "全部銷售組",
    )

    annual_rows = []
    years = sorted(
        set(branch["_date"].dropna().dt.year.astype(int).tolist())
        | set(specialist["_date"].dropna().dt.year.astype(int).tolist())
    )
    for year in years:
        branch_total = float(branch.loc[branch["_date"].dt.year == year, "_total"].sum())
        specialist_total = float(specialist.loc[specialist["_date"].dt.year == year, "_total"].sum())
        combined = branch_total + specialist_total
        annual_rows.append(
            {
                "year": year,
                "branchRevenue": branch_total,
                "specialistRevenue": specialist_total,
                "combinedRevenue": combined,
                "branchSharePct": round(branch_total / combined * 100, 2) if combined else 0.0,
                "specialistSharePct": round(specialist_total / combined * 100, 2) if combined else 0.0,
            }
        )

    def monthly(frame: pd.DataFrame) -> pd.Series:
        if frame.empty:
            return pd.Series(dtype=float)
        return frame.groupby(frame["_date"].dt.strftime("%Y-%m"))["_total"].sum()

    branch_monthly = monthly(branch)
    specialist_monthly = monthly(specialist)
    month_keys = sorted(set(branch_monthly.index) | set(specialist_monthly.index))
    monthly_rows = [
        {
            "month": month,
            "branchRevenue": float(branch_monthly.get(month, 0.0)),
            "specialistRevenue": float(specialist_monthly.get(month, 0.0)),
            "combinedRevenue": float(branch_monthly.get(month, 0.0) + specialist_monthly.get(month, 0.0)),
        }
        for month in month_keys
    ]

    branch_total = float(branch["_total"].sum())
    specialist_total = float(specialist["_total"].sum())
    combined_total = branch_total + specialist_total
    branch_ranking = _ranking(branch, "branch")
    specialist_ranking = _ranking(specialist, "specialist")
    branch_products = _product_rows(branch)
    specialist_products = _product_rows(specialist)
    checks = [
        _check("annualTotal", combined_total, sum(row["combinedRevenue"] for row in annual_rows)),
        _check("monthlyTotal", combined_total, sum(row["combinedRevenue"] for row in monthly_rows)),
        _check("branchRankingTotal", branch_total, sum(row["totalRevenue"] for row in branch_ranking)),
        _check("specialistRankingTotal", specialist_total, sum(row["totalRevenue"] for row in specialist_ranking)),
        _check("branchProductTotal", branch_total, sum(row["revenue"] for row in branch_products)),
        _check("specialistProductTotal", specialist_total, sum(row["revenue"] for row in specialist_products)),
    ]
    return {
        "annualSummary": annual_rows,
        "monthlyTrend": monthly_rows,
        "branchRanking": branch_ranking,
        "specialistRanking": specialist_ranking,
        "productDrilldown": {
            "branch": branch_products,
            "specialist": specialist_products,
        },
        "reconciliation": {
            "status": "matched" if all(check["status"] == "matched" for check in checks) else "drift",
            "combinedRevenue": combined_total,
            "checks": checks,
        },
    }


def build_dashboard_analytics(filters: dict) -> dict:
    db_tour, db_others = load_all_data_from_db()
    analysis_tour, analysis_others, _ = build_revenue_scope_frames(db_tour, db_others)
    branch_mapping, target_branches, cruise_depts, sales_reps = _current_rules()
    _, branch_facts, specialist_facts = build_dashboard_data(
        analysis_tour,
        analysis_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        make_workbook=False,
    )
    return {
        "appliedFilters": filters,
        "revenueScope": REVENUE_SCOPE_LABEL,
        **build_analytics_from_facts(branch_facts, specialist_facts, filters),
    }

