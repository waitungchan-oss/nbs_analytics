import pandas as pd

from backend.services.receipt_exclusion_models import (
    ReceiptExclusionIdentity,
    ReceiptExclusionRule,
)
from backend.services.receipt_exclusion_matcher import match_receipt_exclusions


def _rule() -> ReceiptExclusionRule:
    return ReceiptExclusionRule(
        id=7,
        identity=ReceiptExclusionIdentity(
            receipt_no="SK2606005393",
            source_order_no="31NZY6629115617",
            exclusion_kind="payment_method:TT 退款轉團款",
        ),
        status="active",
    )


def test_exact_active_rule_filters_only_target_receipt():
    frame = pd.DataFrame([
        {
            "收款單號": " sk2606005393　",
            "來源單據號": "31nzy6629115617",
            "收款方式": "TT 退款轉團款",
            "收款類型": "旅費",
            "收款原幣金額": 1630,
        },
        {
            "收款單號": "SK2606005395",
            "來源單據號": "31NZY6629115617",
            "收款方式": "現金",
            "收款類型": "旅費",
            "收款原幣金額": 1270,
        },
    ])

    result = match_receipt_exclusions(frame, [_rule()])

    assert result.filtered_frame["收款單號"].tolist() == ["SK2606005395"]
    assert result.matches[0]["registryId"] == 7
    assert result.matches[0]["receiptNo"] == "SK2606005393"
    assert result.collisions == ()


def test_same_receipt_with_different_order_is_collision_and_not_filtered():
    frame = pd.DataFrame([{
        "收款單號": "SK2606005393",
        "來源單據號": "DIFFERENT",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
    }])

    result = match_receipt_exclusions(frame, [_rule()])

    assert len(result.filtered_frame) == 1
    assert result.matches == ()
    assert result.collisions[0]["reason"] == "source_order_mismatch"


def test_corrected_normal_payment_is_collision_and_not_filtered():
    frame = pd.DataFrame([{
        "收款單號": "SK2606005393",
        "來源單據號": "31NZY6629115617",
        "收款方式": "現金",
        "收款類型": "旅費",
    }])

    result = match_receipt_exclusions(frame, [_rule()])

    assert len(result.filtered_frame) == 1
    assert result.collisions[0]["reason"] == "exclusion_kind_mismatch"


def test_shifted_export_duplicate_matches_the_same_exact_exclusion_identity():
    frame = pd.DataFrame([
        {
            "收款單號": "SK2606005393",
            "來源單據號": "31NZY6629115617",
            "原幣幣種": pd.NA,
            "匯率": "HKD 港幣",
            "收款原幣金額": "1",
            "收款本幣金額": "1630.00",
            "收款類型": "1630.00",
            "收款方式": "旅費",
            "收款流水號": "TT 退款轉團款",
            "Unnamed: 19": "中國簽證(2026年6月)",
        },
        {
            "收款單號": "SK2606005393",
            "來源單據號": "31NZY6629115617",
            "原幣幣種": "HKD 港幣",
            "匯率": "1",
            "收款原幣金額": "1630.00",
            "收款本幣金額": "1630.00",
            "收款類型": "旅費",
            "收款方式": "TT 退款轉團款",
        },
    ])

    result = match_receipt_exclusions(frame, [_rule()])

    assert result.filtered_frame.empty
    assert result.collisions == ()
    assert len(result.matches) == 2
    assert {item["observedAmount"] for item in result.matches} == {1630.0}
