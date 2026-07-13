from pathlib import Path

from fastapi.testclient import TestClient

from config import DB_FILE
from backend.main import create_app


def _read_model():
    return {
        "status": "ready",
        "serviceVersion": "dashboard-facts-v1",
        "generationToken": "1:test",
        "cacheKey": "facts-key",
        "factsCacheStatus": "hit",
        "revenueScope": "不含掛賬核銷與TT退款轉團款",
        "scopeAudit": {"scope_label": "不含掛賬核銷與TT退款轉團款"},
        "kpiTotals": {
            "branchRevenue": 100.0,
            "specialistRevenue": 20.0,
            "combinedRevenue": 120.0,
            "tourRevenue": 100.0,
            "cruiseRevenue": 0.0,
            "ticketRevenue": 20.0,
        },
        "monthlyTotals": [
            {"month": "2026-05", "branchRevenue": 100.0, "specialistRevenue": 20.0, "combinedRevenue": 120.0}
        ],
        "branchRanking": [],
        "specialistRanking": [],
        "productTotals": [
            {"product": "旅行團", "revenue": 100.0, "sharePct": 83.33},
            {"product": "郵輪", "revenue": 0.0, "sharePct": 0.0},
            {"product": "票務", "revenue": 20.0, "sharePct": 16.67},
        ],
        "reconciliation": {"status": "matched", "combinedRevenue": 120.0, "checks": []},
    }


def test_dashboard_facts_api_returns_read_model(monkeypatch):
    seen = {}

    def fake_read_model(**kwargs):
        seen.update(kwargs)
        return _read_model()

    monkeypatch.setattr("backend.routers.dashboard.build_dashboard_facts_read_model", fake_read_model)
    monkeypatch.setattr(
        "backend.routers.dashboard.load_cache_generation",
        lambda **kwargs: {"cacheToken": "1:test"},
    )
    monkeypatch.setattr(
        "backend.routers.dashboard._current_rules",
        lambda: ({"01": "銅鑼灣分社"}, ["銅鑼灣分社"], [], ["YTLAU 刘元太"]),
    )

    response = TestClient(create_app()).get("/api/dashboard/facts")

    assert response.status_code == 200
    assert response.json()["generationToken"] == "1:test"
    assert response.json()["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
    assert "rawTour" not in response.json()
    assert seen["db_path"] == Path(DB_FILE)
    assert seen["generation_token"] == "1:test"


def test_dashboard_facts_openapi_has_named_response_contract():
    schema = create_app().openapi()
    ref = schema["paths"]["/api/dashboard/facts"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert ref.endswith("/DashboardFactsResponse")
