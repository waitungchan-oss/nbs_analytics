from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.services.receipt_exclusion_matcher import (
    classify_exclusion_kind,
    normalize_identity_text,
)
from backend.services.receipt_exclusion_models import (
    ReceiptExclusionIdentity,
    canonical_json_hash,
)


RAW_EVIDENCE_FIELDS = (
    "來源單據號", "收款單號", "收款類型", "收款方式",
    "收款原幣金額", "收款時間", "銷售點", "銷售員",
)
PREPARED_EVIDENCE_FIELDS = (
    "來源單據號", "收款單號", "收款類型", "收款方式",
    "收款原幣金額", "統一日期", "銷售點", "副表_銷售點",
    "銷售員", "資料來源", "產品分類",
)


def _json_value(value):
    return None if pd.isna(value) else value


def _project_row(row: pd.Series, fields: tuple[str, ...]) -> dict:
    return {
        field: _json_value(row[field])
        for field in fields
        if field in row.index
    }


def _is_exclusion_driver(driver: dict) -> bool:
    payment_type = normalize_identity_text(driver.get("paymentType"))
    payment_method = normalize_identity_text(driver.get("paymentMethod"))
    return payment_type == "掛賬核銷" or payment_method == "TT 退款轉團款"


def _eligible_identity(driver: dict) -> ReceiptExclusionIdentity | None:
    payment_type = normalize_identity_text(driver.get("paymentType"))
    payment_method = normalize_identity_text(driver.get("paymentMethod"))
    if payment_type == "掛賬核銷":
        kind = f"receipt_type:{payment_type}"
    elif payment_method == "TT 退款轉團款":
        kind = f"payment_method:{payment_method}"
    else:
        return None
    identity = ReceiptExclusionIdentity(
        receipt_no=normalize_identity_text(driver.get("receiptNo")),
        source_order_no=normalize_identity_text(driver.get("sourceOrderNo")),
        exclusion_kind=kind,
    )
    return identity if identity.receipt_no and identity.source_order_no else None


def _matching_rows(frame: pd.DataFrame, identity: ReceiptExclusionIdentity) -> list[pd.Series]:
    if frame is None or frame.empty:
        return []
    matches: list[pd.Series] = []
    for _, row in frame.iterrows():
        candidate_identity = ReceiptExclusionIdentity(
            receipt_no=normalize_identity_text(row.get("收款單號")),
            source_order_no=normalize_identity_text(row.get("來源單據號")),
            exclusion_kind=classify_exclusion_kind(row),
        )
        if candidate_identity == identity:
            matches.append(row)
    return matches


def _resolve_candidate_evidence(
    drivers: list[dict],
    raw_main_frame: pd.DataFrame,
    prepared_frames: list[pd.DataFrame],
    source_files: list[str],
    source_batch_fingerprint: str,
) -> tuple[list[dict], dict]:
    source_name = Path(str(source_files[0] if source_files else "")).name
    if not source_name:
        return [], {}
    candidates: list[dict] = []
    private_evidence: dict = {}
    for driver in drivers:
        identity = _eligible_identity(driver)
        if identity is None:
            return [], {}
        raw_matches = _matching_rows(raw_main_frame, identity)
        prepared_matches = [
            ("tour_data" if index == 0 else "others_data", row)
            for index, frame in enumerate(prepared_frames)
            for row in _matching_rows(frame, identity)
        ]
        if len(raw_matches) != 1 or len(prepared_matches) != 1:
            return [], {}
        raw_payload = _project_row(raw_matches[0], RAW_EVIDENCE_FIELDS)
        table_name, prepared_row = prepared_matches[0]
        prepared_payload = _project_row(prepared_row, PREPARED_EVIDENCE_FIELDS)
        raw_row_hash = canonical_json_hash(raw_payload)
        prepared_row_hash = canonical_json_hash(prepared_payload)
        candidate_id = identity.candidate_id
        row_hash = canonical_json_hash({
            "candidateId": candidate_id,
            "rawRowHash": raw_row_hash,
            "preparedRowHash": prepared_row_hash,
        })
        observed_amount = float(
            pd.to_numeric(
                pd.Series([raw_payload.get("收款原幣金額")]), errors="coerce"
            ).fillna(0).iloc[0]
        )
        candidates.append({
            "candidateId": candidate_id,
            "sourceOrderNo": identity.source_order_no,
            "receiptNo": identity.receipt_no,
            "exclusionKind": identity.exclusion_kind,
            "observedAmount": observed_amount,
            "affectedRevenue": float(driver.get("deltaAmount") or 0),
            "rowHash": row_hash,
        })
        private_evidence[candidate_id] = {
            "rawPayload": raw_payload,
            "rawRowHash": raw_row_hash,
            "preparedPayload": prepared_payload,
            "preparedRowHash": prepared_row_hash,
            "sourceFileName": source_name,
            "sourceFileSha256": source_batch_fingerprint,
            "observedAmount": observed_amount,
            "tableName": table_name,
        }
    return candidates, private_evidence


def build_receipt_exclusion_proposal(
    *,
    diagnosis: dict,
    raw_main_frame: pd.DataFrame,
    prepared_frames: list[pd.DataFrame],
    operation_id: str,
    source_files: list[str],
    source_batch_fingerprint: str,
    registry_revision: str,
    live_db_identity: str,
) -> tuple[dict, dict]:
    exclusion_drivers = [
        driver for driver in diagnosis.get("topDrivers", []) if _is_exclusion_driver(driver)
    ]
    if diagnosis.get("status") != "drift" or not exclusion_drivers:
        return {}, {}
    if any(_eligible_identity(driver) is None for driver in exclusion_drivers):
        return {}, {}
    candidates, private_evidence = _resolve_candidate_evidence(
        exclusion_drivers,
        raw_main_frame,
        prepared_frames,
        source_files,
        source_batch_fingerprint,
    )
    if not candidates:
        return {}, {}
    fingerprint_payload = {
        "operationId": operation_id,
        "sourceBatchFingerprint": source_batch_fingerprint,
        "diagnosedCheckKey": diagnosis.get("diagnosedCheckKey"),
        "expectedTotal": diagnosis.get("expectedTotal"),
        "actualTotal": diagnosis.get("actualTotal"),
        "deltaAmount": diagnosis.get("deltaAmount"),
        "candidateIds": [candidate["candidateId"] for candidate in candidates],
        "rowHashes": [candidate["rowHash"] for candidate in candidates],
        "registryRevision": registry_revision,
        "liveDbIdentity": live_db_identity,
    }
    return {
        "schemaVersion": "receipt-exclusion-proposal-v1",
        "status": "confirmation_required",
        "operationId": operation_id,
        "sourceBatchFingerprint": source_batch_fingerprint,
        "diagnosedCheckKey": str(diagnosis.get("diagnosedCheckKey") or ""),
        "expectedTotal": float(diagnosis.get("expectedTotal") or 0),
        "actualTotal": float(diagnosis.get("actualTotal") or 0),
        "deltaAmount": float(diagnosis.get("deltaAmount") or 0),
        "candidates": candidates,
        "proposalFingerprint": canonical_json_hash(fingerprint_payload),
    }, private_evidence


def validate_candidate_selection(proposal: dict, selected_candidate_ids: list[str]) -> list[dict]:
    candidates = {
        str(candidate.get("candidateId")): candidate
        for candidate in proposal.get("candidates", [])
    }
    selected: list[dict] = []
    seen: set[str] = set()
    for candidate_id in selected_candidate_ids:
        if candidate_id in seen or candidate_id not in candidates:
            raise ValueError("unknown receipt exclusion candidate")
        seen.add(candidate_id)
        selected.append(candidates[candidate_id])
    if not selected:
        raise ValueError("at least one receipt exclusion candidate is required")
    return selected
