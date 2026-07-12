from pathlib import Path

from backend.services import system_health_service


def test_system_health_reports_operational_summary(monkeypatch, tmp_path):
    db_path = tmp_path / "nbs.db"
    db_path.write_bytes(b"sqlite")
    cache_path = tmp_path / ".nbs_runtime_cache"
    cache_path.mkdir()
    (cache_path / "cache.pkl").write_bytes(b"1234")
    (tmp_path / "nbs.db.backup_1").write_bytes(b"12")
    (tmp_path / "nbs.db.quarantine_1").write_bytes(b"123")
    monkeypatch.setattr(system_health_service, "validate_sqlite_database", lambda path: {"ok": True, "integrity": "ok"})
    monkeypatch.setattr(
        system_health_service,
        "list_stability_history",
        lambda limit=1, **kwargs: [
            {
                "id": 9,
                "createdAt": "2026-06-24T10:00:00+08:00",
                "coreStatus": "matched",
                "freshnessStatus": "updated",
                "latestDataDate": "2026-06-23",
                "uploadStatus": "accepted",
                "rollbackStatus": "not_required",
                "rollbackError": None,
                "driftDiagnosis": {"status": "drift"},
            }
        ],
    )

    payload = system_health_service.build_system_health(
        db_path=db_path,
        cache_path=cache_path,
    )

    assert payload["status"] == "ok"
    assert payload["db"]["integrity"] == "ok"
    assert payload["latestAcceptance"]["id"] == 9
    assert payload["latestAcceptance"]["latestDiagnosisSourceLabel"] == "Record #9 · 2026-06-24T10:00:00+08:00"
    assert payload["storage"]["backups"]["count"] == 1
    assert payload["storage"]["quarantines"]["count"] == 1
    assert payload["runtimeCache"]["fileCount"] == 1
    assert payload["uploadCoordination"]["locked"] is False
    assert payload["dataGeneration"]["generation"] >= 0
    assert payload["uploadEvidence"]["matched"] in {True, None}


def test_system_health_is_critical_when_sqlite_integrity_fails(monkeypatch, tmp_path):
    db_path = tmp_path / "nbs.db"
    db_path.write_bytes(b"broken")
    monkeypatch.setattr(
        system_health_service,
        "validate_sqlite_database",
        lambda path: {"ok": False, "integrity": "database disk image is malformed"},
    )
    monkeypatch.setattr(system_health_service, "list_stability_history", lambda limit=1, **kwargs: [])

    payload = system_health_service.build_system_health(
        db_path=db_path,
        cache_path=tmp_path / ".nbs_runtime_cache",
    )

    assert payload["status"] == "critical"
    assert "SQLite integrity check failed" in payload["issues"][0]
