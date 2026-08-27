"""原始主副表讀取、清洗、模糊匹配與 14 張 Sheet 報表生成。"""

from __future__ import annotations

import difflib
import hashlib
import io
import itertools
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import (
    COL_BRANCH,
    COL_DATE,
    COL_DAYS,
    COL_DEPT,
    COL_DEST_CATEGORY,
    COL_MONEY,
    COL_ORDER_ID,
    COL_QTY,
    COL_RECEIPT_OPERATOR,
    COL_SALESPERSON,
    COL_SOURCE_TAG,
    COL_TOUR_NAME,
    COL_TRANS_TIME,
    COLS_TO_FETCH,
    DATE_COL_R,
    DATE_COL_Y,
    KEY_COL_1,
    KEY_COL_2,
    MONEY_COLS_1,
    MONEY_COLS_2,
    BRANCH_REASSIGNMENT_OVERRIDES,
    TARGET_DEPT_FOR_REP,
)

COL_SUBTABLE_BRANCH = "副表_銷售點"


@dataclass(frozen=True, slots=True)
class DashboardIntermediate:
    tour: pd.DataFrame
    others: pd.DataFrame
    branch_mapping: dict
    target_branches_s3: list[str]
    cruise_depts: list[str]
    sales_rep_list: list[str]
    scope_masks: dict[str, tuple[pd.Series, pd.Series]]
    source_fingerprint: str


def build_dashboard_intermediate(
    tour: pd.DataFrame,
    others: pd.DataFrame,
    *,
    branch_mapping: dict,
    target_branches_s3: list[str],
    cruise_depts: list[str],
    sales_rep_list: list[str],
) -> DashboardIntermediate:
    """Normalize dashboard inputs once before scope-specific aggregation."""
    normalized_tour = normalize_runtime_columns(tour.copy(deep=True))
    normalized_others = normalize_runtime_columns(others.copy(deep=True))
    for frame in (normalized_tour, normalized_others):
        if "統一日期" in frame.columns:
            frame["統一日期"] = pd.to_datetime(frame["統一日期"], errors="coerce").dt.strftime("%Y-%m-%d")

    def masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
        all_mask = pd.Series(True, index=frame.index, dtype=bool)
        no_writeoff = all_mask.copy()
        official = all_mask.copy()
        if "收款類型" in frame.columns:
            no_writeoff &= ~frame["收款類型"].astype(str).str.strip().eq("掛賬核銷")
        if "收款方式" in frame.columns:
            official &= ~frame["收款方式"].astype(str).str.strip().eq("TT 退款轉團款")
        official &= no_writeoff
        return {"all": all_mask, "no_writeoff": no_writeoff, "official": official}

    payload = pd.util.hash_pandas_object(normalized_tour, index=True).values.tobytes()
    payload += pd.util.hash_pandas_object(normalized_others, index=True).values.tobytes()
    tour_masks = masks(normalized_tour)
    others_masks = masks(normalized_others)
    return DashboardIntermediate(
        tour=normalized_tour,
        others=normalized_others,
        branch_mapping=dict(branch_mapping),
        target_branches_s3=list(target_branches_s3),
        cruise_depts=list(cruise_depts),
        sales_rep_list=list(sales_rep_list),
        scope_masks={
            scope: (tour_masks[scope], others_masks[scope])
            for scope in ("all", "no_writeoff", "official")
        },
        source_fingerprint=hashlib.sha256(payload).hexdigest(),
    )


def build_dashboard_data_from_intermediate(
    intermediate: DashboardIntermediate,
    *,
    scope_id: str,
    make_workbook: bool = False,
    include_branch_salesperson_sheet: bool = True,
    return_facts: bool = True,
):
    """Run the legacy-compatible report builder on prepared, scoped frames."""
    if scope_id not in intermediate.scope_masks:
        raise ValueError(f"unsupported dashboard scope: {scope_id}")
    tour_mask, others_mask = intermediate.scope_masks[scope_id]
    return build_dashboard_data(
        intermediate.tour.loc[tour_mask].copy(),
        intermediate.others.loc[others_mask].copy(),
        intermediate.branch_mapping,
        intermediate.target_branches_s3,
        intermediate.cruise_depts,
        intermediate.sales_rep_list,
        make_workbook=make_workbook,
        include_branch_salesperson_sheet=include_branch_salesperson_sheet,
        return_facts=return_facts,
        _already_normalized=True,
    )


def read_excel_source(source) -> tuple[pd.DataFrame, str]:
    if isinstance(source, tuple) and len(source) == 2 and isinstance(source[1], pd.DataFrame):
        name, frame = source
        return frame.copy(), str(name)
    if isinstance(source, pd.DataFrame):
        return source.copy(), str(getattr(source, "name", ""))
    return pd.read_excel(source, dtype=str), str(getattr(source, "name", ""))


_read_excel_source = read_excel_source


def clean_invoice_number(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(r"\.0$", "", regex=True)
    return s.str.replace("\xa0", "", regex=False).str.strip().str.upper()


def resolve_key_column(df: pd.DataFrame, preferred: str, fallback_index: int = 2) -> str:
    if preferred in df.columns:
        return preferred
    compact_cols = {str(col).replace(" ", "").replace("\u3000", ""): col for col in df.columns}
    candidates = [
        "來源單據號",
        "交易號碼",
        "交易号码",
        "來源單號",
        "單據號",
        "单据号",
        "訂單號",
        "订单号",
        "收款單號",
        "收款单号",
        "團代號",
        "团代号",
        "單號",
        "单号",
    ]
    for name in candidates:
        if name in compact_cols:
            return compact_cols[name]
    fuzzy_hits = [
        col
        for key, col in compact_cols.items()
        if ("單" in key or "单" in key or "號" in key or "号" in key or "交易" in key or "團" in key)
    ]
    if fuzzy_hits:
        return fuzzy_hits[0]
    if len(df.columns) > fallback_index:
        return df.columns[fallback_index]
    if len(df.columns) > 0:
        return df.columns[0]
    raise ValueError("Excel 檔案沒有任何欄位，無法辨識單據號。")


def format_money_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(r"[^\d\.-]", "", regex=True)
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def add_date_grouping(df: pd.DataFrame, date_col: str, new_col_name: str) -> pd.DataFrame:
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df[new_col_name] = df[date_col].dt.strftime("%Y-%m")
    return df


def get_branch_type(branch_text: Any) -> str:
    text = str(branch_text)
    if "分社" in text:
        return "門店"
    if "服務" in text:
        return "服務點"
    if "展覽" in text:
        return "展覽"
    return "其他"


def format_date_to_daily(date_series: pd.Series) -> pd.Series:
    return pd.to_datetime(date_series, errors="coerce").dt.strftime("%Y-%m-%d")


def ensure_numeric(df: pd.DataFrame, col_name: str) -> pd.DataFrame:
    if col_name in df.columns:
        df[col_name] = pd.to_numeric(df[col_name], errors="coerce").fillna(0)
    return df


def _format_date_with_fallback(source: pd.Series, fallback: pd.Series) -> pd.Series:
    """Format dates without relying on deprecated mixed-dtype fill behavior."""
    parsed = pd.to_datetime(source, errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
    return parsed.fillna(fallback.astype("string"))


def _fill_numeric_columns(df: pd.DataFrame, columns: list[str] | tuple[str, ...]) -> pd.DataFrame:
    """Fill only declared numeric columns, leaving text/null columns untouched."""
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)
    return df


def apply_fuzzy(val: Any, std_list: list[str], ctx: str, anomaly_log: list[dict]) -> str:
    val_str = str(val).strip()
    if pd.isna(val) or not val_str:
        return val
    if val_str in std_list:
        return val_str
    matches = difflib.get_close_matches(val_str, std_list, n=1, cutoff=0.6)
    if matches:
        anomaly_log.append(
            {"異常發生欄位": ctx, "原始異常值": val_str, "系統修正值": matches[0], "處理狀態": "✅ 自動模糊替換"}
        )
        return matches[0]
    anomaly_log.append({"異常發生欄位": ctx, "原始異常值": val_str, "系統修正值": "維持原值", "處理狀態": "⚠️ 無法匹配"})
    return val_str


def _compact_match_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).upper().replace("\u3000", " ").replace("\xa0", " ").strip()
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", text)


def _sales_rep_code(value: Any) -> str:
    text = _compact_match_text(value)
    match = re.match(r"^[A-Z0-9]+", text)
    return match.group(0) if match else ""


def match_sales_rep_by_operator(operator: Any, sales_rep_list: list[str]) -> str | None:
    """用收款操作員匹配標準專職銷售代表名單。"""
    operator_key = _compact_match_text(operator)
    operator_code = _sales_rep_code(operator)
    if not operator_key:
        return None

    rep_keys: dict[str, str] = {}
    rep_codes: dict[str, str] = {}
    for rep in sales_rep_list:
        rep_key = _compact_match_text(rep)
        rep_code = _sales_rep_code(rep)
        if rep_key:
            rep_keys[rep_key] = rep
        if rep_code:
            rep_codes[rep_code] = rep

    if operator_key in rep_keys:
        return rep_keys[operator_key]
    if operator_code and operator_code in rep_codes:
        return rep_codes[operator_code]

    for rep_key, rep in rep_keys.items():
        if operator_key.startswith(rep_key) or rep_key.startswith(operator_key):
            return rep

    candidates = list(rep_keys.keys()) + list(rep_codes.keys())
    matches = difflib.get_close_matches(operator_key, candidates, n=1, cutoff=0.82)
    if matches:
        return rep_keys.get(matches[0]) or rep_codes.get(matches[0])
    return None


def apply_operator_sales_rep_override(
    df: pd.DataFrame,
    sales_rep_list: list[str],
    anomaly_log: list[dict] | None = None,
) -> pd.DataFrame:
    """收款操作員若屬專職名單，優先歸入專職銷售組。"""
    if df.empty or COL_RECEIPT_OPERATOR not in df.columns:
        return df

    result = df.copy()
    if COL_BRANCH not in result.columns:
        result[COL_BRANCH] = ""
    if COL_SALESPERSON not in result.columns:
        result[COL_SALESPERSON] = ""

    for idx, operator in result[COL_RECEIPT_OPERATOR].items():
        matched_rep = match_sales_rep_by_operator(operator, sales_rep_list)
        if not matched_rep:
            continue

        old_branch = str(result.at[idx, COL_BRANCH]).strip()
        old_sales = str(result.at[idx, COL_SALESPERSON]).strip()
        if old_branch == TARGET_DEPT_FOR_REP and old_sales == matched_rep:
            continue

        result.at[idx, COL_BRANCH] = TARGET_DEPT_FOR_REP
        result.at[idx, COL_SALESPERSON] = matched_rep
        if anomaly_log is not None:
            anomaly_log.append(
                {
                    "異常發生欄位": COL_RECEIPT_OPERATOR,
                    "原始異常值": f"{operator} | 原銷售點={old_branch or '空白'} | 原銷售員={old_sales or '空白'}",
                    "系統修正值": f"{TARGET_DEPT_FOR_REP} / {matched_rep}",
                    "處理狀態": "✅ 收款操作員命中專職名單，已改歸專職銷售組",
                }
            )
    return result


def apply_subtable_branch_override(
    df: pd.DataFrame,
    anomaly_log: list[dict] | None = None,
) -> pd.DataFrame:
    """副表銷售點非空且與主表識別不一致時，優先用副表銷售點作一般分社歸屬。"""
    if df.empty or COL_SUBTABLE_BRANCH not in df.columns:
        return df

    result = df.copy()
    if COL_BRANCH not in result.columns:
        result[COL_BRANCH] = ""

    sub_branch = result[COL_SUBTABLE_BRANCH].fillna("").astype(str).str.replace("\u3000", " ").str.strip()
    current_branch = result[COL_BRANCH].fillna("").astype(str).str.replace("\u3000", " ").str.strip()
    mask = sub_branch.ne("") & current_branch.ne(sub_branch)

    for idx in result.index[mask]:
        old_branch = str(result.at[idx, COL_BRANCH]).replace("\u3000", " ").strip()
        new_branch = str(result.at[idx, COL_SUBTABLE_BRANCH]).replace("\u3000", " ").strip()
        source_id = str(result.at[idx, COL_ORDER_ID]).strip() if COL_ORDER_ID in result.columns else ""
        result.at[idx, COL_BRANCH] = new_branch
        if anomaly_log is not None:
            anomaly_log.append(
                {
                    "異常發生欄位": COL_SUBTABLE_BRANCH,
                    "原始異常值": f"來源單據號={source_id or '未知'} | 原銷售點={old_branch or '空白'} | 副表銷售點={new_branch}",
                    "系統修正值": new_branch,
                    "處理狀態": "✅ 副表銷售點與主表識別不一致，已改用副表銷售點",
                }
            )
    return result


def _normalize_branch_value(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _reassignment_period_mask(frame: pd.DataFrame, *, month: str = "", year: str = "") -> pd.Series:
    if not month and not year:
        return pd.Series(True, index=frame.index)
    for column in ("統一日期", DATE_COL_R, COL_DATE, DATE_COL_Y):
        if column in frame.columns:
            dates = pd.to_datetime(frame[column], errors="coerce")
            if dates.notna().any():
                if month:
                    return dates.dt.strftime("%Y-%m").eq(str(month))
                return dates.dt.strftime("%Y").eq(str(year))
    return pd.Series(False, index=frame.index)


def apply_branch_reassignment_overrides(
    df: pd.DataFrame,
    overrides: list[dict] | None = None,
    anomaly_log: list[dict] | None = None,
) -> pd.DataFrame:
    if df.empty or COL_BRANCH not in df.columns:
        return df

    result = df.copy()
    if COL_SUBTABLE_BRANCH not in result.columns:
        result[COL_SUBTABLE_BRANCH] = ""

    for override in overrides or []:
        to_branch = _normalize_branch_value(override.get("to_branch"))
        if not to_branch:
            continue
        from_branch = _normalize_branch_value(override.get("from_branch"))
        from_prefix = _normalize_branch_value(override.get("from_prefix")).upper()
        source_order_id = _normalize_branch_value(override.get("source_order_id")).upper()
        month = _normalize_branch_value(override.get("month"))
        year = _normalize_branch_value(override.get("year"))

        mask = _reassignment_period_mask(result, month=month, year=year)
        if from_branch:
            current_branch = result[COL_BRANCH].map(_normalize_branch_value)
            sub_branch = result[COL_SUBTABLE_BRANCH].map(_normalize_branch_value)
            mask &= current_branch.eq(from_branch) | sub_branch.eq(from_branch)
        if (from_prefix or source_order_id) and COL_ORDER_ID in result.columns:
            order_ids = result[COL_ORDER_ID].map(_normalize_branch_value).str.upper()
            if from_prefix:
                mask &= order_ids.str.startswith(from_prefix)
            if source_order_id:
                mask &= order_ids.eq(source_order_id)

        if not bool(mask.any()):
            continue

        for idx in result.index[mask]:
            old_branch = _normalize_branch_value(result.at[idx, COL_BRANCH])
            old_sub_branch = _normalize_branch_value(result.at[idx, COL_SUBTABLE_BRANCH])
            source_id = _normalize_branch_value(result.at[idx, COL_ORDER_ID]) if COL_ORDER_ID in result.columns else ""
            result.at[idx, COL_BRANCH] = to_branch
            result.at[idx, COL_SUBTABLE_BRANCH] = to_branch
            if anomaly_log is not None:
                anomaly_log.append(
                    {
                        "異常發生欄位": COL_BRANCH,
                        "原始異常值": f"來源單據號={source_id or '未知'} | 原銷售點={old_branch or '空白'} | 原副表銷售點={old_sub_branch or '空白'}",
                        "系統修正值": to_branch,
                        "處理狀態": "✅ 命中月份限定分社歸屬 override，已重派銷售點",
                    }
                )
    return result


def map_dest_category(row: pd.Series, cruise_depts: list[str]) -> str:
    dept = str(row.get(COL_DEPT, "")).strip()
    dest = str(row.get(COL_DEST_CATEGORY, "")).strip()
    if dept in cruise_depts:
        return "郵輪"
    if any(k in dest for k in ["海外", "台灣", "臺灣"]):
        return "海外"
    if any(k in dest for k in ["港", "澳"]):
        return "港澳"
    if "中國長線" in dest:
        return "中國長線"
    return "其他" if dest in ("", "nan", "None") else dest


def map_ticket_category(row: pd.Series):
    tour = str(row.get(COL_TOUR_NAME, "")).strip()
    tag = str(row.get(COL_SOURCE_TAG, "")).strip()
    if "未匹配" in tag:
        return None
    if "酒店" in tag:
        return "酒店"
    if "套票" in tag:
        return "套票"
    if "機票" in tour:
        return "機票"
    if "高鐵" in tour:
        return "高鐵"
    if any(kw in tour for kw in ["船票", "珠江船務"]):
        return "船票"
    if "巴士" in tour:
        return "巴士票"
    if any(kw in tag for kw in ["門券", "門票"]):
        return "其它門券"
    return None


def _coalesce_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for base_col, preference in {
        "銷售員": ["銷售員_x", "銷售員_y"],
        "團代號": ["團代號_y", "團代號_x"],
        "團名稱": ["團名稱_y", "團名稱_x"],
    }.items():
        if base_col not in result.columns:
            merged = pd.Series("", index=result.index, dtype=object)
            for candidate in preference:
                if candidate in result.columns:
                    merged = merged.where(merged.astype(str).str.strip().ne(""), result[candidate])
            result[base_col] = merged
        result.drop(columns=[c for c in preference if c in result.columns], inplace=True, errors="ignore")
    return result


def normalize_runtime_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = _coalesce_columns(df.copy())
    if "統一日期" not in result.columns:
        for candidate in [COL_DATE, COL_TRANS_TIME, DATE_COL_R, DATE_COL_Y]:
            if candidate in result.columns:
                result["統一日期"] = format_date_to_daily(result[candidate])
                break
    if "統一日期" not in result.columns:
        result["統一日期"] = pd.Series(dtype=object)
    defaults = {
        COL_BRANCH: "未知",
        COL_DEPT: "",
        COL_DEST_CATEGORY: "未分類",
        COL_SALESPERSON: "未知",
        COL_SOURCE_TAG: "",
        COL_TOUR_NAME: "",
        COL_TRANS_TIME: result.get("統一日期", ""),
        COL_DAYS: 0,
        COL_QTY: 0,
        COL_ORDER_ID: "",
        COL_MONEY: 0,
    }
    for col, default in defaults.items():
        if col not in result.columns:
            result[col] = default
    result[COL_MONEY] = pd.to_numeric(result[COL_MONEY], errors="coerce").fillna(0)
    result[COL_DAYS] = pd.to_numeric(result[COL_DAYS], errors="coerce").fillna(0)
    result[COL_QTY] = pd.to_numeric(result[COL_QTY], errors="coerce").fillna(0)
    return result


def _valid_entity_keys(series: pd.Series) -> pd.Series:
    return clean_invoice_number(series).replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA}).dropna()


def _id_cleaning_samples(df: pd.DataFrame, col: str, role: str, limit: int = 50) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=["資料表", "欄位", "清洗前", "清洗後"])
    raw = df[col].astype(str)
    cleaned = clean_invoice_number(df[col])
    changed = pd.DataFrame(
        {
            "資料表": role,
            "欄位": col,
            "清洗前": raw,
            "清洗後": cleaned,
        }
    )
    changed = changed[changed["清洗前"].str.strip() != changed["清洗後"].str.strip()]
    return changed.head(limit).reset_index(drop=True)


def _duplicate_entity_detail(df: pd.DataFrame, col: str, role: str) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=["資料表", "單號", "出現次數"])
    ids = _valid_entity_keys(df[col])
    counts = ids.value_counts()
    duplicate = counts[counts > 1].reset_index()
    duplicate.columns = ["單號", "出現次數"]
    duplicate.insert(0, "資料表", role)
    return duplicate


def _source_breakdown_from_merged(final_df: pd.DataFrame) -> pd.DataFrame:
    if final_df.empty:
        return pd.DataFrame(columns=["資料來源", "行數", "唯一來源單據號", "金額合計"])
    source = final_df.get("資料來源", pd.Series("未匹配", index=final_df.index)).fillna("未匹配").astype(str)
    work = final_df.copy()
    work["資料來源"] = source.replace({"": "未匹配"})
    amount = pd.to_numeric(work.get(COL_MONEY, 0), errors="coerce").fillna(0)
    work["_audit_amount"] = amount
    grouped = work.groupby("資料來源", dropna=False).agg(
        行數=(COL_ORDER_ID, "size"),
        唯一來源單據號=(COL_ORDER_ID, lambda s: _valid_entity_keys(s).nunique()),
        金額合計=("_audit_amount", "sum"),
    )
    return grouped.reset_index().sort_values(["行數", "資料來源"], ascending=[False, True])


def _build_entity_resolution_audit(
    df1_filtered: pd.DataFrame,
    df2_cleaned: pd.DataFrame,
    final_df: pd.DataFrame,
    col1_name: str,
    col2_name: str,
    id_cleaning_samples: pd.DataFrame,
) -> dict:
    main_ids = _valid_entity_keys(df1_filtered[col1_name]) if col1_name in df1_filtered.columns else pd.Series(dtype=object)
    secondary_ids = _valid_entity_keys(df2_cleaned[col2_name]) if col2_name in df2_cleaned.columns else pd.Series(dtype=object)
    main_unique = set(main_ids.astype(str))
    secondary_unique = set(secondary_ids.astype(str))
    matched_unique = main_unique & secondary_unique
    main_only = sorted(main_unique - secondary_unique)
    secondary_only = sorted(secondary_unique - main_unique)
    matched_rows = int((final_df.get("_merge", pd.Series(dtype=object)) == "both").sum()) if "_merge" in final_df.columns else 0
    main_unmatched_rows = int((final_df.get("_merge", pd.Series(dtype=object)) == "left_only").sum()) if "_merge" in final_df.columns else 0

    summary = pd.DataFrame(
        [
            {"指標": "主表有效來源單據號數", "數值": len(main_unique), "說明": "分社 prefix 與排除 prefix 規則後的主表唯一來源單據號。"},
            {"指標": "副表有效交易號碼數", "數值": len(secondary_unique), "說明": "旅行團與其他副表合併後的唯一交易號碼。"},
            {"指標": "成功匹配唯一單號數", "數值": len(matched_unique), "說明": "來源單據號與交易號碼完全匹配的唯一單號。"},
            {"指標": "成功匹配主表行數", "數值": matched_rows, "說明": "主表行層級 `_merge == both` 的筆數。"},
            {"指標": "主表未匹配行數", "數值": main_unmatched_rows, "說明": "主表存在但副表沒有補充資料；仍按正式收款口徑保留。"},
            {"指標": "副表未落主表單號數", "數值": len(secondary_only), "說明": "副表有交易號碼但主表沒有來源單據號，不進正式營收。"},
            {"指標": "主表重複單號筆數", "數值": int(main_ids.duplicated(keep=False).sum()), "說明": "主表中同一單號出現多筆。"},
            {"指標": "副表重複交易號碼筆數", "數值": int(secondary_ids.duplicated(keep=False).sum()), "說明": "副表中同一交易號碼出現多筆；merge 前會按交易號碼去重。"},
            {"指標": "匹配率", "數值": round(len(matched_unique) / len(main_unique), 4) if main_unique else 0, "說明": "成功匹配唯一單號數 / 主表有效來源單據號數。"},
        ]
    )

    main_only_detail = pd.DataFrame({"單號": main_only, "方向": "主表有 / 副表無", "處理": "正式營收保留，但副表資訊未補充"})
    if secondary_only:
        secondary_base = df2_cleaned[df2_cleaned[col2_name].astype(str).isin(secondary_only)].copy()
        source_cols = [c for c in [col2_name, "資料來源", DATE_COL_Y, COL_MONEY, COL_BRANCH, COL_SALESPERSON, COL_TOUR_NAME] if c in secondary_base.columns]
        secondary_only_detail = secondary_base[source_cols].drop_duplicates(subset=[col2_name]).rename(columns={col2_name: "單號"})
        secondary_only_detail.insert(1, "方向", "副表有 / 主表無")
        secondary_only_detail.insert(2, "處理", "副表-only，不進正式營收")
    else:
        secondary_only_detail = pd.DataFrame(columns=["單號", "方向", "處理"])
    unmatched_detail = pd.concat([main_only_detail, secondary_only_detail], ignore_index=True, sort=False)

    duplicate_detail = pd.concat(
        [
            _duplicate_entity_detail(df1_filtered, col1_name, "財務主表"),
            _duplicate_entity_detail(df2_cleaned, col2_name, "副表"),
        ],
        ignore_index=True,
        sort=False,
    )

    return {
        "summary": summary,
        "source_breakdown": _source_breakdown_from_merged(final_df),
        "duplicate_detail": duplicate_detail,
        "unmatched_detail": unmatched_detail,
        "id_cleaning_samples": id_cleaning_samples,
    }


def process_raw_files(
    main_file,
    tour_file,
    other_files,
    branch_mapping: dict,
    exclude_prefixes: list[str],
    sales_rep_list: list[str],
    return_entity_audit: bool = False,
    branch_reassignment_overrides: list[dict] | None = None,
):
    df1, _ = _read_excel_source(main_file)
    secondary_dfs = []

    if tour_file is not None:
        df_t, _ = _read_excel_source(tour_file)
        df_t["資料來源"] = "旅行團"
        secondary_dfs.append(df_t)

    for f in other_files:
        df_temp, source_name = _read_excel_source(f)
        df_temp["資料來源"] = source_name.replace("副表_", "").replace(".xlsx", "").replace(".xls", "")
        secondary_dfs.append(df_temp)

    if not secondary_dfs:
        raise ValueError("請至少提供旅行團副表或其它副表！")

    df2 = pd.concat(secondary_dfs, ignore_index=True)

    col1_name = resolve_key_column(df1, KEY_COL_1, fallback_index=2)
    main_clean_samples = _id_cleaning_samples(df1, col1_name, "財務主表")
    df1[col1_name] = clean_invoice_number(df1[col1_name])
    df1_filtered = df1[
        df1[col1_name].str.startswith(tuple(branch_mapping.keys()))
        & ~df1[col1_name].str.startswith(tuple(exclude_prefixes))
    ].copy()

    def get_branch(no):
        for k in sorted(branch_mapping.keys(), key=len, reverse=True):
            if str(no).startswith(k):
                return branch_mapping[k]
        return "未知"

    df1_filtered["銷售點"] = df1_filtered[col1_name].apply(get_branch)
    df1_filtered = add_date_grouping(df1_filtered, DATE_COL_R, "收款月份分組")
    if col1_name != "來源單據號":
        df1_filtered.rename(columns={col1_name: "來源單據號"}, inplace=True)

    col2_name = resolve_key_column(df2, KEY_COL_2, fallback_index=2)
    secondary_clean_samples = _id_cleaning_samples(df2, col2_name, "副表")
    df2[col2_name] = clean_invoice_number(df2[col2_name])
    df2_cleaned_for_audit = df2.copy()
    df2 = df2.drop_duplicates(subset=[col2_name])
    cols_to_ext = [col2_name] + [c for c in COLS_TO_FETCH if c in df2.columns and c != col2_name]
    if "資料來源" in df2.columns:
        cols_to_ext.append("資料來源")
    df2_subset = df2[cols_to_ext].copy().rename(columns={"銷售點": "副表_銷售點"})

    final_df = pd.merge(
        df1_filtered,
        df2_subset,
        left_on="來源單據號",
        right_on=col2_name,
        how="left",
        indicator=True,
    )
    final_df = _coalesce_columns(final_df)
    if col2_name in final_df.columns and "來源單據號" != col2_name:
        final_df.drop(columns=[col2_name], inplace=True)

    final_df = format_money_columns(add_date_grouping(final_df, DATE_COL_Y, "交易月份分組"), MONEY_COLS_1 + MONEY_COLS_2)
    for c in [COL_DAYS, COL_QTY]:
        if c in final_df.columns:
            final_df[c] = pd.to_numeric(final_df[c], errors="coerce").fillna(0)

    mask_tour = (final_df["_merge"] == "both") & (final_df["資料來源"] == "旅行團")
    df_tour_matched = final_df[mask_tour].drop(columns=["_merge", "資料來源"], errors="ignore")
    df_others_matched = final_df[~mask_tour].drop(columns=["_merge"], errors="ignore")

    if "資料來源" in df_others_matched.columns:
        df_others_matched["來源報表標籤"] = df_others_matched.pop("資料來源").fillna("未匹配")

    df_tour_matched = normalize_runtime_columns(df_tour_matched)
    df_others_matched = normalize_runtime_columns(df_others_matched)

    anomaly_log: list[dict] = []
    for df_t in [df_tour_matched, df_others_matched]:
        if COL_SALESPERSON in df_t.columns:
            df_t[COL_SALESPERSON] = df_t[COL_SALESPERSON].apply(
                lambda x: apply_fuzzy(x, sales_rep_list, COL_SALESPERSON, anomaly_log)
            )
    df_tour_matched = apply_subtable_branch_override(df_tour_matched, anomaly_log)
    df_others_matched = apply_subtable_branch_override(df_others_matched, anomaly_log)
    overrides = BRANCH_REASSIGNMENT_OVERRIDES if branch_reassignment_overrides is None else branch_reassignment_overrides
    df_tour_matched = apply_branch_reassignment_overrides(df_tour_matched, overrides, anomaly_log)
    df_others_matched = apply_branch_reassignment_overrides(df_others_matched, overrides, anomaly_log)
    df_tour_matched = apply_operator_sales_rep_override(df_tour_matched, sales_rep_list, anomaly_log)
    df_others_matched = apply_operator_sales_rep_override(df_others_matched, sales_rep_list, anomaly_log)
    df_anomaly_log = (
        pd.DataFrame(anomaly_log)
        if anomaly_log
        else pd.DataFrame(columns=["異常發生欄位", "原始異常值", "系統修正值", "處理狀態"])
    )
    if return_entity_audit:
        entity_audit = _build_entity_resolution_audit(
            df1_filtered=df1_filtered,
            df2_cleaned=df2_cleaned_for_audit,
            final_df=final_df,
            col1_name=COL_ORDER_ID,
            col2_name=col2_name,
            id_cleaning_samples=pd.concat([main_clean_samples, secondary_clean_samples], ignore_index=True, sort=False),
        )
        return df_tour_matched, df_others_matched, df_anomaly_log, entity_audit
    return df_tour_matched, df_others_matched, df_anomaly_log


def build_dashboard_data(
    df_tour_matched: pd.DataFrame,
    df_others_matched: pd.DataFrame,
    branch_mapping: dict,
    target_branches_s3: list[str],
    cruise_depts: list[str],
    sales_rep_list: list[str],
    make_workbook: bool = True,
    include_branch_salesperson_sheet: bool = False,
    return_facts: bool = False,
    _already_normalized: bool = False,
):
    if not _already_normalized:
        df_tour_matched = normalize_runtime_columns(df_tour_matched)
        df_others_matched = normalize_runtime_columns(df_others_matched)
        df_tour_matched["統一日期"] = pd.to_datetime(df_tour_matched["統一日期"], errors="coerce").dt.strftime("%Y-%m-%d")
        df_others_matched["統一日期"] = pd.to_datetime(df_others_matched["統一日期"], errors="coerce").dt.strftime("%Y-%m-%d")

    all_days = sorted(list(set(df_tour_matched["統一日期"].dropna()) | set(df_others_matched["統一日期"].dropna())))
    branch_list = [f"{c}{n}" for c, n in branch_mapping.items() if n != TARGET_DEPT_FOR_REP]

    def build_summary(df_t, df_o, text_list, text_col, is_branch=True):
        grid = pd.DataFrame(list(itertools.product(text_list, all_days)), columns=["文本", "日期"])
        grid["種類/單選"] = grid["文本"].apply(get_branch_type) if is_branch else "專職銷售"
        grid["MapKey"] = grid["文本"].apply(lambda x: str(x)[2:]) if is_branch else grid["文本"]

        t_not_c = df_t[~df_t[COL_DEPT].isin(cruise_depts)]
        t_c = df_t[df_t[COL_DEPT].isin(cruise_depts)]
        s_tour = t_not_c.groupby([text_col, "統一日期"])[COL_MONEY].sum().reset_index(name="旅行團")
        s_cruise = t_c.groupby([text_col, "統一日期"])[COL_MONEY].sum().reset_index(name="郵輪")
        s_tkt = df_o.groupby([text_col, "統一日期"])[COL_MONEY].sum().reset_index(name="票務")

        res = (
            grid.merge(s_tour, left_on=["MapKey", "日期"], right_on=[text_col, "統一日期"], how="left")
            .merge(s_cruise, left_on=["MapKey", "日期"], right_on=[text_col, "統一日期"], how="left")
            .merge(s_tkt, left_on=["MapKey", "日期"], right_on=[text_col, "統一日期"], how="left")
        )
        res["月份"] = pd.to_datetime(res["日期"], errors="coerce").dt.strftime("%Y-%m")
        col_type = "單選" if is_branch else "種類"
        return res.rename(columns={"種類/單選": col_type})[
            ["文本", col_type, "日期", "月份", "旅行團", "郵輪", "票務"]
        ].fillna(0)

    def build_branch_salesperson_summary(df_t, df_o):
        columns = ["文本", "單選", "銷售員", "日期", "月份", "旅行團", "郵輪", "票務", "旅行團交易人數", "票務交易數量"]
        branch_text_lookup = {str(text)[2:]: str(text) for text in branch_list}
        if not branch_text_lookup:
            return pd.DataFrame(columns=columns)

        def _prepared_branch_frame(df_sub: pd.DataFrame) -> pd.DataFrame:
            if df_sub.empty:
                return pd.DataFrame()
            work = df_sub.copy()
            if COL_BRANCH not in work.columns:
                return pd.DataFrame()
            if COL_SALESPERSON not in work.columns:
                work[COL_SALESPERSON] = "未指定"
            work = work[work[COL_BRANCH].isin(branch_text_lookup)]
            if work.empty:
                return pd.DataFrame()
            work[COL_SALESPERSON] = work[COL_SALESPERSON].fillna("").astype(str).str.strip().replace("", "未指定")
            return work

        def grouped_amounts(df_sub: pd.DataFrame, amount_col: str) -> pd.DataFrame:
            work = _prepared_branch_frame(df_sub)
            if work.empty:
                return pd.DataFrame(columns=[COL_BRANCH, COL_SALESPERSON, "統一日期", amount_col])
            work["_交易金額"] = pd.to_numeric(work[COL_MONEY], errors="coerce").fillna(0)
            return (
                work.groupby([COL_BRANCH, COL_SALESPERSON, "統一日期"], dropna=False)["_交易金額"]
                .sum()
                .reset_index(name=amount_col)
            )

        def grouped_tour_people(df_sub: pd.DataFrame) -> pd.DataFrame:
            work = _prepared_branch_frame(df_sub)
            if work.empty:
                return pd.DataFrame(columns=[COL_BRANCH, COL_SALESPERSON, "統一日期", "旅行團交易人數"])
            work = (
                work.sort_values(by=COL_TRANS_TIME, na_position="last").drop_duplicates(subset=[COL_ORDER_ID], keep="first")
                if COL_ORDER_ID in work.columns
                else work.copy()
            )
            work["統計日期"] = (
                _format_date_with_fallback(work[COL_TRANS_TIME], work["統一日期"])
                if COL_TRANS_TIME in work.columns
                else work["統一日期"]
            )
            work["旅行團交易人數"] = pd.to_numeric(work[COL_QTY], errors="coerce").fillna(0)
            return (
                work.groupby([COL_BRANCH, COL_SALESPERSON, "統計日期"], dropna=False)["旅行團交易人數"]
                .sum()
                .reset_index()
                .rename(columns={"統計日期": "統一日期"})
            )

        def grouped_ticket_quantity(df_sub: pd.DataFrame) -> pd.DataFrame:
            work = _prepared_branch_frame(df_sub)
            if work.empty:
                return pd.DataFrame(columns=[COL_BRANCH, COL_SALESPERSON, "統一日期", "票務交易數量"])
            work["統計日期"] = (
                _format_date_with_fallback(work[COL_TRANS_TIME], work["統一日期"])
                if COL_TRANS_TIME in work.columns
                else work["統一日期"]
            )
            work["票務種類"] = work.apply(map_ticket_category, axis=1)
            work = work[work["票務種類"].notnull()]
            if work.empty:
                return pd.DataFrame(columns=[COL_BRANCH, COL_SALESPERSON, "統一日期", "票務交易數量"])
            work["票務交易數量"] = pd.to_numeric(work[COL_QTY], errors="coerce").fillna(0)
            return (
                work.groupby([COL_BRANCH, COL_SALESPERSON, "統計日期"], dropna=False)["票務交易數量"]
                .sum()
                .reset_index()
                .rename(columns={"統計日期": "統一日期"})
            )

        t_not_c = df_t[~df_t[COL_DEPT].isin(cruise_depts)]
        t_c = df_t[df_t[COL_DEPT].isin(cruise_depts)]
        s_tour = grouped_amounts(t_not_c, "旅行團")
        s_cruise = grouped_amounts(t_c, "郵輪")
        s_tkt = grouped_amounts(df_o, "票務")
        s_tour_people = grouped_tour_people(df_t)
        s_ticket_qty = grouped_ticket_quantity(df_o)
        keys = [COL_BRANCH, COL_SALESPERSON, "統一日期"]
        res = (
            s_tour.merge(s_cruise, on=keys, how="outer")
            .merge(s_tkt, on=keys, how="outer")
            .merge(s_tour_people, on=keys, how="outer")
            .merge(s_ticket_qty, on=keys, how="outer")
        )
        if res.empty:
            return pd.DataFrame(columns=columns)
        res["文本"] = res[COL_BRANCH].map(branch_text_lookup)
        res["單選"] = res["文本"].apply(get_branch_type)
        res["銷售員"] = res[COL_SALESPERSON]
        res["日期"] = res["統一日期"]
        res["月份"] = pd.to_datetime(res["日期"], errors="coerce").dt.strftime("%Y-%m")
        for amount_col in ["旅行團", "郵輪", "票務", "旅行團交易人數", "票務交易數量"]:
            res[amount_col] = pd.to_numeric(res[amount_col], errors="coerce").fillna(0)
        res = res[
            (res["旅行團"] != 0)
            | (res["郵輪"] != 0)
            | (res["票務"] != 0)
            | (res["旅行團交易人數"] != 0)
            | (res["票務交易數量"] != 0)
        ]
        return res[columns].sort_values(["文本", "銷售員", "日期"]).reset_index(drop=True)

    result_s1 = build_summary(
        df_tour_matched[df_tour_matched[COL_BRANCH] != TARGET_DEPT_FOR_REP],
        df_others_matched[df_others_matched[COL_BRANCH] != TARGET_DEPT_FOR_REP],
        branch_list,
        COL_BRANCH,
        True,
    )
    result_s1_salesperson = (
        build_branch_salesperson_summary(
            df_tour_matched[df_tour_matched[COL_BRANCH] != TARGET_DEPT_FOR_REP],
            df_others_matched[df_others_matched[COL_BRANCH] != TARGET_DEPT_FOR_REP],
        )
        if include_branch_salesperson_sheet
        else pd.DataFrame(columns=["文本", "單選", "銷售員", "日期", "月份", "旅行團", "郵輪", "票務", "旅行團交易人數", "票務交易數量"])
    )
    result_s2 = build_summary(
        df_tour_matched[df_tour_matched[COL_BRANCH] == TARGET_DEPT_FOR_REP],
        df_others_matched[df_others_matched[COL_BRANCH] == TARGET_DEPT_FOR_REP],
        sales_rep_list,
        COL_SALESPERSON,
        False,
    )

    df_tour_dedup = (
        df_tour_matched.sort_values(by=COL_TRANS_TIME, na_position="last").drop_duplicates(subset=[COL_ORDER_ID], keep="first")
        if COL_ORDER_ID in df_tour_matched.columns
        else df_tour_matched.copy()
    )
    df_tour_dedup["日期"] = (
        _format_date_with_fallback(df_tour_dedup[COL_TRANS_TIME], df_tour_dedup["統一日期"])
        if COL_TRANS_TIME in df_tour_dedup.columns
        else df_tour_dedup["統一日期"]
    )
    df_tour_dedup["文本"] = df_tour_dedup.apply(lambda r: map_dest_category(r, cruise_depts), axis=1)
    df_tour_dedup["天數_num"] = pd.to_numeric(df_tour_dedup[COL_DAYS], errors="coerce").fillna(0)
    df_tour_dedup["交易人數"] = pd.to_numeric(df_tour_dedup[COL_QTY], errors="coerce").fillna(0)
    df_tour_dedup["月份"] = pd.to_datetime(df_tour_dedup["日期"], errors="coerce").dt.strftime("%Y-%m")

    def gen_t_stats(df_sub):
        if df_sub.empty:
            return pd.DataFrame(columns=["文本", "天數", "日期", "月份", "交易人數"])
        s = df_sub.groupby(["文本", "天數_num", "日期"])["交易人數"].sum().reset_index()
        s["天數"] = s["天數_num"].apply(lambda x: str(int(x)) if x == int(x) else str(x))
        s["月份"] = pd.to_datetime(s["日期"], errors="coerce").dt.strftime("%Y-%m")
        return s[s["交易人數"] > 0].sort_values(["文本", "日期", "天數_num"])[["文本", "天數", "日期", "月份", "交易人數"]]

    result_s3 = gen_t_stats(df_tour_dedup[df_tour_dedup[COL_BRANCH].isin(target_branches_s3)])
    result_s4 = gen_t_stats(df_tour_dedup[df_tour_dedup[COL_BRANCH] == TARGET_DEPT_FOR_REP])

    df_ticket = df_others_matched.copy()
    df_ticket["日期"] = (
        _format_date_with_fallback(df_ticket[COL_TRANS_TIME], df_ticket["統一日期"])
        if COL_TRANS_TIME in df_ticket.columns
        else df_ticket["統一日期"]
    )
    df_ticket["月份"] = pd.to_datetime(df_ticket["日期"], errors="coerce").dt.strftime("%Y-%m")
    df_ticket["交易數量"] = pd.to_numeric(df_ticket[COL_QTY], errors="coerce").fillna(0)
    df_ticket["文本"] = df_ticket.apply(map_ticket_category, axis=1)
    df_ticket = df_ticket[df_ticket["文本"].notnull()]

    def gen_tk_stats(df_sub):
        if df_sub.empty:
            return pd.DataFrame(columns=["文本", "日期", "月份", "交易數量"])
        s = df_sub.groupby(["文本", "日期", "月份"])["交易數量"].sum().reset_index()
        s["文本"] = pd.Categorical(s["文本"], categories=["船票", "巴士票", "機票", "高鐵", "其它門券", "套票", "酒店"], ordered=True)
        return s[s["交易數量"] > 0].sort_values(["文本", "日期"])[["文本", "日期", "月份", "交易數量"]]

    result_s5 = gen_tk_stats(df_ticket[df_ticket[COL_BRANCH].isin(target_branches_s3)])
    result_s6 = gen_tk_stats(df_ticket[df_ticket[COL_BRANCH] == TARGET_DEPT_FOR_REP])
    result_s7 = gen_tk_stats(df_ticket)

    def gen_d_tour(df_sub, grp_col, t_name):
        if df_sub.empty:
            return pd.DataFrame(columns=["文本", "日期", "月份", t_name, "郵輪交易人數"])
        t = df_sub[df_sub["文本"] != "郵輪"].groupby([grp_col, "日期", "月份"])["交易人數"].sum().reset_index(name=t_name)
        c = df_sub[df_sub["文本"] == "郵輪"].groupby([grp_col, "日期", "月份"])["交易人數"].sum().reset_index(name="郵輪交易人數")
        res = df_sub[[grp_col, "日期", "月份"]].drop_duplicates().merge(t, how="left").merge(c, how="left")
        _fill_numeric_columns(res, (t_name, "郵輪交易人數"))
        return res[(res[t_name] > 0) | (res["郵輪交易人數"] > 0)].rename(columns={grp_col: "文本"}).sort_values(["文本", "日期"])

    result_s8 = gen_d_tour(df_tour_dedup[df_tour_dedup[COL_BRANCH].isin(target_branches_s3)], COL_BRANCH, "交易人數")
    result_s9 = gen_d_tour(df_tour_dedup[df_tour_dedup[COL_BRANCH] == TARGET_DEPT_FOR_REP], COL_SALESPERSON, "旅行團交易人數")

    def gen_d_tkt(df_sub, grp_col):
        if df_sub.empty:
            return pd.DataFrame(columns=["文本", "種類", "日期", "月份", "交易數量"])
        s = df_sub.groupby([grp_col, "文本", "日期", "月份"])["交易數量"].sum().reset_index()
        s = s[s["交易數量"] > 0].rename(columns={grp_col: "文本", "文本": "種類"})
        s["種類"] = pd.Categorical(s["種類"], categories=["其它門券", "巴士票", "船票", "高鐵", "機票", "酒店", "套票"], ordered=True)
        return s.sort_values(["文本", "種類", "日期"])

    result_s10 = gen_d_tkt(df_ticket[df_ticket[COL_BRANCH].isin(target_branches_s3)], COL_BRANCH)
    result_s11 = gen_d_tkt(df_ticket[df_ticket[COL_BRANCH] == TARGET_DEPT_FOR_REP], COL_SALESPERSON)

    def gen_mny(df_sub, grp_col, type_col):
        if df_sub.empty:
            return pd.DataFrame(columns=[grp_col, type_col, "天數", "日期", "月份", "交易金額"])
        df_m = df_sub.copy()
        df_m["日期"] = (
            _format_date_with_fallback(df_m[COL_TRANS_TIME], df_m["統一日期"])
            if COL_TRANS_TIME in df_m.columns
            else df_m["統一日期"]
        )
        df_m["月份"] = pd.to_datetime(df_m["日期"], errors="coerce").dt.strftime("%Y-%m")
        df_m["天數_num"] = pd.to_numeric(df_m[COL_DAYS], errors="coerce").fillna(0)
        df_m["天數"] = df_m["天數_num"].apply(lambda x: str(int(x)) if x == int(x) else str(x))
        df_m["交易金額"] = pd.to_numeric(df_m[COL_MONEY], errors="coerce").fillna(0)
        s = df_m.groupby([grp_col, type_col, "天數", "日期", "月份"])["交易金額"].sum().reset_index()
        return s[s["交易金額"] != 0].sort_values([grp_col, "日期"])

    df_tm = df_tour_matched[df_tour_matched[COL_BRANCH].isin(target_branches_s3)].copy()
    if not df_tm.empty:
        df_tm["線路種類"] = df_tm.apply(lambda r: map_dest_category(r, cruise_depts), axis=1)
    result_s12 = gen_mny(df_tm, COL_BRANCH, "線路種類").rename(columns={COL_BRANCH: "分社種類"})

    df_tkm = df_ticket[df_ticket[COL_BRANCH].isin(target_branches_s3)]
    result_s13 = gen_mny(df_tkm, COL_BRANCH, "文本").rename(columns={COL_BRANCH: "分社名稱", "文本": "票務種類"})

    m1 = pd.melt(
        result_s1,
        id_vars=["文本", "單選", "日期", "月份"],
        value_vars=["旅行團", "郵輪", "票務"],
        var_name="業務板塊",
        value_name="交易金額",
    ).rename(columns={"文本": "實體名稱", "單選": "實體類型"})
    m2 = pd.melt(
        result_s2,
        id_vars=["文本", "種類", "日期", "月份"],
        value_vars=["旅行團", "郵輪", "票務"],
        var_name="業務板塊",
        value_name="交易金額",
    ).rename(columns={"文本": "實體名稱", "種類": "實體類型"})
    df_tour_count_daily = df_tour_dedup.copy()
    df_tour_count_daily["日期"] = df_tour_count_daily["統一日期"]
    df_tour_count_daily["月份"] = pd.to_datetime(df_tour_count_daily["日期"], errors="coerce").dt.strftime("%Y-%m")

    df_tour_amount = df_tour_matched.copy()
    df_tour_amount["日期"] = df_tour_amount["統一日期"]
    df_tour_amount["文本"] = df_tour_amount.apply(lambda r: map_dest_category(r, cruise_depts), axis=1)
    df_tour_amount["月份"] = pd.to_datetime(df_tour_amount["日期"], errors="coerce").dt.strftime("%Y-%m")
    df_tour_amount[COL_MONEY] = pd.to_numeric(df_tour_amount[COL_MONEY], errors="coerce").fillna(0)

    def gen_route_type_daily(
        count_df: pd.DataFrame,
        amount_df: pd.DataFrame,
        grp_col: str,
        grp_name: str,
    ) -> pd.DataFrame:
        if count_df.empty and amount_df.empty:
            return pd.DataFrame(columns=[grp_name, "線路種類", "日子", "月份", "交易人數", "交易金額"])

        if count_df.empty:
            count_s = pd.DataFrame(columns=[grp_col, "文本", "日期", "月份", "交易人數"])
        else:
            count_s = (
                count_df.groupby([grp_col, "文本", "日期", "月份"], dropna=False)["交易人數"]
                .sum()
                .reset_index()
            )

        if amount_df.empty:
            amount_s = pd.DataFrame(columns=[grp_col, "文本", "日期", "月份", "交易金額"])
        else:
            amount_s = (
                amount_df.groupby([grp_col, "文本", "日期", "月份"], dropna=False)[COL_MONEY]
                .sum()
                .reset_index(name="交易金額")
            )

        s = (
            count_s.merge(amount_s, on=[grp_col, "文本", "日期", "月份"], how="outer")
            if not count_s.empty or not amount_s.empty
            else pd.DataFrame(columns=[grp_col, "文本", "日期", "月份", "交易人數", "交易金額"])
        )
        s = s.rename(columns={grp_col: grp_name, "文本": "線路種類", "日期": "日子"})
        s["交易人數"] = pd.to_numeric(s.get("交易人數", 0), errors="coerce").fillna(0)
        s["交易金額"] = pd.to_numeric(s.get("交易金額", 0), errors="coerce").fillna(0)
        s = s[(s["交易人數"] != 0) | (s["交易金額"] != 0)]
        return s[[grp_name, "線路種類", "日子", "月份", "交易人數", "交易金額"]].sort_values([grp_name, "線路種類", "日子"])

    result_s15 = gen_route_type_daily(
        df_tour_count_daily[df_tour_count_daily[COL_BRANCH].isin(target_branches_s3)],
        df_tour_amount[df_tour_amount[COL_BRANCH].isin(target_branches_s3)],
        COL_BRANCH,
        "分社",
    )
    result_s16 = gen_route_type_daily(
        df_tour_count_daily[
            (df_tour_count_daily[COL_BRANCH] == TARGET_DEPT_FOR_REP)
            & df_tour_count_daily[COL_SALESPERSON].isin(sales_rep_list)
        ],
        df_tour_amount[(df_tour_amount[COL_BRANCH] == TARGET_DEPT_FOR_REP) & df_tour_amount[COL_SALESPERSON].isin(sales_rep_list)],
        COL_SALESPERSON,
        "專職銷售員",
    )

    def _project_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
        projected = df.copy()
        for col in columns:
            if col not in projected.columns:
                projected[col] = ""
        projected = projected[columns]
        return projected.fillna("")

    export_schema = [
        "收款單號",
        "收款單狀態",
        "來源單據號",
        "銷售點",
        "匯總單號",
        "收款状态",
        "原幣幣種",
        "匯率",
        "收款原幣金額",
        "收款本幣金額",
        "收款類型",
        "收款方式",
        "收款流水號",
        "收款操作員",
        "客戶名稱",
        "銷售公司",
        "銷售員",
        "收款時間",
        "收款月份分組",
        "團代號",
        "團名稱",
        "行程天數",
        "數量",
        "幣種",
        "應收",
        "已收",
        "交易時間",
        "交易月份分組",
        "團負責人",
        "團負責人部門",
        "目的地大類",
        "一級目的地",
        "二級目的地",
        "目的地名稱",
        "副表_銷售點",
        "來源報表標籤",
    ]

    export_aliases = {
        "收款單號": ["收款單號", "來源單據號", "匯總單號"],
        "收款單狀態": ["收款單狀態", "收款状态"],
        "來源單據號": ["來源單據號", "收款單號"],
        "銷售點": ["銷售點"],
        "匯總單號": ["匯總單號"],
        "收款状态": ["收款状态", "收款單狀態"],
        "原幣幣種": ["原幣幣種", "幣種"],
        "匯率": ["匯率"],
        "收款原幣金額": ["收款原幣金額"],
        "收款本幣金額": ["收款本幣金額"],
        "收款類型": ["收款類型"],
        "收款方式": ["收款方式"],
        "收款流水號": ["收款流水號"],
        "收款操作員": ["收款操作員"],
        "客戶名稱": ["客戶名稱"],
        "銷售公司": ["銷售公司"],
        "銷售員": ["銷售員"],
        "收款時間": ["收款時間"],
        "收款月份分組": ["收款月份分組"],
        "團代號": ["團代號"],
        "團名稱": ["團名稱"],
        "行程天數": ["行程天數"],
        "數量": ["數量"],
        "幣種": ["幣種", "原幣幣種"],
        "應收": ["應收"],
        "已收": ["已收"],
        "交易時間": ["交易時間"],
        "交易月份分組": ["交易月份分組"],
        "團負責人": ["團負責人"],
        "團負責人部門": ["團負責人部門"],
        "目的地大類": ["目的地大類"],
        "一級目的地": ["一級目的地"],
        "二級目的地": ["二級目的地"],
        "目的地名稱": ["目的地名稱"],
        "副表_銷售點": ["副表_銷售點"],
        "來源報表標籤": ["來源報表標籤"],
    }

    def _build_export_df(df: pd.DataFrame) -> pd.DataFrame:
        source = df.copy()
        if "來源單據號" in source.columns and "收款單號" not in source.columns:
            source["收款單號"] = source["來源單據號"]
        if "收款状态" in source.columns and "收款單狀態" not in source.columns:
            source["收款單狀態"] = source["收款状态"]
        if "幣種" in source.columns and "原幣幣種" not in source.columns:
            source["原幣幣種"] = source["幣種"]
        if "收款單號" in source.columns and "匯總單號" not in source.columns:
            source["匯總單號"] = source["收款單號"]
        if "銷售點" in source.columns and "副表_銷售點" not in source.columns:
            source["副表_銷售點"] = source["銷售點"]

        for col, aliases in export_aliases.items():
            if col not in source.columns:
                for alias in aliases:
                    if alias in source.columns:
                        source[col] = source[alias]
                        break
        for col in export_schema:
            if col not in source.columns:
                source[col] = ""
        return source[export_schema].fillna("")

    result_total = pd.concat(
        [
            df_tour_matched.assign(業務類別="旅行團"),
            df_others_matched.assign(業務類別="其它業務"),
        ],
        ignore_index=True,
        sort=False,
    )
    result_total = _build_export_df(result_total)
    result_tour_success = _build_export_df(df_tour_matched)
    result_other_unmatched = _build_export_df(df_others_matched)

    sheets = [
        (result_s1, "分社經營統計"),
        (result_s2, "專職經營統計"),
        (result_s3, "分社旅行團統計"),
        (result_s4, "專職旅行團統計"),
        (result_s5, "分社票務總計"),
        (result_s6, "專職票務總計"),
        (result_s7, "票務總計"),
        (result_s8, "分社每天旅行團交易人數"),
        (result_s9, "專職每天旅行團交易人數"),
        (result_s10, "分社每天票務交易數量"),
        (result_s11, "專職每天票務交易數量"),
        (result_s12, "NBS分社_旅行團金額統計"),
        (result_s13, "NBS分社_票務金額統計"),
        (result_total, "總表_多表匹配完成"),
        (result_tour_success, "旅行團_匹配成功"),
        (result_other_unmatched, "其它_未匹配_包含其它業務"),
        (result_s15, "分社線路種類每天統計"),
        (result_s16, "專職線路種類每天統計"),
    ]
    if include_branch_salesperson_sheet:
        sheets.insert(1, (result_s1_salesperson, "分社經營統計_含銷售員"))

    if not make_workbook and return_facts:
        return None, result_s1, {sheet_name: frame.copy(deep=True) for frame, sheet_name in sheets}
    if not make_workbook:
        return None, result_s1, result_s2

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for df, sheet_name in sheets:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    buf.seek(0)
    return buf, result_s1, result_s2


def build_dashboard_data_excluding_receipt_types(
    df_tour_matched: pd.DataFrame,
    df_others_matched: pd.DataFrame,
    branch_mapping: dict,
    target_branches_s3: list[str],
    cruise_depts: list[str],
    sales_rep_list: list[str],
    excluded_receipt_types: list[str],
    excluded_payment_methods: list[str] | None = None,
    make_workbook: bool = True,
    include_branch_salesperson_sheet: bool = False,
    return_facts: bool = False,
):
    excluded_types = {str(v).strip() for v in excluded_receipt_types if str(v).strip()}
    excluded_methods = {str(v).strip() for v in (excluded_payment_methods or []) if str(v).strip()}
    if not excluded_types and not excluded_methods:
        return build_dashboard_data(
            df_tour_matched,
            df_others_matched,
            branch_mapping,
            target_branches_s3,
            cruise_depts,
            sales_rep_list,
            make_workbook=make_workbook,
            include_branch_salesperson_sheet=include_branch_salesperson_sheet,
            return_facts=return_facts,
        )

    def collect_excluded_ids(df: pd.DataFrame) -> set[str]:
        if df.empty or COL_ORDER_ID not in df.columns:
            return set()
        mask = pd.Series(False, index=df.index)
        if excluded_types and "收款類型" in df.columns:
            mask |= df["收款類型"].astype(str).str.strip().isin(excluded_types)
        if excluded_methods and "收款方式" in df.columns:
            mask |= df["收款方式"].astype(str).str.strip().isin(excluded_methods)
        return set(df.loc[mask, COL_ORDER_ID].astype(str).str.strip())

    excluded_ids = collect_excluded_ids(df_tour_matched) | collect_excluded_ids(df_others_matched)

    def drop_excluded_ids(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or not excluded_ids or COL_ORDER_ID not in df.columns:
            return df.copy()
        keep_mask = ~df[COL_ORDER_ID].astype(str).str.strip().isin(excluded_ids)
        return df.loc[keep_mask].copy()

    return build_dashboard_data(
        drop_excluded_ids(df_tour_matched),
        drop_excluded_ids(df_others_matched),
        branch_mapping,
        target_branches_s3,
        cruise_depts,
        sales_rep_list,
        make_workbook=make_workbook,
        include_branch_salesperson_sheet=include_branch_salesperson_sheet,
        return_facts=return_facts,
    )
