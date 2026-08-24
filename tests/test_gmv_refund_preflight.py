import pandas as pd
import pytest

from app_workflows import (
    _build_gmv_refund_exception_rows,
    _build_gmv_refund_preflight,
    _normalize_gmv_refund_rows,
)


def test_refund_rows_accept_simplified_status_and_return_canonical_columns():
    rows, metrics = _normalize_gmv_refund_rows(
        pd.DataFrame(
            [
                {"來源單據號": " A-1 ", "退款原幣金額": "30.5", "退款状态": "已退款"},
                {"來源單據號": "A-1", "退款原幣金額": "20", "退款状态": "待退款"},
            ]
        )
    )

    assert list(rows.columns[:3]) == ["來源單據號", "退款原幣金額", "退款狀態"]
    assert rows["來源單據號"].tolist() == ["A-1", "A-1"]
    assert rows["退款原幣金額"].tolist() == [pytest.approx(30.5), pytest.approx(20.0)]
    assert metrics["sourceOrders"] == 1
    assert metrics["statusCounts"] == {"已退款": 1, "待退款": 1}
    assert metrics["rows"] == 2


def test_refund_rows_report_blocking_schema_issue_without_required_columns():
    rows, metrics = _normalize_gmv_refund_rows(
        pd.DataFrame([{"來源單據號": "A-1", "退款原幣金額": "30"}])
    )

    assert rows.empty
    assert metrics["status"] == "blocked"
    assert "退款狀態" in metrics["missing"]


def test_refund_rows_count_duplicate_invalid_and_negative_values():
    rows, metrics = _normalize_gmv_refund_rows(
        pd.DataFrame(
            [
                {"來源單據號": "A-1", "退款原幣金額": "30", "退款狀態": "已退款"},
                {"來源單據號": "A-1", "退款原幣金額": "30", "退款狀態": "已退款"},
                {"來源單據號": "A-2", "退款原幣金額": "-5", "退款狀態": "待退款"},
                {"來源單據號": "A-3", "退款原幣金額": "bad", "退款狀態": "待退款"},
            ]
        )
    )

    assert len(rows) == 2
    assert metrics["duplicateRows"] == 1
    assert metrics["negativeAmountRows"] == 1
    assert metrics["invalidAmountRows"] == 1


def test_preflight_classifies_formal_excluded_and_missing_sources():
    raw = pd.DataFrame(
        [
            {
                "來源單據號": "FORMAL-1",
                "收款原幣金額": 100.0,
                "收款類型": "正常",
                "收款方式": "現金",
            },
            {
                "來源單據號": "EXCLUDED-1",
                "收款原幣金額": 80.0,
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
            },
        ]
    )
    refunds = pd.DataFrame(
        [
            {"來源單據號": "FORMAL-1", "退款原幣金額": 20.0, "退款狀態": "已退款"},
            {"來源單據號": "EXCLUDED-1", "退款原幣金額": 30.0, "退款狀態": "已退款"},
            {"來源單據號": "MISSING-1", "退款原幣金額": 40.0, "退款狀態": "已退款"},
        ]
    )
    formal = raw.loc[raw["收款類型"] != "掛賬核銷"].copy()

    report = _build_gmv_refund_preflight(
        raw,
        pd.DataFrame(),
        refunds,
        formal,
        pd.DataFrame(),
    )

    total = report["dimensions"]["總退款"]
    assert total["matchedFormalOrders"] == 1
    assert total["matchedExcludedOrders"] == 1
    assert total["unmatchedOrders"] == 1
    assert set(report["exceptionRows"]["匹配狀態"]) == {
        "正式口徑匹配",
        "被收入規則排除",
        "SQLite 找不到",
    }


def test_preflight_paid_dimension_uses_only_exact_paid_status():
    raw = pd.DataFrame([{"來源單據號": "A-1", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [
            {"來源單據號": "A-1", "退款原幣金額": 20.0, "退款狀態": "待退款"},
            {"來源單據號": "A-1", "退款原幣金額": 30.0, "退款狀態": "已退款"},
        ]
    )

    report = _build_gmv_refund_preflight(raw, pd.DataFrame(), refunds, raw, pd.DataFrame())

    assert report["dimensions"]["總退款"]["refundTotal"] == pytest.approx(50.0)
    assert report["dimensions"]["已退款"]["refundTotal"] == pytest.approx(30.0)
    assert set(report["exceptionRows"].loc[report["exceptionRows"]["退款維度"] == "已退款", "退款狀態"]) == {"已退款"}


def test_preflight_does_not_apply_revenue_scope_excluded_amounts():
    raw = pd.DataFrame(
        [
            {"來源單據號": "FORMAL", "收款原幣金額": 100.0, "收款類型": "正常"},
            {"來源單據號": "EXCLUDED", "收款原幣金額": 80.0, "收款類型": "掛賬核銷"},
        ]
    )
    formal = raw.loc[raw["收款類型"] != "掛賬核銷"].copy()
    refunds = pd.DataFrame(
        [
            {"來源單據號": "FORMAL", "退款原幣金額": 20.0, "退款狀態": "已退款"},
            {"來源單據號": "EXCLUDED", "退款原幣金額": 30.0, "退款狀態": "已退款"},
        ]
    )

    report = _build_gmv_refund_preflight(raw, pd.DataFrame(), refunds, formal, pd.DataFrame())

    excluded = report["exceptionRows"].loc[report["exceptionRows"]["來源單據號"] == "EXCLUDED"].iloc[0]
    assert bool(excluded["是否可扣減"]) is False
    assert excluded["實際扣減金額"] == pytest.approx(0.0)


def test_preflight_excludes_tt_refund_transfer_before_over_refund_cap():
    raw = pd.DataFrame([
        {
            "來源單據號": "03LBJ6727142533",
            "收款原幣金額": 9520.0,
            "收款類型": "旅費",
            "收款方式": "CR 信用咭",
        }
    ])
    refunds = pd.DataFrame([
        {
            "來源單據號": "03LBJ6727142533",
            "退款原幣金額": 4000.0,
            "退款狀態": "待退款",
            "退款方式": "TT 退款轉團款",
        },
        {
            "來源單據號": "03LBJ6727142533",
            "退款原幣金額": 9520.0,
            "退款狀態": "待退款",
            "退款方式": "TT 退款轉團款",
        },
        {
            "來源單據號": "03LBJ6727142533",
            "退款原幣金額": 100.0,
            "退款狀態": "已退款",
            "退款方式": "TT 退款轉團款",
        },
    ])

    report = _build_gmv_refund_preflight(raw, pd.DataFrame(), refunds, raw, pd.DataFrame())

    total = report["dimensions"]["總退款"]
    excluded = report["exceptionRows"].iloc[0]
    assert total["refundTotal"] == pytest.approx(0.0)
    assert total["appliedRefundTotal"] == pytest.approx(0.0)
    assert total["overRefundTotal"] == pytest.approx(0.0)
    paid = report["dimensions"]["已退款"]
    assert paid["refundTotal"] == pytest.approx(0.0)
    assert paid["appliedRefundTotal"] == pytest.approx(0.0)
    assert paid["overRefundTotal"] == pytest.approx(0.0)
    assert excluded["匹配狀態"] == "被收入規則排除"
    assert excluded["原因代碼"] == "REVENUE_SCOPE_EXCLUDED"
    assert bool(excluded["是否可扣減"]) is False
    assert excluded["實際扣減金額"] == pytest.approx(0.0)
    assert excluded["超額退款金額"] == pytest.approx(0.0)


def test_preflight_handles_all_sources_excluded_from_formal_scope():
    raw = pd.DataFrame(
        [{"來源單據號": "EXCLUDED", "收款原幣金額": 80.0, "收款類型": "掛賬核銷"}]
    )
    formal = raw.iloc[0:0].copy()
    refunds = pd.DataFrame(
        [{"來源單據號": "EXCLUDED", "退款原幣金額": 30.0, "退款狀態": "已退款"}]
    )

    report = _build_gmv_refund_preflight(raw, pd.DataFrame(), refunds, formal, pd.DataFrame())

    assert report["dimensions"]["總退款"]["matchedExcludedOrders"] == 1
    assert report["exceptionRows"].iloc[0]["匹配狀態"] == "被收入規則排除"


def test_preflight_counts_original_rows_when_source_id_is_empty():
    rows = pd.DataFrame(
        [
            {"來源單據號": "", "退款原幣金額": 10.0, "退款狀態": "已退款"},
            {"來源單據號": "A-1", "退款原幣金額": 20.0, "退款狀態": "已退款"},
        ]
    )

    report = _build_gmv_refund_preflight(rows, pd.DataFrame(), rows, rows, pd.DataFrame())

    assert report["fileMetrics"]["rows"] == 2
    assert report["fileMetrics"]["validRows"] == 1
    assert any(issue["code"] == "EMPTY_SOURCE_ORDER_ID" for issue in report["issues"])


def test_preflight_blocks_when_no_row_has_source_status_and_valid_amount():
    rows = pd.DataFrame(
        [{"來源單據號": "", "退款原幣金額": "bad", "退款狀態": ""}]
    )

    report = _build_gmv_refund_preflight(rows, pd.DataFrame(), rows, pd.DataFrame(), pd.DataFrame())

    assert report["status"] == "blocked"
    assert report["issues"][0]["code"] == "NO_USABLE_REFUND_ROWS"


def test_exception_actual_amount_uses_canonical_source_id():
    raw = pd.DataFrame([{"來源單據號": " A-1 ", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [{"來源單據號": "A-1", "退款原幣金額": 20.0, "退款狀態": "已退款"}]
    )

    rows = _build_gmv_refund_exception_rows(refunds, raw, pd.DataFrame(), raw, pd.DataFrame())

    assert rows.iloc[0]["實際扣減金額"] == pytest.approx(20.0)


def test_distinct_refund_rows_for_same_source_are_aggregated_not_duplicate():
    raw = pd.DataFrame([{"來源單據號": "A-1", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [
            {"來源單據號": "A-1", "退款原幣金額": 20.0, "退款狀態": "已退款"},
            {"來源單據號": "A-1", "退款原幣金額": 30.0, "退款狀態": "已退款"},
        ]
    )

    rows = _build_gmv_refund_exception_rows(refunds, raw, pd.DataFrame(), raw, pd.DataFrame())

    assert rows.iloc[0]["退款明細金額"] == pytest.approx(50.0)
    assert rows.iloc[0]["原因代碼"] == "FORMAL_MATCHED"


def test_exception_rows_include_required_audit_columns_and_over_refund_code():
    raw = pd.DataFrame([{"來源單據號": "A-1", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [{"來源單據號": "A-1", "退款原幣金額": 120.0, "退款狀態": "已退款"}]
    )

    rows = _build_gmv_refund_exception_rows(
        refunds,
        raw,
        pd.DataFrame(),
        raw,
        pd.DataFrame(),
    )

    assert {
        "退款維度",
        "來源單據號",
        "匹配狀態",
        "原因代碼",
        "超額退款金額",
    }.issubset(rows.columns)
    assert rows.iloc[0]["原因代碼"] == "OVER_REFUND"
