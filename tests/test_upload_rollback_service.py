from backend.services.upload_rollback_service import handle_core_drift_rollback


def _gate(status: str) -> dict:
    return {
        "status": status,
        "coreValidation": {
            "status": status,
            "summary": {
                "totalChecks": 2,
                "matchedChecks": 2 if status == "matched" else 1,
                "driftChecks": 0 if status == "matched" else 1,
            },
        },
    }


def test_matched_gate_is_accepted_without_restore():
    calls = []

    result = handle_core_drift_rollback(
        _gate("matched"),
        "backup.db",
        restore_database=lambda path: calls.append(("restore", path)),
        rebuild_cache=lambda: calls.append(("cache", None)),
        build_gate=lambda: _gate("matched"),
    )

    assert result == {
        "status": "accepted",
        "rollbackStatus": "not_required",
        "backupPath": "backup.db",
        "quarantinePath": None,
        "postRollbackGate": None,
        "rollbackError": None,
    }
    assert calls == []


def test_core_drift_restores_rebuilds_and_verifies():
    calls = []

    result = handle_core_drift_rollback(
        _gate("drift"),
        "backup.db",
        restore_database=lambda path: (
            calls.append(("restore", path))
            or {"status": "restored", "quarantine_path": "quarantine.db"}
        ),
        rebuild_cache=lambda: calls.append(("cache", None)),
        build_gate=lambda: calls.append(("gate", None)) or _gate("matched"),
    )

    assert result["status"] == "rejected_rolled_back"
    assert result["rollbackStatus"] == "verified"
    assert result["quarantinePath"] == "quarantine.db"
    assert result["postRollbackGate"]["status"] == "matched"
    assert calls == [("restore", "backup.db"), ("cache", None), ("gate", None)]


def test_post_restore_drift_is_reported_as_rollback_failed():
    result = handle_core_drift_rollback(
        _gate("drift"),
        "backup.db",
        restore_database=lambda path: {"status": "restored", "quarantine_path": "quarantine.db"},
        rebuild_cache=lambda: None,
        build_gate=lambda: _gate("drift"),
    )

    assert result["status"] == "rollback_failed"
    assert result["rollbackStatus"] == "verification_failed"
    assert "post-rollback core validation drift" in result["rollbackError"]
