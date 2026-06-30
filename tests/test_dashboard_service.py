import pandas as pd

from backend.services import dashboard_service


def _sample_frames():
    tour = pd.DataFrame(
        [
            {
                "來源單據號": "A001",
                "收款時間": "2026-06-01",
                "統一日期": "2026-06-01",
                "收款原幣金額": 1000,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
            {
                "來源單據號": "A002",
                "收款時間": "2026-06-02",
                "統一日期": "2026-06-02",
                "收款原幣金額": 500,
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
            {
                "來源單據號": "A003",
                "收款時間": "2026-06-04",
                "統一日期": "2026-06-04",
                "收款原幣金額": 700,
                "收款類型": "正常收款",
                "收款方式": "TT 退款轉團款",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "B001",
                "收款時間": "2026-06-03",
                "統一日期": "2026-06-03",
                "收款原幣金額": 300,
                "收款類型": "正常收款",
                "收款方式": "信用卡",
                "銷售點": "太古分社",
                "銷售員": "ELSA 谢玲玲",
                "目的地大類": "票務",
                "團負責人部門": "",
                "行程天數": 0,
                "數量": 1,
            }
        ]
    )
    return tour, others


def test_revenue_scope_excludes_writeoff_order():
    tour, others = _sample_frames()

    scoped_tour, scoped_others, audit = dashboard_service.build_revenue_scope_frames(tour, others)

    assert len(scoped_tour) == 1
    assert len(scoped_others) == 1
    assert audit["excluded_order_count"] == 2
    assert audit["excluded_rows"] == 2
    assert audit["analysis_amount"] == 1300


def test_dashboard_context_uses_read_only_loaded_frames(monkeypatch):
    tour, others = _sample_frames()
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))

    context = dashboard_service.build_dashboard_context()

    assert context["hasData"] is True
    assert context["tourRows"] == 3
    assert context["othersRows"] == 1
    assert context["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
    assert "2026-06" in context["months"]


def test_dashboard_summary_returns_kpis_without_export_generation(monkeypatch):
    tour, others = _sample_frames()
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))

    summary = dashboard_service.build_dashboard_summary(
        {
            "years": [2026],
            "months": ["2026-06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    assert summary["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
    assert len(summary["kpis"]) == 5
    assert summary["exportReadiness"]["lazyExport"] is True
    assert summary["exportReadiness"]["status"] == "not_loaded"
    assert set(summary["branchRanking"][0].keys()) == {
        "rank",
        "branch",
        "tourRevenue",
        "cruiseRevenue",
        "ticketRevenue",
        "totalRevenue",
        "sharePct",
    }


def test_dashboard_summary_accepts_month_number_filter(monkeypatch):
    tour, others = _sample_frames()
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))

    summary = dashboard_service.build_dashboard_summary(
        {
            "years": [2026],
            "months": ["06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    assert summary["kpis"][0]["value"] == "HKD 1,300"


def test_dashboard_summary_returns_combined_branch_and_specialist_totals(monkeypatch):
    tour, others = _sample_frames()
    specialist = tour.copy()
    specialist["銷售點"] = "營銷運營中心-專職銷售組"
    specialist["銷售員"] = "YTLAU 刘元太"
    specialist["收款原幣金額"] = 200
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (pd.concat([tour, specialist]), others))
    monkeypatch.setattr(
        dashboard_service,
        "load_business_rules",
        lambda: {
            "BRANCH_MAPPING": {"01": "銅鑼灣分社", "47": "太古分社", "225": "營銷運營中心-專職銷售組"},
            "TARGET_BRANCHES_S3": ["銅鑼灣分社", "太古分社"],
            "CRUISE_DEPTS": [],
            "SALES_REP_LIST": ["YTLAU 刘元太"],
        },
        raising=False,
    )

    summary = dashboard_service.build_dashboard_summary(
        {
            "years": [2026],
            "months": ["06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    assert summary["revenueTotals"] == {
        "branchRevenue": 1300.0,
        "specialistRevenue": 200.0,
        "combinedRevenue": 1500.0,
        "formattedCombinedRevenue": "HKD 1,500",
        "scope": "不含掛賬核銷與TT退款轉團款",
    }


def test_dashboard_summary_returns_phase2b_stability_baseline_for_official_month(monkeypatch):
    tour, others = _sample_frames()
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))
    monkeypatch.setattr(
        dashboard_service,
        "build_dashboard_data",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            pd.DataFrame(
                [
                    {
                        "日期": "2026-05-01",
                        "文本": "銅鑼灣分社",
                        "旅行團": 1000.0,
                        "郵輪": 200.0,
                        "票務": 300.0,
                    }
                ]
            ),
            pd.DataFrame(),
        ),
    )

    summary = dashboard_service.build_dashboard_summary(
        {
            "years": [2026],
            "months": ["2026-05"],
            "dateRange": ["2026-05-01", "2026-05-31"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    stability = summary["stabilityBaseline"]
    assert stability["name"] == "Phase 2B Stability Baseline"
    assert stability["baselineMonth"] == "2026-05"
    assert stability["status"] == "drift"
    assert stability["formattedExpectedTotal"] == "HKD 12,057,968"
    assert stability["formattedActualTotal"] == "HKD 1,500"
    assert stability["deltaAmount"] == -12_056_468.0
    assert stability["summary"] == {"totalChecks": 2, "matchedChecks": 1, "driftChecks": 1}
    assert stability["coreValidation"]["status"] == "drift"
    assert stability["coreValidation"]["summary"] == {"totalChecks": 2, "matchedChecks": 1, "driftChecks": 1}
    assert stability["freshnessUpdate"]["status"] == "updated"
    assert stability["freshnessUpdate"]["summary"] == {"totalChecks": 3, "stableChecks": 0, "updatedChecks": 3}
    assert {check["key"] for check in stability["checks"]} == {
        "combinedRevenue",
        "revenueScope",
        "maxDate",
        "analysisRows",
        "excludedRows",
    }
    assert stability["checks"][0]["status"] == "drift"


def test_stability_baseline_treats_new_dates_and_rows_as_non_blocking_updates():
    stability = dashboard_service.build_stability_baseline(
        {
            "combinedRevenue": 12_057_967.92,
            "scope": "不含掛賬核銷與TT退款轉團款",
        },
        {
            "scope": "不含掛賬核銷與TT退款轉團款",
            "maxDate": "2026-06-23",
            "analysisRows": 26_729,
            "excludedRows": 551,
        },
    )

    assert stability["status"] == "matched"
    assert stability["summary"] == {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0}
    assert stability["coreValidation"]["status"] == "matched"
    assert stability["freshnessUpdate"]["status"] == "updated"
    assert stability["freshnessUpdate"]["summary"] == {"totalChecks": 3, "stableChecks": 0, "updatedChecks": 3}
    assert {check["status"] for check in stability["freshnessUpdate"]["checks"]} == {"updated"}


def test_phase2c_stability_gate_wraps_matched_baseline(monkeypatch):
    monkeypatch.setattr(
        dashboard_service,
        "build_dashboard_summary",
        lambda filters: {
            "stabilityBaseline": {
                "status": "matched",
                "baselineMonth": "2026-05",
                "formattedExpectedTotal": "HKD 12,057,968",
                "formattedActualTotal": "HKD 12,057,968",
                "deltaAmount": -0.08,
                "deltaPct": 0.0,
                "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0},
                "checks": [{"key": "combinedRevenue", "status": "matched"}],
                "coreValidation": {
                    "status": "matched",
                    "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0},
                    "checks": [{"key": "combinedRevenue", "status": "matched"}],
                },
                "freshnessUpdate": {
                    "status": "updated",
                    "summary": {"totalChecks": 3, "stableChecks": 0, "updatedChecks": 3},
                    "checks": [{"key": "maxDate", "status": "updated"}],
                },
            }
        },
    )

    gate = dashboard_service.build_phase2c_stability_gate()

    assert gate["status"] == "matched"
    assert gate["label"] == "Phase 2C Upload Rebuild Stability Gate"
    assert gate["message"] == "重建成功，核心口徑穩定：2/2 checks matched；資料已更新 3 項。"
    assert gate["baselineMonth"] == "2026-05"
    assert gate["formattedActualTotal"] == "HKD 12,057,968"
    assert gate["freshnessStatus"] == "updated"
    assert gate["freshnessUpdateCount"] == 3


def test_phase2c_stability_gate_surfaces_drift_message(monkeypatch):
    monkeypatch.setattr(
        dashboard_service,
        "build_dashboard_summary",
        lambda filters: {
            "stabilityBaseline": {
                "status": "drift",
                "baselineMonth": "2026-05",
                "formattedExpectedTotal": "HKD 12,057,968",
                "formattedActualTotal": "HKD 12,000,000",
                "deltaAmount": -57968.0,
                "deltaPct": -0.4808,
                "summary": {"totalChecks": 2, "matchedChecks": 1, "driftChecks": 1},
                "checks": [
                    {"key": "combinedRevenue", "label": "2026-05 分社 + 專職總營收", "status": "drift"}
                ],
                "coreValidation": {
                    "status": "drift",
                    "summary": {"totalChecks": 2, "matchedChecks": 1, "driftChecks": 1},
                    "checks": [
                        {"key": "combinedRevenue", "label": "2026-05 分社 + 專職總營收", "status": "drift"}
                    ],
                },
                "freshnessUpdate": {
                    "status": "stable",
                    "summary": {"totalChecks": 3, "stableChecks": 3, "updatedChecks": 0},
                    "checks": [],
                },
            }
        },
    )

    gate = dashboard_service.build_phase2c_stability_gate()

    assert gate["status"] == "drift"
    assert gate["message"] == "重建完成，但核心口徑出現漂移：1/2 checks drift。"
    assert gate["driftCheckCount"] == 1
    assert gate["driftChecks"] == [
        {"key": "combinedRevenue", "label": "2026-05 分社 + 專職總營收", "status": "drift"}
    ]


def test_dashboard_summary_filters_rankings_and_returns_freshness(monkeypatch):
    tour, others = _sample_frames()
    july = others.copy()
    july["統一日期"] = "2026-07-01"
    july["收款時間"] = "2026-07-01"
    july["收款原幣金額"] = 9999
    specialist = tour.iloc[[0]].copy()
    specialist["銷售點"] = "營銷運營中心-專職銷售組"
    specialist["銷售員"] = "YTLAU 刘元太"
    specialist["收款原幣金額"] = 200
    monkeypatch.setattr(
        dashboard_service,
        "load_all_data_from_db",
        lambda: (pd.concat([tour, specialist]), pd.concat([others, july])),
    )
    monkeypatch.setattr(
        dashboard_service,
        "load_business_rules",
        lambda: {
            "BRANCH_MAPPING": {"01": "銅鑼灣分社", "47": "太古分社", "225": "營銷運營中心-專職銷售組"},
            "TARGET_BRANCHES_S3": ["銅鑼灣分社", "太古分社"],
            "CRUISE_DEPTS": [],
            "SALES_REP_LIST": ["YTLAU 刘元太"],
        },
        raising=False,
    )

    summary = dashboard_service.build_dashboard_summary(
        {
            "years": [2026],
            "months": ["06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    assert summary["branchRanking"] == [
        {
            "rank": 1,
            "branch": "01銅鑼灣分社",
            "tourRevenue": 1000.0,
            "cruiseRevenue": 0.0,
            "ticketRevenue": 0.0,
            "totalRevenue": 1000.0,
            "sharePct": 76.92,
        },
        {
            "rank": 2,
            "branch": "47太古分社",
            "tourRevenue": 0.0,
            "cruiseRevenue": 0.0,
            "ticketRevenue": 300.0,
            "totalRevenue": 300.0,
            "sharePct": 23.08,
        },
    ]
    assert summary["specialistRanking"][0]["specialist"] == "YTLAU 刘元太"
    assert summary["specialistRanking"][0]["totalRevenue"] == 200.0
    assert summary["dataFreshness"]["maxDate"] == "2026-07-01"
    assert summary["dataFreshness"]["analysisRows"] == 4
    assert summary["dataFreshness"]["scope"] == "不含掛賬核銷與TT退款轉團款"


def test_dashboard_context_uses_persisted_business_rules(monkeypatch):
    tour, others = _sample_frames()
    captured = {}

    def fake_build_dashboard_data(df_tour, df_others, branch_mapping, target_branches, cruise_depts, sales_reps, make_workbook=False):
        captured["branch_mapping"] = branch_mapping
        captured["target_branches"] = target_branches
        captured["cruise_depts"] = cruise_depts
        captured["sales_reps"] = sales_reps
        return None, pd.DataFrame({"文本": ["測試分社"]}), pd.DataFrame()

    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))
    monkeypatch.setattr(
        dashboard_service,
        "load_business_rules",
        lambda: {
            "BRANCH_MAPPING": {"ZZ": "測試分社"},
            "TARGET_BRANCHES_S3": ["測試分社"],
            "CRUISE_DEPTS": ["測試郵輪部門"],
            "SALES_REP_LIST": ["TEST REP"],
        },
        raising=False,
    )
    monkeypatch.setattr(dashboard_service, "build_dashboard_data", fake_build_dashboard_data)

    context = dashboard_service.build_dashboard_context()

    assert context["branches"] == ["測試分社"]
    assert captured["branch_mapping"] == {"ZZ": "測試分社"}
    assert captured["target_branches"] == ["測試分社"]
    assert captured["cruise_depts"] == ["測試郵輪部門"]
    assert captured["sales_reps"] == ["TEST REP"]
