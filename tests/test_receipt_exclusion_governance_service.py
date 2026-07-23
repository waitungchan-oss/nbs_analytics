import pytest

from backend.services.receipt_exclusion_governance_service import (
    verify_receipt_exclusion_confirmation,
)


def test_confirmation_rejects_stale_proposal_fingerprint():
    with pytest.raises(ValueError, match="stale receipt exclusion proposal"):
        verify_receipt_exclusion_confirmation(
            canonical_proposal={"proposalFingerprint": "current", "candidates": []},
            private_evidence={},
            submitted_fingerprint="old",
            selected_candidate_ids=[],
        )


def test_confirmation_rejects_unknown_candidate():
    with pytest.raises(ValueError, match="unknown receipt exclusion candidate"):
        verify_receipt_exclusion_confirmation(
            canonical_proposal={
                "proposalFingerprint": "current",
                "candidates": [{"candidateId": "allowed"}],
            },
            private_evidence={"allowed": {"rawPayload": {}}},
            submitted_fingerprint="current",
            selected_candidate_ids=["forged"],
        )
