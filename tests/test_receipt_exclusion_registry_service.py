import sqlite3

import pytest

from backend.services.receipt_exclusion_registry_service import (
    activate_receipt_exclusions,
    commit_receipt_exclusion_revocation,
    list_receipt_exclusions,
    load_active_registry_snapshot,
    record_auto_applied_events,
)


def _candidate():
    return {
        "candidateId": "candidate-1",
        "receiptNo": "SK2606005393",
        "sourceOrderNo": "31NZY6629115617",
        "exclusionKind": "payment_method:TT 退款轉團款",
        "observedAmount": 1630.0,
        "rawPayload": {"收款單號": "SK2606005393"},
        "rawRowHash": "raw-hash",
        "preparedPayload": {"來源單據號": "31NZY6629115617", "收款原幣金額": 1630.0},
        "preparedRowHash": "prepared-hash",
        "sourceFileName": "財務收款總數-0101-0722.xlsx",
        "sourceFileSha256": "file-hash",
        "reason": "confirmed exact excluded receipt",
    }


def test_activation_writes_registry_quarantine_and_event_atomically(tmp_path):
    db_path = tmp_path / "live.db"

    result = activate_receipt_exclusions(
        [_candidate()],
        operation_id="op-1",
        created_by="streamlit-local",
        proposal_fingerprint="proposal-hash",
        db_path=db_path,
    )

    assert result["status"] == "activated"
    assert len(result["ruleIds"]) == 1
    snapshot = load_active_registry_snapshot(db_path=db_path)
    assert snapshot["rules"][0].identity.receipt_no == "SK2606005393"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_quarantine").fetchone()[0] == 1
        assert conn.execute("SELECT event_type FROM receipt_exclusion_events").fetchone()[0] == "activated"


def test_activation_rolls_back_all_tables_when_quarantine_insert_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    from backend.services import receipt_exclusion_registry_service as service

    monkeypatch.setattr(
        service,
        "_insert_quarantine",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("forced")),
    )

    with pytest.raises(sqlite3.IntegrityError):
        activate_receipt_exclusions(
            [_candidate()],
            operation_id="op-1",
            created_by="streamlit-local",
            proposal_fingerprint="proposal-hash",
            db_path=db_path,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_registry").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_quarantine").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_events").fetchone()[0] == 0


def test_read_only_snapshot_does_not_create_tables(tmp_path):
    db_path = tmp_path / "live.db"
    sqlite3.connect(db_path).close()

    assert load_active_registry_snapshot(db_path=db_path)["rules"] == ()

    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "receipt_exclusion_registry" not in names


def test_duplicate_activation_returns_same_rule_without_duplicate_evidence(tmp_path):
    db_path = tmp_path / "live.db"
    first = activate_receipt_exclusions(
        [_candidate()], operation_id="op-1", created_by="streamlit-local",
        proposal_fingerprint="p1", db_path=db_path,
    )
    second = activate_receipt_exclusions(
        [_candidate()], operation_id="op-2", created_by="streamlit-local",
        proposal_fingerprint="p2", db_path=db_path,
    )

    assert second["ruleIds"] == first["ruleIds"]
    assert second["status"] == "already_active"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_quarantine").fetchone()[0] == 1


def test_auto_applied_event_is_idempotent_per_operation_rule_and_fingerprint(tmp_path):
    db_path = tmp_path / "live.db"
    activation = activate_receipt_exclusions(
        [_candidate()], operation_id="op-1", created_by="streamlit-local",
        proposal_fingerprint="p1", db_path=db_path,
    )
    event = {
        "registryId": activation["ruleIds"][0],
        "proposalFingerprint": "row-hash-1",
        "payload": {"rowHash": "row-hash-1"},
    }

    first = record_auto_applied_events([event], operation_id="op-2", db_path=db_path)
    second = record_auto_applied_events([event], operation_id="op-2", db_path=db_path)

    assert second == first
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM receipt_exclusion_events "
            "WHERE event_type='auto_applied'"
        ).fetchone()[0]
    assert count == 1


def test_revoke_updates_rule_and_event_in_one_transaction(tmp_path):
    db_path = tmp_path / "live.db"
    activation = activate_receipt_exclusions(
        [_candidate()], operation_id="op-1", created_by="streamlit-local",
        proposal_fingerprint="p1", db_path=db_path,
    )

    result = commit_receipt_exclusion_revocation(
        activation["ruleIds"][0],
        operation_id="revoke-1",
        revoked_by="streamlit-local",
        preview_fingerprint="preview-1",
        db_path=db_path,
    )

    assert result["status"] == "revoked"
    assert list_receipt_exclusions(status="active", db_path=db_path) == []
    assert list_receipt_exclusions(status="revoked", db_path=db_path)[0]["id"] == activation["ruleIds"][0]
