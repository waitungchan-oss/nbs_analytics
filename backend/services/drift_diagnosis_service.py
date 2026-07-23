from __future__ import annotations

import pandas as pd

from backend.services.revenue_scope_service import build_revenue_scope_frames
from backend.services.stability_service import PHASE2B_BASELINE_MONTH, PHASE2B_EXPECTED_TOTAL
from config import COL_MONEY, COL_ORDER_ID
from pipeline import normalize_runtime_columns


DRIFT_ROW_LIMIT = 50
_EXCLUDED_RECEIPT_TYPES = {"掛賬核銷"}
_EXCLUDED_PAYMENT_METHODS = {"TT 退款轉團款"}


def _money_text(value: float) -> str:
    return f"HKD {float(value):,.0f}"


def _safe_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u3000", " ").strip()
    return text if text and text.lower() != "nan" else ""


def _float_value(value, default: float) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return default if pd.isna(parsed) else float(parsed)


def _select_drift_context(gate: dict) -> dict:
    drift_keys = {
        str(check.get("key") or "")
        for check in gate.get("driftChecks", [])
        if str(check.get("status") or "") == "drift"
    }
    for check in (gate.get("monthlyBaseline") or {}).get("checks", []):
        key = str(check.get("key") or "")
        if key in drift_keys and key.startswith("monthlyRevenue:"):
            month = str(check.get("month") or key.partition(":")[2])
            expected = _float_value(check.get("expectedTotal"), PHASE2B_EXPECTED_TOTAL)
            actual = _float_value(check.get("actualTotal"), 0.0)
            return {
                "status": str(check.get("status") or "drift"),
                "baselineMonth": month,
                "expectedTotal": expected,
                "actualTotal": actual,
                "deltaAmount": _float_value(check.get("deltaAmount"), actual - expected),
                "checkKey": key,
            }

    expected = _float_value(gate.get("expectedTotal"), PHASE2B_EXPECTED_TOTAL)
    actual = _float_value(gate.get("actualTotal"), 0.0)
    return {
        "status": str(gate.get("status") or "drift"),
        "baselineMonth": str(gate.get("baselineMonth") or PHASE2B_BASELINE_MONTH),
        "expectedTotal": expected,
        "actualTotal": actual,
        "deltaAmount": _float_value(gate.get("deltaAmount"), actual - expected),
        "checkKey": "",
    }


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required_columns = [
        "收款單號",
        "銷售點",
        "副表_銷售點",
        "收款類型",
        "收款方式",
        "銷售員",
        "統一日期",
    ]
    if frame is None or frame.empty:
        work = normalize_runtime_columns(pd.DataFrame())
        for column in required_columns:
            if column not in work.columns:
                work[column] = ""
        work["收款原幣金額"] = pd.Series(dtype=float)
        return work
    work = normalize_runtime_columns(frame).copy()
    for column in required_columns:
        if column not in work.columns:
            work[column] = ""
    work["收款原幣金額"] = pd.to_numeric(work.get(COL_MONEY, 0), errors="coerce").fillna(0)
    work[COL_ORDER_ID] = work.get(COL_ORDER_ID, "").astype(str).map(_safe_text)
    work["收款單號"] = work["收款單號"].astype(str).map(_safe_text)
    work["收款類型"] = work["收款類型"].astype(str).map(_safe_text)
    work["收款方式"] = work["收款方式"].astype(str).map(_safe_text)
    work["銷售點"] = work["銷售點"].astype(str).map(_safe_text)
    work["副表_銷售點"] = work["副表_銷售點"].astype(str).map(_safe_text)
    work["銷售員"] = work["銷售員"].astype(str).map(_safe_text)
    work["統一日期"] = work.get("統一日期", pd.Series(dtype=str)).astype(str).map(_safe_text)
    return work


def _row_key(row: pd.Series) -> str:
    receipt_no = _safe_text(row.get("收款單號"))
    if receipt_no:
        return f"receipt::{receipt_no}"
    source_no = _safe_text(row.get(COL_ORDER_ID))
    date_value = _safe_text(row.get("統一日期"))
    amount_value = f"{float(pd.to_numeric(pd.Series([row.get('收款原幣金額', 0)]), errors='coerce').fillna(0).iloc[0]):.2f}"
    receipt_type = _safe_text(row.get("收款類型"))
    payment_method = _safe_text(row.get("收款方式"))
    return f"fallback::{source_no}::{date_value}::{amount_value}::{receipt_type}::{payment_method}"


def _frame_with_keys(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    work = _prepare_frame(frame)
    if work.empty:
        columns = [
            "_row_key",
            "dataSource",
            COL_ORDER_ID,
            "收款單號",
            "收款類型",
            "收款方式",
            "收款原幣金額",
            "銷售點",
            "副表_銷售點",
            "銷售員",
            "統一日期",
        ]
        return pd.DataFrame(columns=columns)

    work = work.copy()
    work["_row_key"] = work.apply(_row_key, axis=1)
    work["dataSource"] = label
    columns = [
        "_row_key",
        "dataSource",
        COL_ORDER_ID,
        "收款單號",
        "收款類型",
        "收款方式",
        "收款原幣金額",
        "銷售點",
        "副表_銷售點",
        "銷售員",
        "統一日期",
    ]
    for column in columns:
        if column not in work.columns:
            work[column] = ""
    return work[columns].copy()


def _is_excluded_row(row: pd.Series) -> bool:
    return _safe_text(row.get("收款類型")) in _EXCLUDED_RECEIPT_TYPES or _safe_text(row.get("收款方式")) in _EXCLUDED_PAYMENT_METHODS


def _row_reason(row: pd.Series, other_row: pd.Series | None, merge_state: str) -> str:
    excluded = _is_excluded_row(row)
    if merge_state == "right_only":
        return "新增明細"
    if merge_state == "left_only":
        return "原始明細已被移除"
    if excluded and other_row is not None:
        return "新增排除明細，會觸發整個來源單據號被正式口徑排除"
    if excluded:
        return "排除明細變動"
    return "同來源單據號明細變動"


def _order_contribution_map(frame: pd.DataFrame, excluded_order_ids: set[str]) -> dict[str, float]:
    if frame.empty or COL_ORDER_ID not in frame.columns:
        return {}
    work = frame.copy()
    work[COL_ORDER_ID] = work[COL_ORDER_ID].astype(str).map(_safe_text)
    work = work[work[COL_ORDER_ID] != ""].copy()
    if work.empty:
        return {}
    work["收款原幣金額"] = pd.to_numeric(work.get("收款原幣金額", 0), errors="coerce").fillna(0)
    grouped = work.groupby(COL_ORDER_ID)["收款原幣金額"].sum()
    return {
        str(order_id): (0.0 if str(order_id) in excluded_order_ids else float(amount))
        for order_id, amount in grouped.items()
    }


def _filter_month(frame: pd.DataFrame, month: str) -> pd.DataFrame:
    if frame.empty or "統一日期" not in frame.columns:
        return frame.iloc[0:0].copy()
    dates = pd.to_datetime(frame["統一日期"], errors="coerce")
    return frame.loc[dates.dt.strftime("%Y-%m") == month].copy()


def _select_driver_row(order_rows: pd.DataFrame, order_id: str) -> pd.Series | None:
    if order_rows.empty:
        return None
    work = order_rows.copy()
    work["收款原幣金額"] = pd.to_numeric(work.get("收款原幣金額", 0), errors="coerce").fillna(0)
    excluded = work[work.apply(_is_excluded_row, axis=1)]
    if not excluded.empty:
        return excluded.sort_values("收款原幣金額", ascending=False).iloc[0]
    return work.sort_values("收款原幣金額", ascending=False).iloc[0]


def _with_clean_order_id(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if COL_ORDER_ID not in work.columns:
        work[COL_ORDER_ID] = ""
    work["_clean_order_id"] = work[COL_ORDER_ID].astype(str).map(_safe_text)
    return work


def _rows_for_order(clean_frame: pd.DataFrame, order_id: str) -> pd.DataFrame:
    if clean_frame.empty or "_clean_order_id" not in clean_frame.columns:
        return clean_frame.iloc[0:0].copy()
    return clean_frame.loc[clean_frame["_clean_order_id"] == order_id].copy()


def _candidate_rows(clean_frame: pd.DataFrame, candidate_order_ids: set[str]) -> pd.DataFrame:
    if clean_frame.empty or not candidate_order_ids or "_clean_order_id" not in clean_frame.columns:
        return clean_frame.iloc[0:0].copy()
    return clean_frame.loc[clean_frame["_clean_order_id"].isin(candidate_order_ids)].drop(columns=["_clean_order_id"], errors="ignore").copy()


def build_upload_drift_diagnosis(
    live_tour: pd.DataFrame,
    live_others: pd.DataFrame,
    temp_tour: pd.DataFrame,
    temp_others: pd.DataFrame,
    *,
    stability_gate: dict | None = None,
    row_limit: int = DRIFT_ROW_LIMIT,
) -> dict:
    gate = stability_gate or {}
    drift_context = _select_drift_context(gate)
    status = drift_context["status"]
    baseline_month = drift_context["baselineMonth"]
    expected_total = drift_context["expectedTotal"]
    actual_total = drift_context["actualTotal"]
    delta_amount = drift_context["deltaAmount"]
    check_key = drift_context["checkKey"]

    live_tour = _prepare_frame(live_tour)
    live_others = _prepare_frame(live_others)
    temp_tour = _prepare_frame(temp_tour)
    temp_others = _prepare_frame(temp_others)

    live_raw = pd.concat([live_tour, live_others], ignore_index=True, sort=False)
    temp_raw = pd.concat([temp_tour, temp_others], ignore_index=True, sort=False)
    live_raw_clean = _with_clean_order_id(live_raw)
    temp_raw_clean = _with_clean_order_id(temp_raw)

    live_analysis_tour, live_analysis_others, live_audit = build_revenue_scope_frames(live_tour, live_others)
    temp_analysis_tour, temp_analysis_others, temp_audit = build_revenue_scope_frames(temp_tour, temp_others)

    if status == "matched" or abs(delta_amount) < 1.0:
        return {
            "status": "no_drift",
            "baselineMonth": baseline_month,
            "expectedTotal": expected_total,
            "actualTotal": actual_total,
            "deltaAmount": delta_amount,
            "summaryMessage": "核心口徑未漂移。",
            "rowLimit": row_limit,
            "liveAudit": live_audit,
            "tempAudit": temp_audit,
            "sourceOrderDiffs": [],
            "receiptDiffs": [],
            "excludedReceiptDiffs": [],
            "topDrivers": [],
            "detailMode": "skipped_core_matched",
        }

    live_excluded_ids = {
        str(value).strip()
        for value in (
            pd.concat(
                [
                    live_tour.loc[live_tour["收款類型"].isin(_EXCLUDED_RECEIPT_TYPES), COL_ORDER_ID],
                    live_tour.loc[live_tour["收款方式"].isin(_EXCLUDED_PAYMENT_METHODS), COL_ORDER_ID],
                    live_others.loc[live_others["收款類型"].isin(_EXCLUDED_RECEIPT_TYPES), COL_ORDER_ID],
                    live_others.loc[live_others["收款方式"].isin(_EXCLUDED_PAYMENT_METHODS), COL_ORDER_ID],
                ],
                ignore_index=True,
            ).dropna().astype(str)
        )
        if str(value).strip()
    }
    temp_excluded_ids = {
        str(value).strip()
        for value in (
            pd.concat(
                [
                    temp_tour.loc[temp_tour["收款類型"].isin(_EXCLUDED_RECEIPT_TYPES), COL_ORDER_ID],
                    temp_tour.loc[temp_tour["收款方式"].isin(_EXCLUDED_PAYMENT_METHODS), COL_ORDER_ID],
                    temp_others.loc[temp_others["收款類型"].isin(_EXCLUDED_RECEIPT_TYPES), COL_ORDER_ID],
                    temp_others.loc[temp_others["收款方式"].isin(_EXCLUDED_PAYMENT_METHODS), COL_ORDER_ID],
                ],
                ignore_index=True,
            ).dropna().astype(str)
        )
        if str(value).strip()
    }

    live_analysis = pd.concat([live_analysis_tour, live_analysis_others], ignore_index=True, sort=False)
    temp_analysis = pd.concat([temp_analysis_tour, temp_analysis_others], ignore_index=True, sort=False)
    if check_key.startswith("monthlyRevenue:"):
        live_analysis = _filter_month(live_analysis, baseline_month)
        temp_analysis = _filter_month(temp_analysis, baseline_month)
        scoped_raw = pd.concat(
            [_filter_month(live_raw, baseline_month), _filter_month(temp_raw, baseline_month)],
            ignore_index=True,
            sort=False,
        )
        scoped_order_ids = {
            _safe_text(value)
            for value in scoped_raw.get(COL_ORDER_ID, pd.Series(dtype=str))
            if _safe_text(value)
        }
    else:
        scoped_order_ids = live_excluded_ids | temp_excluded_ids

    live_order_map = _order_contribution_map(live_analysis, live_excluded_ids)
    temp_order_map = _order_contribution_map(temp_analysis, temp_excluded_ids)

    scoped_excluded_ids = (live_excluded_ids | temp_excluded_ids) & scoped_order_ids
    order_ids = sorted(set(live_order_map) | set(temp_order_map) | scoped_excluded_ids)
    order_diffs: list[dict] = []
    for order_id in order_ids:
        live_rows = _rows_for_order(live_raw_clean, order_id)
        temp_rows = _rows_for_order(temp_raw_clean, order_id)
        live_total = float(live_order_map.get(order_id, 0.0))
        temp_total = float(temp_order_map.get(order_id, 0.0))
        delta = round(temp_total - live_total, 2)
        live_excluded = order_id in live_excluded_ids
        temp_excluded = order_id in temp_excluded_ids
        if abs(delta) < 1.0 and live_excluded == temp_excluded:
            continue

        reason = "來源單據號明細變動"
        trigger_receipt = ""
        if temp_excluded and not live_excluded:
            excluded_rows = temp_rows[temp_rows.apply(_is_excluded_row, axis=1)]
            trigger = _select_driver_row(excluded_rows, order_id)
            if trigger is not None:
                trigger_receipt = _safe_text(trigger.get("收款單號"))
                reason = "新增排除明細，觸發整個來源單據號被正式口徑排除"
        elif live_excluded and not temp_excluded:
            reason = "排除明細被移除，來源單據號回補"
        elif len(live_rows) != len(temp_rows):
            reason = "來源單據號筆數變動"

        order_diffs.append(
            {
                "sourceOrderNo": order_id,
                "liveContribution": live_total,
                "tempContribution": temp_total,
                "deltaAmount": delta,
                "liveReceiptCount": int(len(live_rows)),
                "tempReceiptCount": int(len(temp_rows)),
                "liveExcluded": live_excluded,
                "tempExcluded": temp_excluded,
                "triggerReceiptNo": trigger_receipt,
                "reason": reason,
            }
        )

    order_diffs.sort(
        key=lambda item: (
            abs(float(item.get("deltaAmount") or 0) - delta_amount) >= 1.0,
            -abs(float(item.get("deltaAmount") or 0)),
        )
    )
    candidate_order_ids = {str(item.get("sourceOrderNo") or "") for item in order_diffs if str(item.get("sourceOrderNo") or "")}

    live_rows = _frame_with_keys(_candidate_rows(live_raw_clean, candidate_order_ids), "live")
    temp_rows = _frame_with_keys(_candidate_rows(temp_raw_clean, candidate_order_ids), "temp")
    candidate_receipt_rows = {"live": int(len(live_rows)), "temp": int(len(temp_rows))}
    merged = live_rows.merge(
        temp_rows,
        on="_row_key",
        how="outer",
        suffixes=("_live", "_temp"),
        indicator=True,
    )

    receipt_diffs: list[dict] = []
    excluded_receipt_diffs: list[dict] = []
    for _, row in merged.iterrows():
        live_order = _safe_text(row.get(f"{COL_ORDER_ID}_live"))
        temp_order = _safe_text(row.get(f"{COL_ORDER_ID}_temp"))
        source_order = temp_order or live_order
        live_amount = float(pd.to_numeric(pd.Series([row.get("收款原幣金額_live", 0)]), errors="coerce").fillna(0).iloc[0]) if row.get("_merge") != "right_only" else 0.0
        temp_amount = float(pd.to_numeric(pd.Series([row.get("收款原幣金額_temp", 0)]), errors="coerce").fillna(0).iloc[0]) if row.get("_merge") != "left_only" else 0.0
        amount_delta = round(temp_amount - live_amount, 2)
        if row.get("_merge") == "both":
            if abs(amount_delta) < 1.0:
                continue
            reason = "同筆收款金額變動"
        elif row.get("_merge") == "left_only":
            reason = "原始明細已被移除"
        else:
            reason = "新增明細"
        row_payload = {
            "sourceOrderNo": source_order,
            "receiptNo": _safe_text(row.get("收款單號_temp")) or _safe_text(row.get("收款單號_live")),
            "amountDelta": amount_delta,
            "liveAmount": live_amount,
            "tempAmount": temp_amount,
            "paymentType": _safe_text(row.get("收款類型_temp")) or _safe_text(row.get("收款類型_live")),
            "paymentMethod": _safe_text(row.get("收款方式_temp")) or _safe_text(row.get("收款方式_live")),
            "mainSalesPoint": _safe_text(row.get("銷售點_temp")) or _safe_text(row.get("銷售點_live")),
            "subSalesPoint": _safe_text(row.get("副表_銷售點_temp")) or _safe_text(row.get("副表_銷售點_live")),
            "salesperson": _safe_text(row.get("銷售員_temp")) or _safe_text(row.get("銷售員_live")),
            "reason": _row_reason(
                pd.Series(
                    {
                        "收款類型": _safe_text(row.get("收款類型_temp")) or _safe_text(row.get("收款類型_live")),
                        "收款方式": _safe_text(row.get("收款方式_temp")) or _safe_text(row.get("收款方式_live")),
                    }
                ),
                pd.Series(
                    {
                        "收款類型": _safe_text(row.get("收款類型_live")),
                        "收款方式": _safe_text(row.get("收款方式_live")),
                    }
                )
                if row.get("_merge") != "right_only"
                else None,
                str(row.get("_merge") or ""),
            ),
            "rowState": str(row.get("_merge") or ""),
            "analysisOrderNo": source_order,
        }
        receipt_diffs.append(row_payload)
        if _safe_text(row.get("收款類型_temp")) in _EXCLUDED_RECEIPT_TYPES or _safe_text(row.get("收款方式_temp")) in _EXCLUDED_PAYMENT_METHODS:
            excluded_receipt_diffs.append(row_payload)
        elif _safe_text(row.get("收款類型_live")) in _EXCLUDED_RECEIPT_TYPES or _safe_text(row.get("收款方式_live")) in _EXCLUDED_PAYMENT_METHODS:
            excluded_receipt_diffs.append(row_payload)

    receipt_diffs.sort(key=lambda item: abs(float(item.get("amountDelta") or 0)), reverse=True)
    excluded_receipt_diffs.sort(key=lambda item: abs(float(item.get("amountDelta") or 0)), reverse=True)

    top_drivers: list[dict] = []
    for order_diff in order_diffs[:row_limit]:
        order_id = str(order_diff.get("sourceOrderNo") or "")
        temp_order_rows = _rows_for_order(temp_raw_clean, order_id).drop(columns=["_clean_order_id"], errors="ignore")
        live_order_rows = _rows_for_order(live_raw_clean, order_id).drop(columns=["_clean_order_id"], errors="ignore")
        chosen_row = _select_driver_row(temp_order_rows if not temp_order_rows.empty else live_order_rows, order_id)
        if chosen_row is None:
            continue
        top_drivers.append(
            {
                "sourceOrderNo": order_id,
                "receiptNo": _safe_text(chosen_row.get("收款單號")),
                "amount": float(pd.to_numeric(pd.Series([chosen_row.get("收款原幣金額", 0)]), errors="coerce").fillna(0).iloc[0]),
                "paymentType": _safe_text(chosen_row.get("收款類型")),
                "paymentMethod": _safe_text(chosen_row.get("收款方式")),
                "mainSalesPoint": _safe_text(chosen_row.get("銷售點")),
                "subSalesPoint": _safe_text(chosen_row.get("副表_銷售點")),
                "reason": str(order_diff.get("reason") or ""),
                "deltaAmount": float(order_diff.get("deltaAmount") or 0),
            }
        )

    if status == "matched" or abs(delta_amount) < 1.0:
        summary_message = "核心口徑未漂移。"
        diagnosis_status = "no_drift"
    elif top_drivers:
        first = top_drivers[0]
        summary_message = (
            f"核心口徑漂移 { _money_text(abs(delta_amount)) }，"
            f"最可能由來源單據號 {first['sourceOrderNo']} / 收款單號 {first['receiptNo'] or '—'} 觸發：{first['reason']}。"
        )
        diagnosis_status = "drift"
    else:
        summary_message = f"核心口徑漂移 {_money_text(abs(delta_amount))}，但目前沒有可用的 row-level 證據。"
        diagnosis_status = "drift"

    return {
        "status": diagnosis_status,
        "baselineMonth": baseline_month,
        "expectedTotal": expected_total,
        "actualTotal": actual_total,
        "deltaAmount": delta_amount,
        "diagnosedCheckKey": check_key,
        "summaryMessage": summary_message,
        "rowLimit": row_limit,
        "liveAudit": live_audit,
        "tempAudit": temp_audit,
        "sourceOrderDiffs": order_diffs[:row_limit],
        "receiptDiffs": receipt_diffs[:row_limit],
        "excludedReceiptDiffs": excluded_receipt_diffs[:row_limit],
        "topDrivers": top_drivers[:row_limit],
        "detailMode": "candidate_order_scope",
        "candidateOrderCount": len(candidate_order_ids),
        "candidateReceiptRows": candidate_receipt_rows,
    }
