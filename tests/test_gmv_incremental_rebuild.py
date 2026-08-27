from backend.services.gmv_refund_models import (
    RefundCurrentState,
    classify_refund_state_delta,
)


def _state(order, receipt, amount, status):
    return RefundCurrentState(order, receipt, amount, status, "batch-1", f"sha-{order}")


def test_status_and_amount_changes_mark_affected_receipts_without_append_semantics():
    current = {
        "R-1": _state("R-1", "S-1", 100, "退款中"),
        "R-2": _state("R-2", "S-2", 200, "已退款"),
    }
    proposed = [
        _state("R-1", "S-1", 100, "已退款"),
        _state("R-2", "S-2", 250, "已退款"),
        _state("R-3", "S-1", 50, "退款中"),
    ]
    delta = classify_refund_state_delta(current, proposed)
    assert delta.status_changed_refund_order_nos == ("R-1",)
    assert delta.amount_changed_refund_order_nos == ("R-2",)
    assert delta.new_refund_order_nos == ("R-3",)
    assert delta.affected_source_receipt_nos == ("S-1", "S-2")
    assert delta.classification_counts["STATUS_CHANGED"] == 1


def test_identity_conflict_marks_old_and_new_receipts():
    delta = classify_refund_state_delta(
        {"R-1": _state("R-1", "S-1", 100, "退款中")},
        [_state("R-1", "S-9", 100, "已退款")],
    )
    assert delta.identity_conflict_refund_order_nos == ("R-1",)
    assert delta.affected_source_receipt_nos == ("S-1", "S-9")
