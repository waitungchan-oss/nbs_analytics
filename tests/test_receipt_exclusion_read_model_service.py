from backend.services.receipt_exclusion_read_model_service import build_receipt_exclusion_read_model
from backend.services.receipt_exclusion_registry_service import activate_receipt_exclusions


def test_read_model_is_bounded_and_hides_quarantine_payloads(tmp_path):
    db_path = tmp_path / "live.db"
    activation = activate_receipt_exclusions(
        [{
            "candidateId": "candidate-1", "receiptNo": "SK2606005393",
            "sourceOrderNo": "31NZY6629115617", "exclusionKind": "payment_method:TT 退款轉團款",
            "observedAmount": 1630.0, "rawPayload": {"收款單號": "SK2606005393"},
            "rawRowHash": "raw-hash", "preparedPayload": {"來源單據號": "31NZY6629115617"},
            "preparedRowHash": "prepared-hash", "sourceFileName": "main.xlsx",
            "sourceFileSha256": "file-hash",
        }],
        operation_id="op-1", created_by="test", proposal_fingerprint="p1", db_path=db_path,
    )
    model = build_receipt_exclusion_read_model(db_path=db_path, limit=1)

    assert model["counts"]["active"] == 1
    assert model["active"][0]["id"] == activation["ruleIds"][0]
    assert "rawPayload" not in model["active"][0]
    assert "preparedPayload" not in model["active"][0]
