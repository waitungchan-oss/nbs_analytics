import pandas as pd

from config import COL_MONEY, COL_ORDER_ID
from pipeline import normalize_runtime_columns

REVENUE_SCOPE_LABEL = "不含掛賬核銷與TT退款轉團款"
EXCLUDED_RECEIPT_TYPES = {"掛賬核銷"}
EXCLUDED_PAYMENT_METHODS = {"TT 退款轉團款"}


def _sum_money(df: pd.DataFrame) -> float:
    if df.empty or COL_MONEY not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[COL_MONEY], errors="coerce").fillna(0).sum())


def _collect_revenue_scope_excluded_ids(frames) -> set[str]:
    excluded_ids: set[str] = set()
    for frame in frames:
        if frame.empty or COL_ORDER_ID not in frame.columns:
            continue
        mask = pd.Series(False, index=frame.index)
        if "收款類型" in frame.columns:
            mask |= frame["收款類型"].astype(str).str.strip().isin(EXCLUDED_RECEIPT_TYPES)
        if "收款方式" in frame.columns:
            mask |= frame["收款方式"].astype(str).str.strip().isin(EXCLUDED_PAYMENT_METHODS)
        ids = frame.loc[mask, COL_ORDER_ID].dropna().astype(str).str.strip()
        excluded_ids.update(value for value in ids if value)
    return excluded_ids


def _drop_revenue_scope_excluded_ids(df: pd.DataFrame, excluded_ids: set[str]) -> pd.DataFrame:
    if df.empty or not excluded_ids or COL_ORDER_ID not in df.columns:
        return df.copy()
    ids = df[COL_ORDER_ID].astype(str).str.strip()
    return df.loc[~ids.isin(excluded_ids)].copy()


def build_revenue_scope_frames(db_tour: pd.DataFrame, db_others: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
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
