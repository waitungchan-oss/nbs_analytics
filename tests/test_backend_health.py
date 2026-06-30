from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_check_returns_runtime_status():
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "nbs-analytics-api"
    assert "db" in payload
    assert "runtimeCache" in payload
    assert "latestAcceptance" in payload
    assert "storage" in payload
    assert "issues" in payload
    assert payload["storage"]["backups"]["capacityWarningBytes"] == 3 * 1024**3
    assert "operationalHistory" in payload


def test_root_returns_backend_entrypoints():
    client = TestClient(create_app())
    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "nbs-analytics-api"
    assert payload["frontend"] == "http://127.0.0.1:5173/"
    assert payload["docs"] == "/docs"
    assert payload["health"] == "/api/health"
