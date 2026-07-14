from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routers import decisions as decisions_router


def test_decision_api_returns_typed_overview(monkeypatch):
    monkeypatch.setattr(decisions_router, "build_dashboard_facts_read_model", lambda **kwargs: {"status": "ready", "generationToken": "1:test", "revenueScope": "Scope", "monthlyTotals": []})
    monkeypatch.setattr(decisions_router, "build_forecast_read_model", lambda: {"status": "ready", "cache": {}})
    monkeypatch.setattr(decisions_router, "build_data_quality_cached", lambda **kwargs: {"status": "ready", "overallScore": 100, "overallHealth": "優秀"})
    monkeypatch.setattr(decisions_router, "build_system_health", lambda **kwargs: {"status": "ok", "latestAcceptance": {}})
    monkeypatch.setattr(decisions_router, "load_decision_targets", lambda: {"status": "not_configured", "targets": [], "thresholds": {}})
    monkeypatch.setattr(
        decisions_router,
        "build_decision_overview",
        lambda **kwargs: {
            "status": "ready",
            "message": "ok",
            "targetConfig": {"status": "not_configured"},
            "targets": [],
            "alerts": [],
            "decisions": [],
            "provenance": {"generationToken": "1:test"},
        },
    )

    response = TestClient(create_app()).get("/api/decisions/overview")

    assert response.status_code == 200
    assert response.json()["targetConfig"]["status"] == "not_configured"
    schema = create_app().openapi()
    ref = schema["paths"]["/api/decisions/overview"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/DecisionOverviewResponse")


def test_decision_api_uses_generation_aware_quality_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(decisions_router, "load_cache_generation", lambda **kwargs: {"cacheToken": "7:shared"})
    monkeypatch.setattr(
        decisions_router,
        "build_dashboard_facts_read_model",
        lambda **kwargs: {
            "status": "ready",
            "generationToken": kwargs["generation_token"],
            "revenueScope": "Scope",
            "monthlyTotals": [],
            "factsCacheStatus": "hit",
            "readModelCacheStatus": "hit",
        },
    )
    monkeypatch.setattr(
        decisions_router,
        "build_data_quality_cached",
        lambda **kwargs: (calls.append(kwargs) or {"status": "ready", "overallScore": 100, "cacheStatus": "hit"}),
        raising=False,
    )
    monkeypatch.setattr(decisions_router, "build_forecast_read_model", lambda: {"status": "ready", "cache": {}})
    monkeypatch.setattr(decisions_router, "build_system_health", lambda **kwargs: {"status": "ok", "latestAcceptance": {}})
    monkeypatch.setattr(decisions_router, "load_decision_targets", lambda: {"status": "not_configured", "targets": [], "thresholds": {}})

    response = TestClient(create_app()).get("/api/decisions/overview")

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["generation_token"] == "7:shared"
    assert calls[0]["db_path"] == Path(decisions_router.DB_FILE)
    assert response.json()["provenance"]["factsCacheStatus"] == "hit"
    assert response.json()["provenance"]["readModelCacheStatus"] == "hit"
    assert response.json()["provenance"]["dataQualityCacheStatus"] == "hit"
