"""Immutable domain records for the formal GMV refund ledger."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
