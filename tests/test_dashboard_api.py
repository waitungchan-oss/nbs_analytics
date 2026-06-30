from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services import dashboard_service


def test_dashboard_context_api(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.dashboard.build_dashboard_context",
        lambda: {
            "hasData": True,
            "tourRows": 100,
            "othersRows": 50,
            "maxDate": "2026-06-15",
            "minDate": "2026-01-01",
            "years": [2026],
            "months": ["2026-06"],
            "branches": ["A", "B"],
            "salesGroups": ["X", "Y"],
            "revenueScope": "Scope Label",
        },
    )
    client = TestClient(create_app())
    response = client.get("/api/dashboard/context")
    assert response.status_code == 200
    assert response.json()["hasData"] is True


def test_dashboard_summary_api(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.dashboard.build_dashboard_summary",
        lambda filters: {
            "appliedFilters": filters,
            "revenueScope": "Scope Label",
            "scopeAudit": {},
            "kpis": [],
            "revenueTotals": {
                "branchRevenue": 100.0,
                "specialistRevenue": 20.0,
                "combinedRevenue": 120.0,
                "formattedCombinedRevenue": "HKD 120",
                "scope": "Scope Label",
            },
            "dataFreshness": {
                "minDate": "2026-01-01",
                "maxDate": "2026-06-30",
                "rawRows": 150,
                "analysisRows": 148,
                "excludedRows": 2,
                "scope": "Scope Label",
            },
            "stabilityBaseline": {
                "name": "Phase 2B Stability Baseline",
                "baselineMonth": "2026-05",
                "status": "matched",
                "formattedExpectedTotal": "HKD 12,057,968",
                "formattedActualTotal": "HKD 12,057,968",
                "expectedTotal": 12057968.0,
                "actualTotal": 12057967.92,
                "deltaAmount": -0.08,
                "deltaPct": 0.0,
                "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0},
                "checks": [],
                "coreValidation": {
                    "status": "matched",
                    "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0},
                    "checks": [],
                },
                "freshnessUpdate": {
                    "status": "stable",
                    "summary": {"totalChecks": 3, "stableChecks": 3, "updatedChecks": 0},
                    "checks": [],
                },
            },
            "branchRanking": [],
            "specialistRanking": [],
            "productMix": [],
            "exportReadiness": {"lazyExport": True, "status": "not_loaded", "message": ""},
        },
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/dashboard/summary",
        json={"years": [2026], "months": ["2026-06"], "dateRange": [], "branch": "全部分社", "salesGroup": "全部銷售組"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["revenueScope"] == "Scope Label"
    assert payload["appliedFilters"]["years"] == [2026]


def test_dashboard_summary_contract_has_fixed_kpi_and_ranking_fields(monkeypatch):
    import pandas as pd

    from backend.services import dashboard_service

    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(
        dashboard_service,
        "build_dashboard_data",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            pd.DataFrame(
                [
                    {"文本": "銅鑼灣分社", "旅行團": 100.0, "郵輪": 20.0, "票務": 30.0},
                    {"文本": "太古分社", "旅行團": 50.0, "郵輪": 10.0, "票務": 5.0},
                ]
            ),
            pd.DataFrame([{"類型": "A"}]),
        ),
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/dashboard/summary",
        json={
            "years": [2026],
            "months": ["2026-06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        },
    )

    payload = response.json()
    assert payload["kpis"][0]["label"] == "淨營收"
    assert set(payload["kpis"][0].keys()) == {"label", "value", "delta", "note", "accent"}
    assert set(payload["revenueTotals"].keys()) == {
        "branchRevenue",
        "specialistRevenue",
        "combinedRevenue",
        "formattedCombinedRevenue",
        "scope",
    }
    assert set(payload["dataFreshness"].keys()) == {
        "minDate",
        "maxDate",
        "rawRows",
        "analysisRows",
        "excludedRows",
        "scope",
    }
    assert set(payload["stabilityBaseline"].keys()) == {
        "name",
        "baselineMonth",
        "status",
        "formattedExpectedTotal",
        "formattedActualTotal",
        "expectedTotal",
        "actualTotal",
        "deltaAmount",
        "deltaPct",
        "summary",
        "checks",
        "coreValidation",
        "freshnessUpdate",
    }
    assert set(payload["branchRanking"][0].keys()) == {
        "rank",
        "branch",
        "tourRevenue",
        "cruiseRevenue",
        "ticketRevenue",
        "totalRevenue",
        "sharePct",
    }
    assert "specialistRanking" in payload


def test_dashboard_analytics_api_has_fixed_contract(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.dashboard.build_dashboard_analytics",
        lambda filters: {
            "appliedFilters": filters,
            "revenueScope": "Scope Label",
            "annualSummary": [],
            "monthlyTrend": [],
            "branchRanking": [],
            "specialistRanking": [],
            "productDrilldown": {"branch": [], "specialist": []},
            "reconciliation": {"status": "matched", "combinedRevenue": 0.0, "checks": []},
        },
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/dashboard/analytics",
        json={
            "years": [2026],
            "months": ["2026-05"],
            "dateRange": ["2026-05-01", "2026-05-31"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "appliedFilters",
        "revenueScope",
        "annualSummary",
        "monthlyTrend",
        "branchRanking",
        "specialistRanking",
        "productDrilldown",
        "reconciliation",
    }


def test_insights_endpoints_return_read_only_contracts(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.insights.build_data_quality",
        lambda: {"status": "ready", "scope": "Scope", "overallScore": 90.0, "overallHealth": "優秀", "latestDate": "2026-06-24", "missingDays": 0, "unmatchedRows": 1, "excludedAmountRate": 0.1, "dimensions": [], "fieldCompleteness": []},
    )
    monkeypatch.setattr(
        "backend.routers.insights.build_forecast_read_model",
        lambda: {"status": "ready", "message": "", "scope": "Scope", "cache": {}, "weights": [], "daily": [], "sevenDay": None, "monthEnd": None, "health": {}},
    )
    client = TestClient(create_app())

    quality = client.get("/api/insights/data-quality")
    forecast = client.get("/api/insights/forecast")

    assert quality.status_code == 200
    assert forecast.status_code == 200
    assert quality.json()["overallScore"] == 90.0
    assert forecast.json()["status"] == "ready"


def test_insights_openapi_has_named_response_contracts():
    schema = create_app().openapi()
    quality_schema = schema["paths"]["/api/insights/data-quality"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    forecast_schema = schema["paths"]["/api/insights/forecast"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert quality_schema["$ref"].endswith("/DataQualityResponse")
    assert forecast_schema["$ref"].endswith("/ForecastResponse")


def test_stability_history_api_returns_newest_records(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.stability.list_stability_history",
        lambda limit=20: [
            {
                "id": 7,
                "createdAt": "2026-06-24T10:00:00+08:00",
                "uploadStatus": "success",
                "uploadMessage": "上傳完成",
                "sourceFiles": ["main.xlsx"],
                "coreStatus": "matched",
                "baselineMonth": "2026-05",
                "formattedExpectedTotal": "HKD 12,057,968",
                "formattedActualTotal": "HKD 12,057,968",
                "deltaAmount": -0.08,
                "matchedChecks": 2,
                "totalChecks": 2,
                "driftCheckCount": 0,
                "freshnessStatus": "updated",
                "freshnessUpdateCount": 3,
                "latestDataDate": "2026-06-23",
                "batchSummary": [],
                "upsertSummary": [],
                "driftDiagnosis": {"status": "no_drift", "summaryMessage": "核心口徑未漂移。"},
                "gate": {},
                "rollbackStatus": "verified",
                "backupPath": "backup.db",
                "quarantinePath": "quarantine.db",
                "postRollbackGate": {"status": "matched"},
                "rollbackError": None,
            }
        ],
    )
    client = TestClient(create_app())

    response = client.get("/api/stability/history?limit=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == 7
    assert response.json()["items"][0]["coreStatus"] == "matched"
    assert response.json()["items"][0]["freshnessStatus"] == "updated"
    assert response.json()["items"][0]["rollbackStatus"] == "verified"
    assert response.json()["items"][0]["driftDiagnosis"]["status"] == "no_drift"
