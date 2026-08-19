import pandas as pd
import pytest

from app_workflows import _apply_gmv_refund_adjustments


def test_refunds_are_aggregated_by_source_order_and_allocated_without_removing_rows():
    tour = pd.DataFrame(
        [
            {"來源單據號": "ORDER-1", "收款原幣金額": 100.0},
            {"來源單據號": "ORDER-1", "收款原幣金額": 50.0},
        ]
    )
    others = pd.DataFrame(
        [{"來源單據號": "ORDER-2", "收款原幣金額": 80.0}]
    )
    refunds = pd.DataFrame(
        [
            {"來源單據號": "ORDER-1", "退款原幣金額": "30", "退款状态": "已退款"},
            {"來源單據號": "ORDER-1", "退款原幣金額": "20", "退款状态": "待退款"},
            {"來源單據號": "MISSING", "退款原幣金額": "10", "退款状态": "已退款"},
        ]
    )

    result = _apply_gmv_refund_adjustments(tour, others, refunds)

    assert len(result["tour"]) == 2
    assert len(result["others"]) == 1
    assert result["tour"]["收款原幣金額"].tolist() == [pytest.approx(66.6666666667), pytest.approx(33.3333333333)]
    assert result["others"]["收款原幣金額"].tolist() == [80.0]
    assert result["refund_total"] == pytest.approx(60.0)
    assert result["matched_source_ids"] == {"ORDER-1"}
    assert result["unmatched_source_ids"] == ["MISSING"]

    paid_result = _apply_gmv_refund_adjustments(tour, others, refunds, refund_status="已退款")
    assert paid_result["refund_total"] == pytest.approx(40.0)
    assert paid_result["tour"]["收款原幣金額"].sum() == pytest.approx(120.0)
    assert paid_result["unmatched_source_ids"] == ["MISSING"]


def test_refund_adjustment_clamps_over_refund_and_reports_excess():
    tour = pd.DataFrame([{"來源單據號": "ORDER-1", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [
            {"來源單據號": "ORDER-1", "退款原幣金額": "120", "退款状态": "已退款"},
        ]
    )

    result = _apply_gmv_refund_adjustments(tour, pd.DataFrame(), refunds)

    assert result["tour"]["收款原幣金額"].tolist() == [0.0]
    assert result["refund_total"] == pytest.approx(120.0)
    assert result["applied_refund_total"] == pytest.approx(100.0)
    assert result["over_refund_total"] == pytest.approx(20.0)
