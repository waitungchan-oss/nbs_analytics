import pytest
import pandas as pd

from backend.services.receipt_exclusion_governance_service import (
    confirm_receipt_exclusion_revocation,
    preview_receipt_exclusion_revocation,
    verify_receipt_exclusion_confirmation,
)
from backend.services.upload_lock_service import UploadOperation


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


def _operation():
    return UploadOperation("op-1", "test", 123, "2026-07-23T12:00:00+08:00", ("main.xlsx",))


def _evidence():
    return {
        "registryRevision": "r1",
        "tableName": "others_data",
        "preparedRowHash": "prepared-hash",
        "preparedPayload": {"來源單據號": "31NZY6629115617", "收款原幣金額": 1630.0},
    }


def test_revocation_preview_replays_prepared_quarantine_row_in_temp_db(tmp_path):
    calls = []
    result = preview_receipt_exclusion_revocation(
        7,
        operation=_operation(),
        live_db_path=tmp_path / "live.db",
        registry_reader=lambda **kwargs: {"revision": "r1", "rules": ()},
        evidence_loader=lambda *args, **kwargs: _evidence(),
        snapshotter=lambda source, destination: destination.touch(),
        upsert_runner=lambda tour, others, **kwargs: calls.append((tour, others, kwargs)),
        gate_builder=lambda **kwargs: {"status": "drift", "deltaAmount": -1270.0},
    )

    assert result["status"] == "revocation_blocked"
    assert result["deltaAmount"] == -1270.0
    assert result["previewFingerprint"]
    assert calls[0][0].empty
    assert calls[0][1].iloc[0]["來源單據號"] == "31NZY6629115617"


def test_confirm_rejects_changed_registry_revision_or_preview_fingerprint(tmp_path):
    preview = {
        "status": "revocation_ready", "registryRevision": "r1",
        "previewFingerprint": "preview-1",
    }
    with pytest.raises(ValueError, match="stale revocation preview"):
        confirm_receipt_exclusion_revocation(
            7, operation=_operation(), submitted_preview_fingerprint="preview-1",
            revoked_by="streamlit-local", live_db_path=tmp_path / "live.db",
            registry_reader=lambda **kwargs: {"revision": "r2", "rules": ()},
            preview_runner=lambda *args, **kwargs: preview,
        )


def test_matched_preview_commits_revocation_only_after_exact_replay(tmp_path):
    commits = []
    preview = {
        "status": "revocation_ready", "registryRevision": "r1",
        "previewFingerprint": "preview-1",
    }
    result = confirm_receipt_exclusion_revocation(
        7, operation=_operation(), submitted_preview_fingerprint="preview-1",
        revoked_by="streamlit-local", live_db_path=tmp_path / "live.db",
        registry_reader=lambda **kwargs: {"revision": "r1", "rules": ()},
        preview_runner=lambda *args, **kwargs: preview,
        revocation_committer=lambda *args, **kwargs: commits.append(kwargs) or {"status": "revoked", "ruleId": 7},
    )

    assert result["status"] == "revoked"
    assert commits[0]["preview_fingerprint"] == "preview-1"
