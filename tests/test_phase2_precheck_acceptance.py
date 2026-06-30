from pathlib import Path

from backend.services.dashboard_service import build_dashboard_summary


ROOT = Path(__file__).resolve().parents[1]


def test_2026_05_official_branch_plus_specialist_revenue_baseline():
    summary = build_dashboard_summary(
        {
            "years": [2026],
            "months": ["2026-05"],
            "dateRange": ["2026-05-01", "2026-05-31"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    totals = summary["revenueTotals"]
    assert summary["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
    assert round(totals["branchRevenue"]) == 6_658_144
    assert round(totals["specialistRevenue"]) == 5_399_824
    assert round(totals["combinedRevenue"]) == 12_057_968
    assert totals["formattedCombinedRevenue"] == "HKD 12,057,968"
    assert summary["stabilityBaseline"]["status"] == "matched"
    assert summary["stabilityBaseline"]["formattedExpectedTotal"] == "HKD 12,057,968"
    assert summary["stabilityBaseline"]["formattedActualTotal"] == "HKD 12,057,968"
    assert abs(summary["stabilityBaseline"]["deltaAmount"]) < 1
    assert summary["stabilityBaseline"]["summary"]["driftChecks"] == 0


def test_2026_05_official_branch_and_specialist_ranking_baselines():
    summary = build_dashboard_summary(
        {
            "years": [2026],
            "months": ["2026-05"],
            "dateRange": ["2026-05-01", "2026-05-31"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    branch_top5 = [(row["branch"], round(row["totalRevenue"])) for row in summary["branchRanking"][:5]]
    specialist_top4 = [(row["specialist"], round(row["totalRevenue"])) for row in summary["specialistRanking"][:4]]

    assert branch_top5 == [
        ("17荃灣綠楊坊分社", 1_705_339),
        ("36旺角銀行中心分社", 1_146_543),
        ("19沙田分社", 737_527),
        ("33銅鑼灣分社", 704_358),
        ("27屯門市廣場分社", 673_995),
    ]
    assert specialist_top4 == [
        ("YTLAU 刘元太", 4_421_710),
        ("SOGOR 苏清秩", 444_608),
        ("ELSA 谢玲玲", 329_056),
        ("JIA 江嘉韵", 204_450),
    ]


def test_data_freshness_advances_without_regressing_from_phase2_snapshot():
    summary = build_dashboard_summary(
        {
            "years": [2026],
            "months": ["2026-06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    assert summary["dataFreshness"]["maxDate"] >= "2026-06-22"
    assert summary["dataFreshness"]["minDate"] == "2025-01-01"
    assert summary["dataFreshness"]["analysisRows"] >= 26_640
    assert summary["stabilityBaseline"]["freshnessUpdate"]["status"] in {"stable", "updated"}


def test_phase2_precheck_acceptance_doc_records_revenue_baseline():
    doc = ROOT / "PHASE2_PRECHECK_ACCEPTANCE.md"
    text = doc.read_text(encoding="utf-8")

    assert "2026-05" in text
    assert "12,057,968" in text
    assert "Top 5 分社" in text
    assert "2026-06-22" in text
    assert "不含掛賬核銷與TT退款轉團款" in text
    assert "/api/dashboard/summary" in text
