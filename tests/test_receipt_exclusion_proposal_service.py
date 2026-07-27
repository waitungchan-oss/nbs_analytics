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
    operation_id="op-1",
    registry_revision="registry-hash",
    source_batch_fingerprint="batch-hash",
    live_db_identity="db-identity",
):
    row = _row()
    return build_receipt_exclusion_proposal(
        diagnosis=diagnosis or _diagnosis(),
        raw_main_frame=pd.DataFrame([row]),
        prepared_frames=[pd.DataFrame([row])],
        operation_id=operation_id,
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


def test_proposal_fingerprint_survives_a_new_upload_lease_for_the_same_batch():
    first, _ = _build(operation_id="lease-one")
    retried, _ = _build(operation_id="lease-two")

    assert first["operationId"] != retried["operationId"]
    assert first["proposalFingerprint"] == retried["proposalFingerprint"]


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


def test_equivalent_shifted_export_duplicate_creates_one_safe_proposal():
    aligned = _row()
    shifted = {
        "來源單據號": "31NZY6629115617",
        "收款單號": "SK2606005393",
        "原幣幣種": pd.NA,
        "匯率": "HKD 港幣",
        "收款原幣金額": "1",
        "收款本幣金額": "1630.00",
        "收款類型": "1630.00",
        "收款方式": "旅費",
        "收款流水號": "TT 退款轉團款",
        "Unnamed: 19": "中國簽證(2026年6月)",
    }

    public, private = build_receipt_exclusion_proposal(
        diagnosis=_diagnosis(),
        raw_main_frame=pd.DataFrame([shifted, aligned]),
        prepared_frames=[pd.DataFrame([shifted, aligned])],
        operation_id="op-1",
        source_files=["main.xlsx"],
        source_batch_fingerprint="batch-hash",
        registry_revision="r1",
        live_db_identity="db-identity",
    )

    assert public["status"] == "confirmation_required"
    assert len(public["candidates"]) == 1
    assert public["candidates"][0]["observedAmount"] == 1630.0
    candidate_id = public["candidates"][0]["candidateId"]
    assert private[candidate_id]["rawPayload"]["收款方式"] == "TT 退款轉團款"


def test_proposal_includes_every_excluded_receipt_for_the_diagnosed_source_order():
    source_order = "225YTLAU6227154715"
    excluded_rows = [
        {
            "來源單據號": source_order,
            "收款單號": receipt_no,
            "收款類型": "掛賬核銷",
            "收款方式": "BDR 銀行入數紙",
            "收款原幣金額": amount,
        }
        for receipt_no, amount in (
            ("SK2607007619", 237312.38),
            ("SK2607007621", 12687.62),
            ("SK2607007622", 50000.0),
        )
    ]
    unrelated = {
        **excluded_rows[0],
        "來源單據號": "UNRELATED",
        "收款單號": "SK-UNRELATED",
    }
    diagnosis = {
        "status": "drift",
        "diagnosedCheckKey": "monthlyRevenue:2026-03",
        "expectedTotal": 14628841.0,
        "actualTotal": 14578841.0,
        "deltaAmount": -50000.0,
        "topDrivers": [{
            "sourceOrderNo": source_order,
            "receiptNo": "SK2607007619",
            "paymentMethod": "BDR 銀行入數紙",
            "paymentType": "掛賬核銷",
            "deltaAmount": -50000.0,
        }],
    }

    public, private = build_receipt_exclusion_proposal(
        diagnosis=diagnosis,
        raw_main_frame=pd.DataFrame([*excluded_rows, unrelated]),
        prepared_frames=[pd.DataFrame([*excluded_rows, unrelated])],
        operation_id="op-1",
        source_files=["main.xlsx"],
        source_batch_fingerprint="batch-hash",
        registry_revision="r1",
        live_db_identity="db-identity",
    )

    assert [item["receiptNo"] for item in public["candidates"]] == [
        "SK2607007619",
        "SK2607007621",
        "SK2607007622",
    ]
    assert {item["observedAmount"] for item in public["candidates"]} == {
        237312.38,
        12687.62,
        50000.0,
    }
    assert len(private) == 3


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
