from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routers import upload as upload_router


def test_upload_api_accepts_files_and_returns_audit(monkeypatch):
    async def fake_run_vue_upload_action(*, main_file, tour_file=None, other_files=None):
        assert main_file.filename == "main.xlsx"
        assert tour_file.filename == "tour.xlsx"
        assert [item.filename for item in other_files or []] == ["other-a.xlsx", "other-b.xlsx"]
        return {
            "status": "success",
            "message": "上傳批次已寫入；SQLite 最新收款日期：2026-06-24。",
            "operationId": "op-api",
            "entryPoint": "fastapi",
            "sourceFiles": ["main.xlsx", "tour.xlsx", "other-a.xlsx", "other-b.xlsx"],
            "preflightReport": {
                "status": "matched",
                "message": "上傳預演通過：正式 SQLite 不會漂移，才會進入正式寫入。",
                "formattedExpectedTotal": "HKD 12,057,968",
                "formattedActualTotal": "HKD 12,057,968",
                "deltaAmount": 0,
                "writeRows": 2,
                "filteredExcludedRows": 0,
                "driftDiagnosis": {"status": "no_drift", "summaryMessage": "核心口徑未漂移。"},
            },
            "upsertSummary": {},
            "stabilityGate": {},
            "rollbackResult": {"rollbackStatus": "not_required"},
            "historyRecordId": 9,
            "historyError": None,
            "writeCommitted": True,
            "monthlyBaseline": {"allMatched": True},
            "cacheState": "invalidated",
            "cacheError": None,
            "dataGeneration": {},
            "stageTimings": [],
            "latestHealth": {"status": "ok"},
            "entityAudit": {},
            "anmRowCount": 0,
            "environment": {"python": "3.10"},
        }

    monkeypatch.setattr(upload_router, "run_vue_upload_action", fake_run_vue_upload_action)
    client = TestClient(create_app())

    response = client.post(
        "/api/upload",
        files=[
            ("main_file", ("main.xlsx", b"main", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("tour_file", ("tour.xlsx", b"tour", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("other_files", ("other-a.xlsx", b"other-a", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("other_files", ("other-b.xlsx", b"other-b", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["writeCommitted"] is True
    assert payload["historyRecordId"] == 9
    assert payload["preflightReport"]["status"] == "matched"
    assert payload["operationId"] == "op-api"
    assert payload["entryPoint"] == "fastapi"
    assert payload["cacheState"] == "invalidated"
    assert payload["monthlyBaseline"]["allMatched"] is True
    assert "已重建 dashboard cache" not in payload["message"]


def test_upload_api_openapi_contract_is_named():
    schema = create_app().openapi()
    upload_schema = schema["paths"]["/api/upload"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert upload_schema["$ref"].endswith("/UploadActionResponse")
