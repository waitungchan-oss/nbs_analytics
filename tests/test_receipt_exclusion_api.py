from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.main import create_app


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_confirmation_endpoint_requires_files_fingerprint_and_selected_ids(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.upload.run_vue_upload_action",
        AsyncMock(return_value={
            "status": "accepted", "message": "ok", "operationId": "op-1", "entryPoint": "fastapi",
            "sourceFiles": ["main.xlsx"], "preflightReport": {}, "writeCommitted": False,
            "cacheState": "unchanged", "latestHealth": {}, "receiptExclusion": {"activatedRuleIds": [7]},
        }),
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/upload/receipt-exclusions/confirm",
        data={"proposal_fingerprint": "proposal-1", "selected_candidate_ids": '["candidate-1"]'},
        files=[("main_file", ("main.xlsx", b"main", XLSX_MIME))],
    )
    assert response.status_code == 200
    assert response.json()["receiptExclusion"]["activatedRuleIds"] == [7]


def test_confirmation_rejects_invalid_candidate_json():
    client = TestClient(create_app())
    response = client.post(
        "/api/upload/receipt-exclusions/confirm",
        data={"proposal_fingerprint": "proposal-1", "selected_candidate_ids": "not-json"},
        files=[("main_file", ("main.xlsx", b"main", XLSX_MIME))],
    )
    assert response.status_code == 400


def test_registry_list_and_revocation_routes_are_named_in_openapi():
    schema = create_app().openapi()["paths"]
    assert "/api/upload/receipt-exclusions" in schema
    assert "/api/upload/receipt-exclusions/{rule_id}/revoke" in schema
