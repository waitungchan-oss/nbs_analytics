from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.agents.agent_eval_manifest import live_preflight, planned_slots, validate_manifest, verify_binding
from backend.agents.evidence_models import canonical_fingerprint


CASE_IDS = [f"case-{index:02}" for index in range(12)]


def _manifest() -> dict:
    identity = {
        "projectId": "nbs_analytics", "consumerId": "context-agent", "provider": "local",
        "model": "fixture-v1", "settingsFingerprint": "a" * 64, "sourceCommit": "b" * 40,
        "dirtyFingerprint": "c" * 64, "workloadFingerprint": "d" * 64,
        "catalogFingerprint": "e" * 64, "policyFingerprint": "f" * 64,
        "allowedFilesFingerprint": "0" * 64, "commandsFingerprint": "1" * 64,
        "datasetFingerprint": "2" * 64, "rubricFingerprint": "3" * 64,
    }
    manifest = {
        "schemaVersion": "agent-eval-manifest-v1", "experimentId": "exp-1", "identity": identity,
        "caseIds": CASE_IDS, "splits": {"dev": 4, "holdout": 8}, "repeatCount": 3,
        "plannedSlots": 72, "orderPolicy": "alternating-v1", "isolationPolicy": "fresh-session-v1",
        "createdAt": "2026-09-08T00:00:00+00:00", "expiresAt": "2026-09-20T00:00:00+00:00",
        "artifactBudgetBytes": 33_554_432,
        "producerRegistry": {"fixture-v1": {"sourceSchema": "fixture-v1", "producerFingerprint": "4" * 64}},
        "authorization": {
            "scope": None, "runnerIdentity": None, "manifestFingerprint": None,
            "maxTaskTokens": None, "maxBatchTokens": None, "maxBatchCost": None,
            "currency": None, "priceTableFingerprint": None, "timeoutMs": None,
            "maxRetries": None,
        },
    }
    manifest["manifestFingerprint"] = canonical_fingerprint(manifest)
    return manifest


def test_every_expected_slot_is_retained():
    slots = planned_slots(CASE_IDS, 3)
    assert len(slots) == 72
    assert len({(s["taskId"], s["repeatIndex"], s["cohort"]) for s in slots}) == 72
    assert slots[0]["cohort"] == "recall_off"
    assert slots[2]["cohort"] == "recall_on"


def test_no_implicit_authorization():
    assert "blocked_missing_authorization" in live_preflight({}, authorization_evidence=None)


def test_manifest_is_fingerprinted_and_deep_copied():
    manifest = _manifest()
    result = validate_manifest(manifest)
    assert result == manifest
    assert result is not manifest


def test_manifest_drift_and_overlong_ttl_are_rejected():
    manifest = _manifest()
    manifest["identity"]["model"] = "other"
    with pytest.raises(ValueError, match="manifest_fingerprint_mismatch"):
        validate_manifest(manifest)
    manifest = _manifest()
    manifest["expiresAt"] = "2026-10-10T00:00:00+00:00"
    manifest["manifestFingerprint"] = canonical_fingerprint({k: v for k, v in manifest.items() if k != "manifestFingerprint"})
    with pytest.raises(ValueError, match="invalid_expiry"):
        validate_manifest(manifest)


def test_verify_binding_requires_manifest_bound_producer_and_artifact():
    manifest = _manifest()
    artifact = {"path": "observations/call.json", "sha256": "5" * 64}
    payload = {"identity": {**manifest["identity"], "taskId": CASE_IDS[0], "repeatIndex": 0, "cohort": "recall_off", "sessionId": "session-1"},
               "producerId": "fixture-v1", "sourceSchema": "fixture-v1",
               "producerFingerprint": "4" * 64, "artifactRef": artifact}
    result = verify_binding(payload, manifest=manifest, artifact_ref=artifact, producer_registry=manifest["producerRegistry"])
    assert result["manifestFingerprint"] == manifest["manifestFingerprint"]
    payload["producerFingerprint"] = "6" * 64
    with pytest.raises(ValueError, match="producer_binding_mismatch"):
        verify_binding(payload, manifest=manifest, artifact_ref=artifact, producer_registry=manifest["producerRegistry"])


def test_verify_binding_rejects_consumer_identity_mismatch():
    manifest = _manifest()
    artifact = {"path": "observations/call.json", "sha256": "5" * 64}
    payload = {"identity": {**manifest["identity"], "consumerId": "other-consumer", "cohort": "recall_off", "sessionId": "session-1"},
               "producerId": "fixture-v1", "sourceSchema": "fixture-v1",
               "producerFingerprint": "4" * 64, "artifactRef": artifact}
    with pytest.raises(ValueError, match="binding_mismatch"):
        verify_binding(payload, manifest=manifest, artifact_ref=artifact, producer_registry=manifest["producerRegistry"])


def test_live_preflight_rejects_missing_budget_and_session_reuse():
    manifest = _manifest()
    evidence = {"runnerIdentity": None, "manifestFingerprint": manifest["manifestFingerprint"],
                "maxTaskTokens": 1, "maxBatchTokens": 1, "maxBatchCost": 1,
                "currency": "HKD", "priceTableFingerprint": "6" * 64,
                "timeoutMs": 1, "maxRetries": 0, "sourceFingerprint": "d" * 64,
                "freshSession": False}
    assert live_preflight(manifest, authorization_evidence=evidence)[0] == "blocked_session_reuse"


def test_live_preflight_rejects_null_or_mismatched_authorization_values():
    manifest = _manifest()
    evidence = {"runnerIdentity": "runner", "manifestFingerprint": manifest["manifestFingerprint"],
                "maxTaskTokens": 1, "maxBatchTokens": 1, "maxBatchCost": 1,
                "currency": "HKD", "priceTableFingerprint": "6" * 64,
                "timeoutMs": 1, "maxRetries": 1, "sourceFingerprint": "d" * 64,
                "freshSession": True}
    assert "blocked_missing_budget" in live_preflight(manifest, authorization_evidence=evidence)


def test_live_preflight_rejects_non_boolean_fresh_session():
    manifest = _manifest()
    evidence = {"runnerIdentity": "runner", "manifestFingerprint": manifest["manifestFingerprint"],
                "maxTaskTokens": 1, "maxBatchTokens": 1, "maxBatchCost": 1,
                "currency": "HKD", "priceTableFingerprint": "6" * 64,
                "timeoutMs": 1, "maxRetries": 1, "sourceFingerprint": "d" * 64,
                "freshSession": "true"}
    assert "blocked_invalid_authorization" in live_preflight(manifest, authorization_evidence=evidence)


def test_live_preflight_rejects_expired_manifest():
    manifest = _manifest()
    manifest["expiresAt"] = "2026-09-09T00:00:00+00:00"
    manifest["manifestFingerprint"] = canonical_fingerprint(
        {key: value for key, value in manifest.items() if key != "manifestFingerprint"}
    )
    assert live_preflight(manifest, authorization_evidence={}) == ["blocked_manifest_expired"]


def test_verify_binding_rejects_unplanned_slot():
    manifest = _manifest()
    artifact = {"path": "observations/call.json", "sha256": "5" * 64}
    payload = {"identity": {**manifest["identity"], "taskId": "not-planned", "cohort": "recall_off", "sessionId": "session-1"},
               "producerId": "fixture-v1", "sourceSchema": "fixture-v1",
               "producerFingerprint": "4" * 64, "artifactRef": artifact}
    with pytest.raises(ValueError, match="invalid_slot"):
        verify_binding(payload, manifest=manifest, artifact_ref=artifact, producer_registry=manifest["producerRegistry"])
