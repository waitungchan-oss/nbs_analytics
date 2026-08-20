from decimal import Decimal

import pytest

from backend.services.gmv_refund_models import (
    RefundCurrentState,
    RefundObservation,
    canonical_payload_sha256,
    classify_refund_changes,
    money_to_minor,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10.005", 1001), (Decimal("0.004"), 0), ("1,200.10", 120010)],
)
def test_money_to_minor_uses_decimal_half_up(raw, expected):
    assert money_to_minor(raw) == expected


def test_money_to_minor_rejects_negative_amount():
    with pytest.raises(ValueError, match="negative"):
        money_to_minor("-0.01")


def test_status_transition_is_update_not_new_observation_identity():
    current = {
        "R-1": RefundCurrentState(
            refund_order_no="R-1",
            source_receipt_no="S-1",
            refund_amount_minor=5000,
            refund_status="退款中",
            source_batch_id="B-0",
            state_sha256="old",
        )
    }
    incoming = [
        RefundObservation(
            refund_order_no="R-1",
            source_receipt_no="S-1",
            refund_amount_minor=5000,
            refund_status="已退款",
            raw_row_sha256="row-1",
        )
    ]

    changes = classify_refund_changes(incoming, current)

    assert [item.refund_order_no for item in changes.status_changed] == ["R-1"]
    assert changes.new == ()
    assert changes.unchanged == ()
    assert changes.identity_conflicts == ()


def test_same_refund_id_with_changed_source_or_amount_is_identity_conflict():
    current = {
        "R-1": RefundCurrentState("R-1", "S-1", 5000, "退款中", "B-0", "old")
    }
    incoming = [RefundObservation("R-1", "S-2", 6000, "已退款", "row-1")]

    changes = classify_refund_changes(incoming, current)

    assert len(changes.identity_conflicts) == 1
    assert changes.new == ()
    assert changes.status_changed == ()


def test_canonical_payload_hash_is_order_independent():
    left = canonical_payload_sha256({"source": "S-1", "amountMinor": 5000})
    right = canonical_payload_sha256({"amountMinor": 5000, "source": "S-1"})

    assert left == right


def test_refund_state_hash_includes_currency_and_refund_date():
    from backend.services.gmv_refund_models import refund_state_sha256

    base = {
        "R-1": RefundCurrentState("R-1", "S-1", 5000, "已退款", "B-1", "state", "HKD", "2026-08-20")
    }
    changed = {
        "R-1": RefundCurrentState("R-1", "S-1", 5000, "已退款", "B-1", "state", "CNY", "2026-08-20")
    }

    assert refund_state_sha256(base) != refund_state_sha256(changed)
