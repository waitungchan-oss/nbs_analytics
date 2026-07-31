import pandas as pd

from backend.services import dashboard_analytics_service


def _facts():
    branch = pd.DataFrame(
        [
            {"日期": "2026-05-01", "文本": "A分社", "旅行團": 100.0, "郵輪": 20.0, "票務": 30.0},
            {"日期": "2026-05-02", "文本": "B分社", "旅行團": 50.0, "郵輪": 10.0, "票務": 40.0},
            {"日期": "2026-06-01", "文本": "A分社", "旅行團": 80.0, "郵輪": 0.0, "票務": 20.0},
        ]
    )
    specialist = pd.DataFrame(
        [
            {"日期": "2026-05-01", "文本": "銷售甲", "旅行團": 200.0, "郵輪": 0.0, "票務": 10.0},
            {"日期": "2026-06-01", "文本": "銷售乙", "旅行團": 70.0, "郵輪": 0.0, "票務": 20.0},
        ]
    )
    return branch, specialist


def test_analytics_views_reconcile_to_same_filtered_total():
    branch, specialist = _facts()

    payload = dashboard_analytics_service.build_analytics_from_facts(
        branch,
        specialist,
        {
            "years": [2026],
            "months": ["2026-05"],
            "dateRange": ["2026-05-01", "2026-05-31"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        },
    )

    assert payload["annualSummary"] == [
        {
            "year": 2026,
            "branchRevenue": 250.0,
            "specialistRevenue": 210.0,
            "combinedRevenue": 460.0,
            "branchSharePct": 54.35,
            "specialistSharePct": 45.65,
        }
    ]
    assert payload["monthlyTrend"][0]["month"] == "2026-05"
    assert payload["monthlyTrend"][0]["combinedRevenue"] == 460.0
    assert len(payload["branchRanking"]) == 2
    assert payload["branchRanking"][0]["branch"] == "A分社"
    assert payload["specialistRanking"][0]["specialist"] == "銷售甲"
    assert payload["reconciliation"]["status"] == "matched"
    assert all(check["status"] == "matched" for check in payload["reconciliation"]["checks"])


def test_product_drilldown_reconciles_each_channel():
    branch, specialist = _facts()

    payload = dashboard_analytics_service.build_analytics_from_facts(
        branch,
        specialist,
        {
            "years": [2026],
            "months": [],
            "dateRange": ["2026-01-01", "2026-12-31"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        },
    )

    branch_products = payload["productDrilldown"]["branch"]
    specialist_products = payload["productDrilldown"]["specialist"]
    assert sum(row["revenue"] for row in branch_products) == 350.0
    assert sum(row["revenue"] for row in specialist_products) == 300.0
    assert {row["product"] for row in branch_products} == {"旅行團", "郵輪", "票務"}
    assert round(sum(row["sharePct"] for row in branch_products), 1) == 100.0


def test_empty_fact_frames_keep_analytics_schema_instead_of_raising_missing_date():
    payload = dashboard_analytics_service.build_analytics_from_facts(
        pd.DataFrame(),
        pd.DataFrame(),
        {
            "years": [2026],
            "months": ["2026-06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        },
    )

    assert payload["annualSummary"] == []
    assert payload["monthlyTrend"] == []
    assert payload["branchRanking"] == []
    assert payload["specialistRanking"] == []
    assert payload["reconciliation"]["status"] == "matched"
