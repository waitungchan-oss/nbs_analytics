"""Planning primitives for bounded affected-receipt GMV rebuilds."""

from __future__ import annotations

from dataclasses import dataclass

from backend.services.gmv_refund_models import (
    IncrementalRebuildPlan,
    RefundStateDelta,
    RebuildDecision,
    RebuildReasonCode,
    normalize_text,
)


@dataclass(frozen=True, slots=True)
class RebuildFingerprints:
    base_revenue_generation_token: str
    current_revenue_generation_token: str
    base_rules_fingerprint: str
    current_rules_fingerprint: str
    base_source_fingerprint: str
    current_source_fingerprint: str

    @property
    def matches(self) -> bool:
        return (
            normalize_text(self.base_revenue_generation_token)
            == normalize_text(self.current_revenue_generation_token)
            and normalize_text(self.base_rules_fingerprint)
            == normalize_text(self.current_rules_fingerprint)
            and normalize_text(self.base_source_fingerprint)
            == normalize_text(self.current_source_fingerprint)
        )


@dataclass(frozen=True, slots=True)
class IncrementalRebuildThresholds:
    max_affected_receipt_count: int = 100_000
    max_affected_receipt_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_affected_receipt_count < 0:
            raise ValueError("max_affected_receipt_count cannot be negative")
        if not 0 <= self.max_affected_receipt_ratio <= 1:
            raise ValueError("max_affected_receipt_ratio must be between 0 and 1")


def build_incremental_plan(
    *,
    base_version_id: str,
    state_delta: RefundStateDelta,
    fingerprints: RebuildFingerprints,
    source_receipt_universe_count: int | None = None,
    snapshot_complete: bool = True,
    thresholds: IncrementalRebuildThresholds | None = None,
) -> IncrementalRebuildPlan:
    """Create a deterministic plan without loading raw revenue or refund rows."""
    thresholds = thresholds or IncrementalRebuildThresholds()
    affected_receipts = tuple(sorted({normalize_text(item) for item in state_delta.affected_source_receipt_nos if normalize_text(item)}))
    affected_refund_ids = tuple(
        sorted(
            {
                normalize_text(item)
                for values in (
                    state_delta.new_refund_order_nos,
                    state_delta.status_changed_refund_order_nos,
                    state_delta.amount_changed_refund_order_nos,
                    state_delta.identity_conflict_refund_order_nos,
                )
                for item in values
                if normalize_text(item)
            }
        )
    )
    reasons: list[RebuildReasonCode] = []
    decision = RebuildDecision.INCREMENTAL_ELIGIBLE

    if state_delta.identity_conflict_refund_order_nos:
        reasons.append(RebuildReasonCode.IDENTITY_CONFLICT)
        decision = RebuildDecision.BLOCKED
    if not fingerprints.matches:
        reasons.append(RebuildReasonCode.FINGERPRINT_MISMATCH)
        if decision is not RebuildDecision.BLOCKED:
            decision = RebuildDecision.FULL_REBUILD_REQUIRED
    if not snapshot_complete:
        reasons.append(RebuildReasonCode.SNAPSHOT_INCOMPLETE)
        if decision is not RebuildDecision.BLOCKED:
            decision = RebuildDecision.FULL_REBUILD_REQUIRED

    if source_receipt_universe_count is not None:
        if source_receipt_universe_count < 0:
            raise ValueError("source_receipt_universe_count cannot be negative")
        affected_ratio = (
            len(affected_receipts) / source_receipt_universe_count
            if source_receipt_universe_count
            else 0.0
        )
        if (
            len(affected_receipts) > thresholds.max_affected_receipt_count
            or affected_ratio > thresholds.max_affected_receipt_ratio
        ):
            reasons.append(RebuildReasonCode.AFFECTED_SET_TOO_LARGE)
            if decision is not RebuildDecision.BLOCKED:
                decision = RebuildDecision.FULL_REBUILD_REQUIRED

    return IncrementalRebuildPlan(
        base_version_id=base_version_id,
        affected_source_receipt_nos=affected_receipts,
        affected_refund_ids=affected_refund_ids,
        affected_count=len(affected_receipts),
        unaffected_copy_candidate_count=max(
            (source_receipt_universe_count or 0) - len(affected_receipts), 0
        ),
        revenue_generation_token=normalize_text(fingerprints.current_revenue_generation_token),
        rules_fingerprint=normalize_text(fingerprints.current_rules_fingerprint),
        source_fingerprint=normalize_text(fingerprints.current_source_fingerprint),
        decision=decision,
        reason_codes=tuple(reasons),
    )


def resolve_rebuild_strategy(
    plan: IncrementalRebuildPlan,
    *,
    incremental_available: bool,
) -> str:
    """Select a safe execution path; never downgrade a blocked plan."""
    if plan.decision is RebuildDecision.BLOCKED:
        return "BLOCKED"
    if plan.decision is RebuildDecision.FULL_REBUILD_REQUIRED:
        return "FULL_REBUILD"
    return "INCREMENTAL" if incremental_available else "FULL_REBUILD"
