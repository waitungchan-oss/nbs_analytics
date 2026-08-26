from __future__ import annotations

import pandas as pd

from config import (
    COL_BRANCH,
    COL_DATE,
    COL_SALESPERSON,
)
from database import load_all_data_from_db
from pipeline import build_dashboard_data
from backend.services.business_rules_service import load_business_rules_snapshot
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL, build_revenue_scope_frames
from backend.services.stability_service import build_phase2c_stability_gate, build_stability_baseline


def _money_text(value: float) -> str:
    return f"HKD {float(value):,.0f}"


def _safe_option_list(series: pd.Series) -> list[str]:
    if series is None or series.empty:
        return []
    values = series.dropna().astype(str).str.replace("\u3000", " ", regex=False).str.strip()
    return sorted({value for value in values if value})


def _date_pool(*frames: pd.DataFrame) -> pd.Series:
    series_list = []
    for frame in frames:
        if not frame.empty and "統一日期" in frame.columns:
            series_list.append(pd.to_datetime(frame["統一日期"], errors="coerce"))
        elif not frame.empty and COL_DATE in frame.columns:
            series_list.append(pd.to_datetime(frame[COL_DATE], errors="coerce"))
    if not series_list:
        return pd.Series(dtype="datetime64[ns]")
    return pd.concat(series_list, ignore_index=True).dropna()


def _apply_filters(df: pd.DataFrame, date_col: str, years: list[int], months: list[str], date_range: list[str]) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df.copy()
    work = df.copy()
    dt = pd.to_datetime(work[date_col], errors="coerce")
    mask = pd.Series(True, index=work.index)
    if years:
        mask &= dt.dt.year.isin(years)
    if months:
        year_months = {str(month).strip() for month in months if "-" in str(month)}
        month_numbers = {
            str(month).strip().zfill(2)
            for month in months
            if str(month).strip() and "-" not in str(month)
        }
        month_mask = pd.Series(False, index=work.index)
        if year_months:
            month_mask |= dt.dt.strftime("%Y-%m").isin(year_months)
        if month_numbers:
            month_mask |= dt.dt.strftime("%m").isin(month_numbers)
        mask &= month_mask
    if len(date_range) == 2:
        start = pd.to_datetime(date_range[0], errors="coerce")
        end = pd.to_datetime(date_range[1], errors="coerce")
        if pd.notna(start) and pd.notna(end):
            mask &= dt.between(start, end)
    return work.loc[mask].copy()


def _current_rules() -> tuple[dict, list[str], list[str], list[str]]:
    rules = load_business_rules_snapshot().facts_kwargs()
    return (
        rules["branch_mapping"],
        rules["target_branches_s3"],
        rules["cruise_depts"],
        rules["sales_rep_list"],
    )


def build_dashboard_context(*, db_path=None) -> dict:
    db_tour, db_others = load_all_data_from_db(db_path=db_path)
    analysis_tour, analysis_others, _ = build_revenue_scope_frames(db_tour, db_others)
    branch_mapping, target_branches, cruise_depts, sales_reps = _current_rules()
    _, s1, _ = build_dashboard_data(
        analysis_tour,
        analysis_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        make_workbook=False,
    )
    dates = _date_pool(analysis_tour, analysis_others)
    months = sorted(dates.dt.strftime("%Y-%m").unique().tolist()) if not dates.empty else []
    years = sorted(dates.dt.year.astype(int).unique().tolist()) if not dates.empty else []
    max_date = None if dates.empty else str(dates.max().date())
    min_date = None if dates.empty else str(dates.min().date())
    sales_join = pd.concat(
        [
            analysis_tour.get(COL_SALESPERSON, pd.Series(dtype=str)),
            analysis_others.get(COL_SALESPERSON, pd.Series(dtype=str)),
        ],
        ignore_index=True,
    )
    return {
        "hasData": not db_tour.empty or not db_others.empty,
        "tourRows": int(len(db_tour)),
        "othersRows": int(len(db_others)),
        "maxDate": max_date,
        "minDate": min_date,
        "years": years,
        "months": months,
        "branches": _safe_option_list(s1.get("文本", pd.Series(dtype=str))),
        "salesGroups": _safe_option_list(sales_join),
        "revenueScope": REVENUE_SCOPE_LABEL,
    }


def _kpis(s1: pd.DataFrame, tour: pd.DataFrame, others: pd.DataFrame, filters: dict) -> list[dict]:
    years = filters.get("years", [])
    months = filters.get("months", [])
    date_range = filters.get("dateRange", [])
    branch = filters.get("branch", "全部分社")
    sales_group = filters.get("salesGroup", "全部銷售組")

    s1_f = _apply_filters(s1, "日期", years, months, date_range)
    tour_f = _apply_filters(tour, "統一日期", years, months, date_range)
    others_f = _apply_filters(others, "統一日期", years, months, date_range)

    if branch != "全部分社" and "文本" in s1_f.columns:
        s1_f = s1_f[s1_f["文本"].astype(str).str.strip() == str(branch).strip()].copy()
    if branch != "全部分社" and COL_BRANCH in tour_f.columns:
        tour_f = tour_f[tour_f[COL_BRANCH].astype(str).str.strip() == str(branch).strip()].copy()
    if branch != "全部分社" and COL_BRANCH in others_f.columns:
        others_f = others_f[others_f[COL_BRANCH].astype(str).str.strip() == str(branch).strip()].copy()

    if sales_group != "全部銷售組" and COL_SALESPERSON in tour_f.columns:
        tour_f = tour_f[tour_f[COL_SALESPERSON].astype(str).str.strip() == str(sales_group).strip()].copy()
    if sales_group != "全部銷售組" and COL_SALESPERSON in others_f.columns:
        others_f = others_f[others_f[COL_SALESPERSON].astype(str).str.strip() == str(sales_group).strip()].copy()

    tour_value = float(pd.to_numeric(s1_f.get("旅行團", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    cruise_value = float(pd.to_numeric(s1_f.get("郵輪", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    ticket_value = float(pd.to_numeric(s1_f.get("票務", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total = tour_value + cruise_value + ticket_value
    branch_count = int(s1_f["文本"].astype(str).nunique()) if "文本" in s1_f.columns else 0
    sales_join = pd.concat(
        [
            tour_f.get(COL_SALESPERSON, pd.Series(dtype=str)),
            others_f.get(COL_SALESPERSON, pd.Series(dtype=str)),
        ],
        ignore_index=True,
    )
    sales_count = int(sales_join.astype(str).str.strip().replace("", pd.NA).dropna().nunique())

    return [
        {
            "label": "淨營收",
            "value": _money_text(total),
            "delta": f"目前視角：{branch} / {sales_group}",
            "note": f"{REVENUE_SCOPE_LABEL}；含旅行團、郵輪與票務",
            "accent": "#118DFF",
        },
        {
            "label": "旅行團營收",
            "value": _money_text(tour_value),
            "delta": f"佔比 {tour_value / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"旅行團產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#12239E",
        },
        {
            "label": "郵輪營收",
            "value": _money_text(cruise_value),
            "delta": f"佔比 {cruise_value / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"郵輪產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#E66C37",
        },
        {
            "label": "票務營收",
            "value": _money_text(ticket_value),
            "delta": f"佔比 {ticket_value / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"票務產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#6B007B",
        },
        {
            "label": "可見分社 / 專員",
            "value": f"{branch_count} / {sales_count}",
            "delta": "以目前篩選條件計算",
            "note": "用來確認當前視角覆蓋範圍",
            "accent": "#197278",
        },
    ]


def _revenue_totals(s1: pd.DataFrame, s2: pd.DataFrame, filters: dict) -> dict:
    years = filters.get("years", [])
    months = filters.get("months", [])
    date_range = filters.get("dateRange", [])
    branch = filters.get("branch", "全部分社")
    sales_group = filters.get("salesGroup", "全部銷售組")

    branch_frame = _apply_filters(s1, "日期", years, months, date_range)
    specialist_frame = _apply_filters(s2, "日期", years, months, date_range)
    if branch != "全部分社" and "文本" in branch_frame.columns:
        branch_frame = branch_frame[branch_frame["文本"].astype(str).str.strip() == str(branch).strip()].copy()
        specialist_frame = specialist_frame.iloc[0:0].copy()
    if sales_group != "全部銷售組" and "文本" in specialist_frame.columns:
        specialist_frame = specialist_frame[specialist_frame["文本"].astype(str).str.strip() == str(sales_group).strip()].copy()

    def sum_products(frame: pd.DataFrame) -> float:
        if frame.empty:
            return 0.0
        total = 0.0
        for col in ["旅行團", "郵輪", "票務"]:
            total += float(pd.to_numeric(frame.get(col, pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        return total

    branch_revenue = sum_products(branch_frame)
    specialist_revenue = sum_products(specialist_frame)
    combined_revenue = branch_revenue + specialist_revenue
    return {
        "branchRevenue": float(branch_revenue),
        "specialistRevenue": float(specialist_revenue),
        "combinedRevenue": float(combined_revenue),
        "formattedCombinedRevenue": _money_text(combined_revenue),
        "scope": REVENUE_SCOPE_LABEL,
    }


def _ranking_from_summary_frame(
    frame: pd.DataFrame,
    filters: dict,
    text_key: str,
    output_key: str,
    filter_value: str,
    all_value: str,
) -> list[dict]:
    scoped = _apply_filters(frame, "日期", filters.get("years", []), filters.get("months", []), filters.get("dateRange", []))
    if filter_value != all_value and "文本" in scoped.columns:
        scoped = scoped[scoped["文本"].astype(str).str.strip() == str(filter_value).strip()].copy()
    if scoped.empty or "文本" not in scoped.columns:
        return []

    for col in ["旅行團", "郵輪", "票務"]:
        scoped[col] = pd.to_numeric(scoped.get(col, 0), errors="coerce").fillna(0)
    scoped["總額"] = scoped[["旅行團", "郵輪", "票務"]].sum(axis=1)
    grouped = (
        scoped.groupby("文本", as_index=False)[["旅行團", "郵輪", "票務", "總額"]]
        .sum()
        .sort_values("總額", ascending=False)
        .head(20)
    )
    total = float(grouped["總額"].sum()) if not grouped.empty else 0.0
    return [
        {
            "rank": idx + 1,
            output_key: str(row["文本"]),
            "tourRevenue": float(row["旅行團"]),
            "cruiseRevenue": float(row["郵輪"]),
            "ticketRevenue": float(row["票務"]),
            "totalRevenue": float(row["總額"]),
            "sharePct": round(float(row["總額"]) / total * 100, 2) if total > 0 else 0.0,
        }
        for idx, row in grouped.reset_index(drop=True).iterrows()
    ]


def _data_freshness(analysis_tour: pd.DataFrame, analysis_others: pd.DataFrame, scope_audit: dict) -> dict:
    dates = _date_pool(analysis_tour, analysis_others)
    return {
        "minDate": None if dates.empty else str(dates.min().date()),
        "maxDate": None if dates.empty else str(dates.max().date()),
        "rawRows": int(scope_audit.get("raw_rows", 0)),
        "analysisRows": int(scope_audit.get("analysis_rows", 0)),
        "excludedRows": int(scope_audit.get("excluded_rows", 0)),
        "scope": REVENUE_SCOPE_LABEL,
    }


def build_dashboard_summary(filters: dict, *, db_path=None, read_only: bool = False) -> dict:
    db_tour, db_others = load_all_data_from_db(db_path=db_path, read_only=read_only)
    analysis_tour, analysis_others, scope_audit = build_revenue_scope_frames(db_tour, db_others)
    branch_mapping, target_branches, cruise_depts, sales_reps = _current_rules()
    _, s1, s2 = build_dashboard_data(
        analysis_tour,
        analysis_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        make_workbook=False,
    )
    branch_ranking = _ranking_from_summary_frame(
        s1,
        filters,
        "branch",
        "branch",
        filters.get("branch", "全部分社"),
        "全部分社",
    )
    specialist_ranking = _ranking_from_summary_frame(
        s2,
        filters,
        "specialist",
        "specialist",
        filters.get("salesGroup", "全部銷售組"),
        "全部銷售組",
    )
    product_mix = s2.head(50).to_dict("records") if isinstance(s2, pd.DataFrame) else []
    revenue_totals = _revenue_totals(s1, s2, filters)
    data_freshness = _data_freshness(analysis_tour, analysis_others, scope_audit)
    return {
        "appliedFilters": filters,
        "revenueScope": REVENUE_SCOPE_LABEL,
        "scopeAudit": scope_audit,
        "kpis": _kpis(s1, analysis_tour, analysis_others, filters),
        "revenueTotals": revenue_totals,
        "dataFreshness": data_freshness,
        "stabilityBaseline": build_stability_baseline(revenue_totals, data_freshness),
        "branchRanking": branch_ranking,
        "specialistRanking": specialist_ranking,
        "productMix": product_mix,
        "exportReadiness": {
            "lazyExport": True,
            "status": "not_loaded",
            "message": "Phase 1 API does not prepare Excel workbooks.",
        },
    }
