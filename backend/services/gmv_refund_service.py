"""Pure-read Preflight application service for formal GMV refunds."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .gmv_refund_models import (
    RefundObservation,
    canonical_payload_sha256,
    classify_refund_changes,
    money_to_minor,
    refund_state_sha256,
)
from .gmv_refund_repository import GmvRefundRepository


@dataclass(frozen=True, slots=True)
class RevenueFrames:
    raw_tour: pd.DataFrame
    raw_others: pd.DataFrame
    formal_tour: pd.DataFrame
    formal_others: pd.DataFrame


@dataclass(frozen=True, slots=True)
class GmvRefundPreview:
    status: str
    file_sha256: str
    normalized_sha256: str
    current_state_sha256: str
    proposed_state_sha256: str
    revenue_generation_token: str
    rule_version: str
    change_counts: dict[str, int]
    dimensions: dict[str, dict[str, int]]
    formal_revenue_minor: int
    official_net_gmv_minor: int
    blocking_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    preflight_fingerprint: str


def _row_hash(row: Mapping[str, object]) -> str:
    return canonical_payload_sha256(dict(row))


def _build_observations(refund_rows: pd.DataFrame) -> tuple[list[RefundObservation], list[str]]:
    required = {"退款單號", "來源單據號", "退款原幣金額", "退款狀態"}
    missing = sorted(required - set(refund_rows.columns))
    if missing:
        return [], [f"MISSING_{column}" for column in missing]

    observations: list[RefundObservation] = []
    blocking: list[str] = []
    for index, row in refund_rows.iterrows():
        refund_order_no = str(row["退款單號"] or "").strip()
        source_receipt_no = str(row["來源單據號"] or "").strip()
        refund_status = str(row["退款狀態"] or "").strip()
        if not refund_order_no:
            blocking.append("EMPTY_REFUND_ORDER_NO")
            continue
        if not source_receipt_no:
            blocking.append("EMPTY_SOURCE_RECEIPT_NO")
            continue
        if not refund_status:
            blocking.append("EMPTY_REFUND_STATUS")
            continue
        try:
            amount_minor = money_to_minor(row["退款原幣金額"])
        except ValueError:
            blocking.append("INVALID_REFUND_AMOUNT")
            continue
        observations.append(
            RefundObservation(
                refund_order_no=refund_order_no,
                source_receipt_no=source_receipt_no,
                refund_amount_minor=amount_minor,
                refund_status=refund_status,
                raw_row_sha256=_row_hash(row.to_dict()),
                currency_code=str(row.get("幣種", "HKD") or "HKD").strip(),
                refund_date=str(row.get("退款日期", "") or "").strip() or None,
            )
        )
    return observations, sorted(set(blocking))


def _observations_frame(states: Mapping[str, object]) -> pd.DataFrame:
    rows = [
        {
            "退款單號": state.refund_order_no,
            "來源單據號": state.source_receipt_no,
            "退款原幣金額": state.refund_amount_minor / 100,
            "退款狀態": state.refund_status,
        }
        for state in states.values()
    ]
    return pd.DataFrame(rows, columns=["退款單號", "來源單據號", "退款原幣金額", "退款狀態"])


def _formal_revenue_minor(frames: RevenueFrames) -> int:
    values = []
    for frame in (frames.formal_tour, frames.formal_others):
        if "收款原幣金額" in frame.columns:
            values.extend(frame["收款原幣金額"].tolist())
    return sum(money_to_minor(value) for value in values)


def preview_refund_batch(
    refund_rows: pd.DataFrame,
    *,
    repository: GmvRefundRepository,
    revenue_frames: RevenueFrames,
    revenue_generation_token: str,
    rule_version: str,
    file_sha256: str,
) -> GmvRefundPreview:
    observations, blocking_codes = _build_observations(refund_rows)
    current = repository.load_current_refunds()
    current_hash = refund_state_sha256(current)
    changes = classify_refund_changes(observations, current)
    proposed = dict(current)
    for change in (*changes.new, *changes.status_changed):
        proposed[change.refund_order_no] = change.to_current_state(batch_id="PREVIEW")

    normalized_sha256 = hashlib.sha256(
        "\n".join(item.raw_row_sha256 for item in observations).encode("utf-8")
    ).hexdigest()
    dimensions: dict[str, dict[str, int]] = {}
    proposed_rows = _observations_frame(proposed)
    from app_workflows import _apply_gmv_refund_adjustments

    for dimension, status in (("總退款", None), ("已退款", "已退款")):
        adjusted = _apply_gmv_refund_adjustments(
            revenue_frames.formal_tour,
            revenue_frames.formal_others,
            proposed_rows,
            refund_status=status,
        )
        dimensions[dimension] = {
            "source_order_count": len(adjusted["refund_amounts"]),
            "matched_source_order_count": len(adjusted["matched_source_ids"]),
            "unmatched_source_order_count": len(adjusted["unmatched_source_ids"]),
            "refund_detail_amount_minor": money_to_minor(adjusted["refund_total"]),
            "applied_refund_amount_minor": money_to_minor(adjusted["applied_refund_total"]),
            "over_refund_amount_minor": money_to_minor(adjusted["over_refund_total"]),
        }

    if changes.identity_conflicts:
        blocking_codes.append("REFUND_IDENTITY_CONFLICT")
    blocking_codes = sorted(set(blocking_codes))
    status = "blocked" if blocking_codes else "ready"
    formal_revenue_minor = _formal_revenue_minor(revenue_frames)
    paid_deduction = dimensions["已退款"]["applied_refund_amount_minor"]
    proposed_hash = refund_state_sha256(proposed)
    fingerprint = canonical_payload_sha256(
        {
            "fileSha256": file_sha256,
            "normalizedSha256": normalized_sha256,
            "currentStateSha256": current_hash,
            "proposedStateSha256": proposed_hash,
            "revenueGenerationToken": revenue_generation_token,
            "ruleVersion": rule_version,
        }
    )
    return GmvRefundPreview(
        status=status,
        file_sha256=file_sha256,
        normalized_sha256=normalized_sha256,
        current_state_sha256=current_hash,
        proposed_state_sha256=proposed_hash,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
        change_counts=changes.counts,
        dimensions=dimensions,
        formal_revenue_minor=formal_revenue_minor,
        official_net_gmv_minor=formal_revenue_minor - paid_deduction,
        blocking_codes=tuple(blocking_codes),
        warning_codes=(),
        preflight_fingerprint=fingerprint,
    )
