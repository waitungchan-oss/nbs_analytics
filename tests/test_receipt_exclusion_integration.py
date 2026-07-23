import hashlib
import sqlite3

import pandas as pd

from backend.services.receipt_exclusion_governance_service import preview_receipt_exclusion_revocation
from backend.services.receipt_exclusion_matcher import match_receipt_exclusions
from backend.services.receipt_exclusion_proposal_service import build_receipt_exclusion_proposal
from backend.services.receipt_exclusion_registry_service import (
    activate_receipt_exclusions,
    load_active_registry_snapshot,
)
from backend.services.upload_lock_service import UploadOperation


SOURCE_ORDER = "31NZY6629115617"
EXCLUDED_RECEIPT = "SK2606005393"
HISTORICAL_RECEIPT = "SK2606005395"
JUNE_EXPECTED = 9_083_241.29
MAY_EXPECTED = 12_057_967.92


def _operation():
    return UploadOperation("integration-op", "test", 123, "2026-07-23T12:00:00+08:00", ("main.xlsx",))


def _raw_frame():
    return pd.DataFrame([{
        "來源單據號": SOURCE_ORDER, "收款單號": EXCLUDED_RECEIPT,
        "收款方式": "TT 退款轉團款", "收款類型": "旅費", "收款原幣金額": 1270.0,
    }])


def _diagnosis():
    return {
        "status": "drift", "diagnosedCheckKey": "monthlyRevenue:2026-06",
        "expectedTotal": JUNE_EXPECTED, "actualTotal": JUNE_EXPECTED - 1270.0,
        "deltaAmount": -1270.0,
        "topDrivers": [{
            "sourceOrderNo": SOURCE_ORDER, "receiptNo": EXCLUDED_RECEIPT,
            "paymentMethod": "TT 退款轉團款", "paymentType": "旅費", "deltaAmount": -1270.0,
        }],
    }


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_disposable_receipt_exclusion_lifecycle_blocks_then_auto_applies_and_preserves_rule(tmp_path):
    db_path = tmp_path / "disposable.db"
    sqlite3.connect(db_path).close()
    raw = _raw_frame()
    public, private = build_receipt_exclusion_proposal(
        diagnosis=_diagnosis(), raw_main_frame=raw, prepared_frames=[pd.DataFrame(), raw],
        operation_id="integration-op", source_files=["main.xlsx"], source_batch_fingerprint="batch-1",
        registry_revision="empty", live_db_identity="disposable",
    )

    assert public["status"] == "confirmation_required"
    assert public["candidates"][0]["receiptNo"] == EXCLUDED_RECEIPT
    assert public["candidates"][0]["sourceOrderNo"] == SOURCE_ORDER
    before = _sha256(db_path)
    assert match_receipt_exclusions(raw, ()).filtered_frame.equals(raw)
    assert _sha256(db_path) == before

    candidate = {**public["candidates"][0], **private[public["candidates"][0]["candidateId"]]}
    candidate["reason"] = "integration confirmation"
    activated = activate_receipt_exclusions(
        [candidate], operation_id="integration-op", created_by="test",
        proposal_fingerprint=public["proposalFingerprint"], db_path=db_path,
    )
    snapshot = load_active_registry_snapshot(db_path=db_path)
    repeated = match_receipt_exclusions(raw, snapshot["rules"])

    assert activated["status"] == "activated"
    assert repeated.filtered_frame.empty
    assert len(repeated.matches) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_quarantine").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_events").fetchone()[0] == 1

    rule_id = activated["ruleIds"][0]
    preview = preview_receipt_exclusion_revocation(
        rule_id, operation=_operation(), live_db_path=db_path,
        registry_reader=lambda **kwargs: {"revision": snapshot["revision"], "rules": snapshot["rules"]},
        evidence_loader=lambda *args, **kwargs: {
            "preparedPayload": raw.iloc[0].to_dict(), "preparedRowHash": "prepared-hash",
            "tableName": "others_data",
        },
        snapshotter=lambda source, destination: destination.write_bytes(source.read_bytes()),
        upsert_runner=lambda *args, **kwargs: {},
        gate_builder=lambda **kwargs: {"status": "drift", "deltaAmount": -1270.0},
    )

    assert preview["status"] == "revocation_blocked"
    assert preview["deltaAmount"] == -1270.0
    assert load_active_registry_snapshot(db_path=db_path)["rules"][0].status == "active"
