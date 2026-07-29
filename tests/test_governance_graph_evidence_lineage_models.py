from __future__ import annotations

import json

import pytest

from backend.agents.governance_graph_evidence_lineage_models import (
    EVIDENCE_LINEAGE_INPUT_SCHEMA,
    EVIDENCE_LINEAGE_SCHEMA,
    EvidenceLineageInput,
    EvidenceLineageLink,
    EvidenceLineageResult,
    EvidenceLineageSchemaError,
    EvidenceIdentity,
)


SHA = "a" * 64


def _input(**overrides):
    payload = {
        "schemaVersion": EVIDENCE_LINEAGE_INPUT_SCHEMA,
        "runId": "run-123",
        "snapshotFingerprint": SHA,
        "source": {"kind": "node", "identity": "protected_incident"},
        "evidence": {"path": "protected-incident.json", "sha256": SHA},
    }
    payload.update(overrides)
    return payload


def _result(**overrides):
    payload = {
        "schemaVersion": EVIDENCE_LINEAGE_SCHEMA,
        "status": "available",
        "lineagePolicyVersion": "e1-canonical-evidence-lineage-v1",
        "runId": "run-123",
        "snapshotFingerprint": SHA,
        "source": {"kind": "node", "identity": "protected_incident"},
        "evidence": [
            {
                "path": "protected-incident.json",
                "sha256": SHA,
                "artifactKind": "protected_incident",
                "schemaVersion": "governance-canonical-evidence-v1",
                "writer": "protected_incident_recorder",
                "status": "available",
                "reasonCode": None,
                "finalizedAt": "2026-07-29T08:00:00+00:00",
                "fingerprintMatched": True,
            }
        ],
        "links": [
            {
                "relation": "node_evidence",
                "sourceIdentity": "protected_incident",
                "evidencePath": "protected-incident.json",
                "evidenceSha256": SHA,
            }
        ],
        "diagnostics": [],
        "lineageFingerprint": None,
    }
    payload.update(overrides)
    return payload


def test_input_accepts_single_explicit_evidence_and_normalizes_sha():
    request = EvidenceLineageInput.from_dict(_input(snapshotFingerprint=None))

    assert request.run_id == "run-123"
    assert request.source_kind == "node"
    assert request.evidence.path == "protected-incident.json"
    assert request.to_dict()["schemaVersion"] == EVIDENCE_LINEAGE_INPUT_SCHEMA


@pytest.mark.parametrize(
    "payload",
    [
        {**_input(), "extra": True},
        {**_input(), "runId": "../escape"},
        {**_input(), "source": {"kind": "node", "identity": "../guess"}},
        {**_input(), "evidence": {"path": "/tmp/secret.json", "sha256": SHA}},
        {**_input(), "evidence": {"path": "legacy.json", "sha256": "A" * 64}},
        {**_input(), "--approve": True},
        {**_input(), "rawPayload": {"secret": "value"}},
    ],
)
def test_input_rejects_unsafe_or_unknown_fields(payload):
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageInput.from_dict(payload)


def test_result_is_bounded_and_fingerprint_is_stable():
    result = EvidenceLineageResult.from_dict(_result()).with_fingerprint()
    first = result.to_dict()
    second = EvidenceLineageResult.from_dict(json.loads(json.dumps(first, sort_keys=True))).to_dict()

    assert first == second
    assert first["schemaVersion"] == EVIDENCE_LINEAGE_SCHEMA
    assert first["lineageFingerprint"] == second["lineageFingerprint"]
    assert len(first["evidence"]) <= 12
    assert len(first["links"]) == len(first["evidence"])


def test_result_nulls_fingerprint_when_identity_is_untrusted():
    result = EvidenceLineageResult.from_dict(
        _result(status="unknown", snapshotFingerprint=None, evidence=[], links=[])
    )

    assert result.to_dict()["snapshotFingerprint"] is None
    assert result.to_dict()["lineageFingerprint"] is None


def test_links_are_sorted_and_duplicates_are_rejected():
    link = EvidenceLineageLink.from_dict(
        {
            "relation": "node_evidence",
            "sourceIdentity": "protected_incident",
            "evidencePath": "protected-incident.json",
            "evidenceSha256": SHA,
        }
    )
    assert link.to_dict()["relation"] == "node_evidence"
    duplicate = _result(links=[link.to_dict(), link.to_dict()])
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(duplicate)


def test_result_rejects_more_than_twelve_evidence_entries():
    evidence = _result()["evidence"] * 13
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(evidence=evidence, links=[]))


def test_detail_must_match_registry_metadata_and_timestamp():
    detail = _result()["evidence"][0]
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(evidence=[{**detail, "writer": "fake_writer"}]))
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(evidence=[{**detail, "finalizedAt": "not-a-time"}]))


def test_untrusted_result_rejects_snapshot_fingerprint_and_secret_values():
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(status="unknown", evidence=[], links=[], snapshotFingerprint=SHA))
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageInput.from_dict(_input(source={"kind": "node", "identity": "ghp_secretvalue"}))


def test_public_constructor_cannot_bypass_contract():
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageLink("not-a-relation", "node", "task-gate.json", SHA)
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceIdentity("/tmp/secret", SHA)


def test_status_consistency_rejects_available_result_with_unmatched_detail():
    detail = _result()["evidence"][0]
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(evidence=[{**detail, "fingerprintMatched": False}]))


def test_result_rejects_mismatched_or_duplicate_lineage_pairs():
    detail = _result()["evidence"][0]
    other = {**detail, "path": "task-gate.json", "artifactKind": "task_gate", "writer": "task_gate_writer"}
    link = {**_result()["links"][0], "evidencePath": "task-gate.json", "evidenceSha256": "b" * 64}
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(evidence=[detail, other], links=[_result()["links"][0], link]))
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(evidence=[detail, detail], links=[_result()["links"][0], _result()["links"][0]]))


def test_fingerprint_mismatch_requires_unmatched_detail():
    detail = _result()["evidence"][0]
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult.from_dict(_result(status="fingerprint_mismatch", evidence=[detail]))


def test_result_constructor_rejects_nested_raw_values_and_wrong_fingerprint():
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult(
            "unknown", "run-123", None, "node", "node",
            ({"raw": "sk-secretvalue"},), (), ({"code": "sk-secretvalue", "summary": "x"},), None,
        )
    result = EvidenceLineageResult.from_dict(_result()).with_fingerprint()
    with pytest.raises(EvidenceLineageSchemaError):
        EvidenceLineageResult(
            result.status, result.run_id, result.snapshot_fingerprint, result.source_kind,
            result.source_identity, result.evidence, result.links, result.diagnostics, "b" * 64,
        )
