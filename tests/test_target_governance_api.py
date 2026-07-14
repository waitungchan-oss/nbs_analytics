from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routers import target_governance as target_router


def _payload():
    return {
        "version": "2026-07",
        "scope": "不含掛賬核銷與TT退款轉團款",
        "population": "全部正式分社＋正式四人專職銷售組",
        "approvalStatus": "draft",
        "updatedBy": "manager",
        "changeReason": "設定 2026-07 目標",
        "approvedBy": None,
        "thresholds": {"forecastGapPct": 0.05, "qualityWarningScore": 75, "qualityCriticalScore": 60},
        "targets": [{"id": "jul", "label": "2026-07 合計目標", "month": "2026-07", "scope": "combined", "targetRevenue": 10000000}],
    }


def test_target_config_api_get_returns_config_and_history(monkeypatch):
    monkeypatch.setattr(target_router, "load_target_config", lambda: {"status": "not_configured", "revision": 0, "targets": []})
    monkeypatch.setattr(target_router, "load_target_history", lambda: [{"revision": 1}])

    response = TestClient(create_app()).get("/api/decisions/targets")

    assert response.status_code == 200
    assert response.json()["config"]["status"] == "not_configured"
    assert response.json()["history"] == [{"revision": 1}]


def test_target_config_api_put_saves_and_returns_revision(monkeypatch):
    saved = {}
    monkeypatch.setattr(target_router, "save_target_config", lambda payload: saved.update(payload) or {**payload, "revision": 3, "status": "draft"})
    monkeypatch.setattr(target_router, "load_target_history", lambda: [])

    response = TestClient(create_app()).put("/api/decisions/targets", json=_payload())

    assert response.status_code == 200
    assert response.json()["config"]["revision"] == 3
    assert saved["targets"][0]["scope"] == "combined"


def test_target_config_api_rejects_invalid_business_scope_without_save(monkeypatch):
    called = False

    def fail_save(payload):
        nonlocal called
        called = True
        raise AssertionError("invalid config must not be saved")

    monkeypatch.setattr(target_router, "save_target_config", fail_save)
    payload = _payload()
    payload["scope"] = "自訂口徑"

    response = TestClient(create_app()).put("/api/decisions/targets", json=payload)

    assert response.status_code == 422
    assert called is False
