import inspect

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routers import decisions as decisions_router
from backend.services.application_snapshot_service import (
    ApplicationSnapshot,
    SnapshotGenerationConflict,
)
from backend.services.business_rules_service import BusinessRulesSnapshot


def _snapshot() -> ApplicationSnapshot:
    return ApplicationSnapshot(
        generation_token="7:shared",
        rules=BusinessRulesSnapshot(
            branch_mapping_items=(("01", "Branch A"),),
            target_branches=("Branch A",),
            cruise_departments=(),
            sales_reps=(),
            fingerprint="rules-1",
        ),
        facts={
            "status": "ready",
            "generationToken": "7:shared",
            "revenueScope": "Scope",
            "monthlyTotals": [],
            "factsCacheStatus": "hit",
            "readModelCacheStatus": "hit",
        },
        forecast={"status": "ready", "cache": {"version": "forecast-v1"}},
        quality={
            "status": "ready",
            "overallScore": 100,
            "overallHealth": "優秀",
            "cacheStatus": "hit",
        },
        health={"status": "ok", "latestAcceptance": {}},
        targets={"status": "not_configured", "targets": [], "thresholds": {}},
        provenance={
            "generationToken": "7:shared",
            "coreGenerationConsistent": True,
            "snapshotAttemptCount": 1,
            "dbPath": "/tmp/live.db",
            "rulesFingerprint": "rules-1",
            "factsCacheStatus": "hit",
            "readModelCacheStatus": "hit",
            "dataQualityCacheStatus": "hit",
            "forecastStatus": "ready",
            "forecastCache": {"version": "forecast-v1"},
            "systemHealthStatus": "ok",
        },
    )


def test_decision_api_returns_typed_overview_from_application_snapshot(monkeypatch):
    monkeypatch.setattr(
        decisions_router.ApplicationSnapshotService,
        "build",
        lambda self: _snapshot(),
    )

    response = TestClient(create_app()).get("/api/decisions/overview")

    assert response.status_code == 200
    assert response.json()["targetConfig"]["status"] == "not_configured"
    assert response.json()["provenance"]["generationToken"] == "7:shared"
    assert response.json()["provenance"]["rulesFingerprint"] == "rules-1"
    assert response.json()["provenance"]["snapshotAttemptCount"] == 1
    assert response.json()["provenance"]["coreGenerationConsistent"] is True
    schema = create_app().openapi()
    ref = schema["paths"]["/api/decisions/overview"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/DecisionOverviewResponse")


def test_decision_api_maps_snapshot_generation_conflict_to_http_409(monkeypatch):
    def raise_conflict(self):
        raise SnapshotGenerationConflict(
            (("1:first", "2:second"), ("2:second", "3:third"))
        )

    monkeypatch.setattr(
        decisions_router.ApplicationSnapshotService,
        "build",
        raise_conflict,
    )

    response = TestClient(create_app()).get("/api/decisions/overview")

    assert response.status_code == 409
    assert "Data generation changed" in response.json()["detail"]


def test_decision_router_keeps_generation_orchestration_outside_transport_layer():
    source = inspect.getsource(decisions_router)

    assert "_current_rules" not in source
    assert "load_cache_generation" not in source
    assert "build_dashboard_facts_read_model" not in source
    assert "build_data_quality_cached" not in source
    assert "for _ in range(2)" not in source
