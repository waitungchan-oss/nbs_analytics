import json

import pandas as pd

from backend.services.receipt_exclusion_proposal_service import (
    build_receipt_exclusion_proposal,
    validate_candidate_selection,
)


def _diagnosis(payment_method="TT 退款轉團款"):
    return {
        "status": "drift",
        "diagnosedCheckKey": "monthlyRevenue:2026-06",
        "expectedTotal": 9083241.29,
        "actualTotal": 9081971.29,
        "deltaAmount": -1270.0,
        "topDrivers": [{
            "sourceOrderNo": "31NZY6629115617",
            "receiptNo": "SK2606005393",
            "paymentMethod": payment_method,
            "paymentType": "旅費",
            "deltaAmount": -1270.0,
        }],
    }


def _row():
    return {
        "來源單據號": "31NZY6629115617",
        "收款單號": "SK2606005393",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
        "收款原幣金額": 1630.0,
        "統一日期": "2026-06-29",
    }


def _build(
    diagnosis=None,
    *,
    registry_revision="registry-hash",
    source_batch_fingerprint="batch-hash",
    live_db_identity="db-identity",
):
    row = _row()
    return build_receipt_exclusion_proposal(
        diagnosis=diagnosis or _diagnosis(),
        raw_main_frame=pd.DataFrame([row]),
        prepared_frames=[pd.DataFrame([row])],
        operation_id="op-1",
        source_files=["財務收款總數-0101-0722.xlsx"],
        source_batch_fingerprint=source_batch_fingerprint,
        registry_revision=registry_revision,
        live_db_identity=live_db_identity,
    )


def test_builds_exact_proposal_for_tt_driver():
    public, private = _build()

    assert public["status"] == "confirmation_required"
    assert public["candidates"][0]["receiptNo"] == "SK2606005393"
    assert "rawPayload" not in json.dumps(public, ensure_ascii=False)
    assert "preparedPayload" not in json.dumps(public, ensure_ascii=False)
    candidate_id = public["candidates"][0]["candidateId"]
    assert private[candidate_id]["preparedPayload"]["收款單號"] == "SK2606005393"
    assert validate_candidate_selection(public, [candidate_id]) == [public["candidates"][0]]


def test_normal_payment_driver_does_not_create_proposal():
    public, private = build_receipt_exclusion_proposal(
        diagnosis=_diagnosis(payment_method="現金"),
        raw_main_frame=pd.DataFrame(),
        prepared_frames=[],
        operation_id="op-1",
        source_files=["main.xlsx"],
        source_batch_fingerprint="batch-hash",
        registry_revision="registry-hash",
        live_db_identity="db-identity",
    )

    assert public == {}
    assert private == {}


def test_proposal_fingerprint_changes_with_every_required_context_input():
    first, _ = _build(registry_revision="r1")
    changed_registry, _ = _build(registry_revision="r2")
    changed_source, _ = _build(source_batch_fingerprint="batch-hash-2")
    changed_db, _ = _build(live_db_identity="db-identity-2")
    changed_gate, _ = _build(diagnosis={**_diagnosis(), "diagnosedCheckKey": "monthlyRevenue:2026-05"})

    assert first["proposalFingerprint"] != changed_registry["proposalFingerprint"]
    assert first["proposalFingerprint"] != changed_source["proposalFingerprint"]
    assert first["proposalFingerprint"] != changed_db["proposalFingerprint"]
    assert first["proposalFingerprint"] != changed_gate["proposalFingerprint"]


def test_ambiguous_or_missing_identity_does_not_create_proposal():
    row = _row()
    raw = pd.DataFrame([row, row])
    public, private = build_receipt_exclusion_proposal(
        diagnosis=_diagnosis(),
        raw_main_frame=raw,
        prepared_frames=[pd.DataFrame([row])],
        operation_id="op-1",
        source_files=["main.xlsx"],
        source_batch_fingerprint="batch-hash",
        registry_revision="r1",
        live_db_identity="db-identity",
    )

    assert public == {}
    assert private == {}


def test_incomplete_eligible_driver_blocks_the_entire_proposal():
    diagnosis = _diagnosis()
    diagnosis["topDrivers"].append({
        "sourceOrderNo": "",
        "receiptNo": "SK2606005394",
        "paymentMethod": "TT 退款轉團款",
        "paymentType": "旅費",
        "deltaAmount": -10.0,
    })

    public, private = _build(diagnosis=diagnosis)

    assert public == {}
    assert private == {}
