from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from backend.services.receipt_exclusion_models import (
    ReceiptExclusionIdentity,
    ReceiptExclusionMatchResult,
    ReceiptExclusionRule,
    canonical_json_hash,
)

EXCLUDED_RECEIPT_TYPES = {"掛賬核銷"}
EXCLUDED_PAYMENT_METHODS = {"TT 退款轉團款"}


def normalize_identity_text(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\u3000", " ").replace("\xa0", " ").strip().upper()


def is_shifted_receipt_export_row(row: Mapping[str, object]) -> bool:
    original_currency = row.get("原幣幣種")
    currency_in_rate = normalize_identity_text(row.get("匯率"))
    tail_value = row.get("Unnamed: 19")
    original_currency_missing = (
        original_currency is None
        or bool(pd.isna(original_currency))
        or not normalize_identity_text(original_currency)
    )
    tail_value_present = (
        tail_value is not None
        and not bool(pd.isna(tail_value))
        and bool(normalize_identity_text(tail_value))
    )
    return (
        original_currency_missing
        and bool(currency_in_rate)
        and ("幣" in currency_in_rate or any(char.isalpha() for char in currency_in_rate))
        and tail_value_present
    )


def receipt_exclusion_observed_amount(row: Mapping[str, object]) -> float:
    amount_column = "收款本幣金額" if is_shifted_receipt_export_row(row) else "收款原幣金額"
    amount = pd.to_numeric(pd.Series([row.get(amount_column)]), errors="coerce").fillna(0).iloc[0]
    return float(amount)


def classify_exclusion_kind(row: Mapping[str, object]) -> str:
    receipt_type = normalize_identity_text(row.get("收款類型"))
    payment_method = normalize_identity_text(row.get("收款方式"))
    if receipt_type in EXCLUDED_RECEIPT_TYPES:
        return f"receipt_type:{receipt_type}"
    if payment_method in EXCLUDED_PAYMENT_METHODS:
        return f"payment_method:{payment_method}"
    if is_shifted_receipt_export_row(row):
        shifted_receipt_type = normalize_identity_text(row.get("收款方式"))
        shifted_payment_method = normalize_identity_text(row.get("收款流水號"))
        if shifted_receipt_type in EXCLUDED_RECEIPT_TYPES:
            return f"receipt_type:{shifted_receipt_type}"
        if shifted_payment_method in EXCLUDED_PAYMENT_METHODS:
            return f"payment_method:{shifted_payment_method}"
    return ""


def _row_identity(row: Mapping[str, object]) -> ReceiptExclusionIdentity:
    return ReceiptExclusionIdentity(
        receipt_no=normalize_identity_text(row.get("收款單號")),
        source_order_no=normalize_identity_text(row.get("來源單據號")),
        exclusion_kind=classify_exclusion_kind(row),
    )


def match_receipt_exclusions(
    main_frame: pd.DataFrame,
    rules: Sequence[ReceiptExclusionRule],
) -> ReceiptExclusionMatchResult:
    active = [rule for rule in rules if rule.status == "active"]
    by_receipt = {rule.identity.receipt_no: rule for rule in active}
    drop_indexes: list[object] = []
    matches: list[dict] = []
    collisions: list[dict] = []
    for index, row in main_frame.iterrows():
        identity = _row_identity(row)
        rule = by_receipt.get(identity.receipt_no)
        if rule is None:
            continue
        reason = ""
        if identity.source_order_no != rule.identity.source_order_no:
            reason = "source_order_mismatch"
        elif identity.exclusion_kind != rule.identity.exclusion_kind:
            reason = "exclusion_kind_mismatch"
        if reason:
            collisions.append({
                "registryId": rule.id,
                "receiptNo": identity.receipt_no,
                "reason": reason,
            })
            continue
        drop_indexes.append(index)
        payload = {
            "registryId": rule.id,
            "receiptNo": identity.receipt_no,
            "sourceOrderNo": identity.source_order_no,
            "exclusionKind": identity.exclusion_kind,
            "observedAmount": receipt_exclusion_observed_amount(row),
        }
        matches.append({**payload, "rowHash": canonical_json_hash(payload)})
    return ReceiptExclusionMatchResult(
        filtered_frame=main_frame.drop(index=drop_indexes).copy(),
        matches=tuple(matches),
        collisions=tuple(collisions),
    )
