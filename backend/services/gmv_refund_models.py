"""Immutable domain records for the formal GMV refund ledger."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Mapping, Sequence


_CENT = Decimal("0.01")


def normalize_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def money_to_minor(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("money value is invalid")
    try:
        decimal_value = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("money value is invalid") from exc
    if not decimal_value.is_finite():
        raise ValueError("money value is invalid")
    if decimal_value < 0:
        raise ValueError("negative money value is not allowed")
    return int((decimal_value.quantize(_CENT, rounding=ROUND_HALF_UP) * 100))


def minor_to_money(value: int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("minor money value is invalid")
    return (Decimal(value) / 100).quantize(_CENT)


def canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def refund_state_sha256(current: Mapping[str, "RefundCurrentState"]) -> str:
    rows = [
        {
            "refundOrderNo": state.refund_order_no,
            "sourceReceiptNo": state.source_receipt_no,
            "refundStatus": state.refund_status,
            "refundAmountMinor": state.refund_amount_minor,
            "currencyCode": state.currency_code,
            "refundDate": state.refund_date,
        }
        for state in sorted(current.values(), key=lambda item: normalize_text(item.refund_order_no))
    ]
    return canonical_payload_sha256({"refunds": rows})


@dataclass(frozen=True, slots=True)
class RefundObservation:
    refund_order_no: str
    source_receipt_no: str
    refund_amount_minor: int
    refund_status: str
    raw_row_sha256: str
    currency_code: str = "HKD"
    refund_date: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("refund_order_no", "source_receipt_no", "refund_status", "raw_row_sha256"):
            if not normalize_text(getattr(self, field_name)):
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.refund_amount_minor, int) or isinstance(self.refund_amount_minor, bool):
            raise ValueError("refund_amount_minor must be an integer")
        if self.refund_amount_minor < 0:
            raise ValueError("refund_amount_minor cannot be negative")

    def to_current_state(self, batch_id: str) -> "RefundCurrentState":
        state_sha256 = canonical_payload_sha256(
            {
                "refundOrderNo": self.refund_order_no,
                "sourceReceiptNo": self.source_receipt_no,
                "refundAmountMinor": self.refund_amount_minor,
                "refundStatus": self.refund_status,
                "currencyCode": self.currency_code,
                "refundDate": self.refund_date,
            }
        )
        return RefundCurrentState(
            refund_order_no=self.refund_order_no,
            source_receipt_no=self.source_receipt_no,
            refund_amount_minor=self.refund_amount_minor,
            refund_status=self.refund_status,
            source_batch_id=batch_id,
            state_sha256=state_sha256,
            currency_code=self.currency_code,
            refund_date=self.refund_date,
        )


@dataclass(frozen=True, slots=True)
class RefundCurrentState:
    refund_order_no: str
    source_receipt_no: str
    refund_amount_minor: int
    refund_status: str
    source_batch_id: str
    state_sha256: str
    currency_code: str = "HKD"
    refund_date: str | None = None


@dataclass(frozen=True, slots=True)
class RefundChange:
    refund_order_no: str
    incoming: RefundObservation
    existing: RefundCurrentState | None
    reason: str

    def to_current_state(self, batch_id: str) -> RefundCurrentState:
        return self.incoming.to_current_state(batch_id)


@dataclass(frozen=True, slots=True)
class RefundChangeSet:
    new: tuple[RefundChange, ...] = ()
    unchanged: tuple[RefundChange, ...] = ()
    status_changed: tuple[RefundChange, ...] = ()
    identity_conflicts: tuple[RefundChange, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            "NEW": len(self.new),
            "UNCHANGED": len(self.unchanged),
            "STATUS_CHANGED": len(self.status_changed),
            "REFUND_IDENTITY_CONFLICT": len(self.identity_conflicts),
        }


@dataclass(frozen=True, slots=True)
class RefundStateDelta:
    new_refund_order_nos: tuple[str, ...] = ()
    unchanged_refund_order_nos: tuple[str, ...] = ()
    status_changed_refund_order_nos: tuple[str, ...] = ()
    amount_changed_refund_order_nos: tuple[str, ...] = ()
    identity_conflict_refund_order_nos: tuple[str, ...] = ()
    affected_source_receipt_nos: tuple[str, ...] = ()
    classification_counts: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.classification_counts is None:
            object.__setattr__(self, "classification_counts", {
                "NEW": len(self.new_refund_order_nos),
                "UNCHANGED": len(self.unchanged_refund_order_nos),
                "STATUS_CHANGED": len(self.status_changed_refund_order_nos),
                "AMOUNT_CHANGED": len(self.amount_changed_refund_order_nos),
                "REFUND_IDENTITY_CONFLICT": len(self.identity_conflict_refund_order_nos),
            })


class RebuildDecision(str, Enum):
    INCREMENTAL_ELIGIBLE = "INCREMENTAL_ELIGIBLE"
    FULL_REBUILD_REQUIRED = "FULL_REBUILD_REQUIRED"
    BLOCKED = "BLOCKED"


class RebuildReasonCode(str, Enum):
    IDENTITY_CONFLICT = "REFUND_IDENTITY_CONFLICT"
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
    SNAPSHOT_INCOMPLETE = "SNAPSHOT_INCOMPLETE"
    AFFECTED_SET_TOO_LARGE = "AFFECTED_SET_TOO_LARGE"
    INVALID_REFUND_STATE = "INVALID_REFUND_STATE"


def _stable_unique_text(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({normalized for value in values if (normalized := normalize_text(value))}))


@dataclass(frozen=True, slots=True)
class IncrementalRebuildPlan:
    base_version_id: str
    affected_source_receipt_nos: tuple[str, ...]
    affected_refund_ids: tuple[str, ...]
    affected_count: int
    unaffected_copy_candidate_count: int
    revenue_generation_token: str
    rules_fingerprint: str
    source_fingerprint: str
    decision: RebuildDecision
    reason_codes: tuple[RebuildReasonCode, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_version_id", normalize_text(self.base_version_id))
        object.__setattr__(self, "affected_source_receipt_nos", _stable_unique_text(self.affected_source_receipt_nos))
        object.__setattr__(self, "affected_refund_ids", _stable_unique_text(self.affected_refund_ids))
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes), key=lambda item: item.value)))
        if not self.base_version_id:
            raise ValueError("base_version_id is required")
        if self.affected_count < 0 or self.unaffected_copy_candidate_count < 0:
            raise ValueError("rebuild counts cannot be negative")


@dataclass(frozen=True, slots=True)
class IncrementalRebuildResult:
    version_id: str
    recomputed_receipts: int
    copied_receipts: int
    recomputed_rows: int
    copied_rows: int
    dimensions: tuple[str, ...]
    equivalence_status: str
    fallback_used: bool
    publish_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_id", normalize_text(self.version_id))
        object.__setattr__(self, "dimensions", tuple(normalize_text(item) for item in self.dimensions))
        if not self.version_id:
            raise ValueError("version_id is required")
        if any(value < 0 for value in (self.recomputed_receipts, self.copied_receipts, self.recomputed_rows, self.copied_rows)):
            raise ValueError("rebuild counts cannot be negative")


def classify_refund_state_delta(
    current: Mapping[str, RefundCurrentState],
    proposed: Sequence[RefundCurrentState],
) -> RefundStateDelta:
    """Classify an incoming current-state projection deterministically."""
    buckets: dict[str, list[str]] = {
        "NEW": [], "UNCHANGED": [], "STATUS_CHANGED": [],
        "AMOUNT_CHANGED": [], "REFUND_IDENTITY_CONFLICT": [],
    }
    affected: set[str] = set()
    seen: set[str] = set()
    for candidate in proposed:
        order_no = normalize_text(candidate.refund_order_no)
        if order_no in seen:
            buckets["REFUND_IDENTITY_CONFLICT"].append(order_no)
            affected.add(normalize_text(candidate.source_receipt_no))
            continue
        seen.add(order_no)
        prior = current.get(order_no)
        if prior is None:
            buckets["NEW"].append(order_no)
            affected.add(normalize_text(candidate.source_receipt_no))
            continue
        same_identity = (
            normalize_text(prior.source_receipt_no) == normalize_text(candidate.source_receipt_no)
            and normalize_text(prior.currency_code) == normalize_text(candidate.currency_code)
        )
        if not same_identity:
            buckets["REFUND_IDENTITY_CONFLICT"].append(order_no)
            affected.update((normalize_text(prior.source_receipt_no), normalize_text(candidate.source_receipt_no)))
        elif prior.refund_amount_minor != candidate.refund_amount_minor:
            buckets["AMOUNT_CHANGED"].append(order_no)
            affected.add(normalize_text(candidate.source_receipt_no))
        elif normalize_text(prior.refund_status) != normalize_text(candidate.refund_status):
            buckets["STATUS_CHANGED"].append(order_no)
            affected.add(normalize_text(candidate.source_receipt_no))
        else:
            buckets["UNCHANGED"].append(order_no)
    for values in buckets.values():
        values.sort()
    return RefundStateDelta(
        new_refund_order_nos=tuple(buckets["NEW"]),
        unchanged_refund_order_nos=tuple(buckets["UNCHANGED"]),
        status_changed_refund_order_nos=tuple(buckets["STATUS_CHANGED"]),
        amount_changed_refund_order_nos=tuple(buckets["AMOUNT_CHANGED"]),
        identity_conflict_refund_order_nos=tuple(buckets["REFUND_IDENTITY_CONFLICT"]),
        affected_source_receipt_nos=tuple(sorted(item for item in affected if item)),
    )


def classify_refund_changes(
    incoming: Sequence[RefundObservation],
    current: Mapping[str, RefundCurrentState],
) -> RefundChangeSet:
    new: list[RefundChange] = []
    unchanged: list[RefundChange] = []
    status_changed: list[RefundChange] = []
    identity_conflicts: list[RefundChange] = []
    seen: dict[str, RefundObservation] = {}

    for observation in incoming:
        refund_order_no = normalize_text(observation.refund_order_no)
        prior_incoming = seen.get(refund_order_no)
        if prior_incoming is not None:
            identity_conflicts.append(
                RefundChange(refund_order_no, observation, None, "DUPLICATE_REFUND_ORDER_NO")
            )
            continue
        seen[refund_order_no] = observation

        existing = current.get(refund_order_no)
        if existing is None:
            new.append(RefundChange(refund_order_no, observation, None, "NEW"))
            continue

        same_identity = (
            normalize_text(existing.source_receipt_no) == normalize_text(observation.source_receipt_no)
            and existing.refund_amount_minor == observation.refund_amount_minor
            and normalize_text(existing.currency_code) == normalize_text(observation.currency_code)
        )
        if not same_identity:
            identity_conflicts.append(
                RefundChange(refund_order_no, observation, existing, "REFUND_IDENTITY_CONFLICT")
            )
        elif normalize_text(existing.refund_status) != normalize_text(observation.refund_status):
            status_changed.append(RefundChange(refund_order_no, observation, existing, "STATUS_CHANGED"))
        else:
            unchanged.append(RefundChange(refund_order_no, observation, existing, "UNCHANGED"))

    return RefundChangeSet(
        new=tuple(new),
        unchanged=tuple(unchanged),
        status_changed=tuple(status_changed),
        identity_conflicts=tuple(identity_conflicts),
    )
