import json
from pathlib import Path

from backend.services import operational_monitor_service


def test_health_history_is_compact_bounded_and_tolerates_bad_lines(tmp_path):
    history_path = tmp_path / "health_history.jsonl"
    history_path.write_text("{bad json}\n", encoding="utf-8")

    for index in range(4):
        operational_monitor_service.append_health_snapshot(
            {
                "status": "ok",
                "db": {"integrity": "ok", "integrityOk": True},
                "latestAcceptance": {
                    "id": index,
                    "createdAt": f"2026-06-2{index}T10:00:00+08:00",
                    "latestDataDate": "2026-06-23",
                    "latestDiagnosisSourceLabel": f"Record #{index} · 2026-06-2{index}T10:00:00+08:00",
                    "gate": {"large": "must not be persisted"},
                },
                "storage": {"backups": {"count": 2, "totalBytes": 10}},
                "runtimeCache": {"fileCount": 1, "totalBytes": 5},
                "issues": [],
            },
            history_path=history_path,
            endpoint_probes={"api": {"ready": True, "responseMs": 12.5}},
            max_records=3,
        )

    records = operational_monitor_service.read_health_history(history_path, limit=10)

    assert [record["latestAcceptanceId"] for record in records] == [3, 2, 1]
    assert records[0]["latestAcceptance"]["latestDiagnosisSourceLabel"] == "Record #3 · 2026-06-23T10:00:00+08:00"
    assert records[0]["endpoints"]["api"]["responseMs"] == 12.5
    assert "gate" not in records[0]
    assert len(history_path.read_text(encoding="utf-8").splitlines()) == 3


def test_probe_endpoints_reports_failure_without_raising(monkeypatch):
    def fake_probe(url, timeout=2.0):
        if "8601" in url:
            raise OSError("connection refused")
        return {"ready": True, "statusCode": 200, "responseMs": 4.2}

    monkeypatch.setattr(operational_monitor_service, "_probe_endpoint", fake_probe)

    result = operational_monitor_service.probe_endpoints(
        {
            "streamlit": "http://127.0.0.1:8502/_stcore/health",
            "api": "http://127.0.0.1:8601/api/health",
        }
    )

    assert result["streamlit"]["ready"] is True
    assert result["api"]["ready"] is False
    assert "connection refused" in result["api"]["error"]
