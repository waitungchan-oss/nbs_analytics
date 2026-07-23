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


def classify_exclusion_kind(row: Mapping[str, object]) -> str:
    receipt_type = normalize_identity_text(row.get("收款類型"))
    payment_method = normalize_identity_text(row.get("收款方式"))
    if receipt_type in EXCLUDED_RECEIPT_TYPES:
        return f"receipt_type:{receipt_type}"
    if payment_method in EXCLUDED_PAYMENT_METHODS:
        return f"payment_method:{payment_method}"
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
        amount = pd.to_numeric(
            pd.Series([row.get("收款原幣金額")]), errors="coerce"
        ).fillna(0).iloc[0]
        payload = {
            "registryId": rule.id,
            "receiptNo": identity.receipt_no,
            "sourceOrderNo": identity.source_order_no,
            "exclusionKind": identity.exclusion_kind,
            "observedAmount": float(amount),
        }
        matches.append({**payload, "rowHash": canonical_json_hash(payload)})
    return ReceiptExclusionMatchResult(
        filtered_frame=main_frame.drop(index=drop_indexes).copy(),
        matches=tuple(matches),
        collisions=tuple(collisions),
    )
