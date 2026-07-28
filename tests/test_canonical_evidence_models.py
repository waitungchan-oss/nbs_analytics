import hashlib
import json

import pytest

from backend.agents.canonical_evidence_models import (
    CanonicalEvidenceEnvelope,
    CanonicalEvidenceSchemaError,
)
from backend.agents.canonical_evidence_registry import CanonicalEvidenceRegistry


def _sha256_without_evidence_fingerprint(payload):
    unsigned = dict(payload)
    unsigned.pop("evidenceFingerprint")
    return hashlib.sha256(json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _task_gate_payload(**overrides):
    payload = {
        "schemaVersion": "governance-canonical-evidence-v1",
        "artifactKind": "task_gate", "runId": "run-1",
        "writer": "task_gate_writer", "writerVersion": "1.0.0",
        "contractFingerprint": "a" * 64, "status": "passed", "reasonCode": None,
        "lifecycle": {"createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:02Z"},
        "evidenceFingerprint": "0" * 64,
        "payload": {"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": ["implementation"], "missingEvidenceKinds": []},
    }
    payload.update(overrides)
    payload["contractFingerprint"] = overrides.get("contractFingerprint", "a" * 64)
    payload["evidenceFingerprint"] = _sha256_without_evidence_fingerprint(payload)
    return payload


def _terra_payload(**overrides):
    payload = _task_gate_payload(
        artifactKind="terra_diagnosis", writer="terra_diagnosis_runner",
        status="completed", reasonCode="protected_incident",
        payload={
            "diagnosisKind": "protected_incident", "outcome": "diagnosed",
            "incidentRef": "protected-incident.json", "findingCode": "protected_incident",
        },
    )
    payload.update(overrides)
    payload["contractFingerprint"] = overrides.get("contractFingerprint", "a" * 64)
    payload["evidenceFingerprint"] = _sha256_without_evidence_fingerprint(payload)
    return payload


def _incident_payload(**overrides):
    payload = _task_gate_payload(
        artifactKind="protected_incident", writer="protected_incident_recorder",
        status="detected", reasonCode="protected_incident",
        payload={
            "incidentCode": "protected_incident", "severity": "low",
            "affectedScope": "workflow_artifact", "terraDiagnosisRequired": True,
            "terraDiagnosisRef": "terra-diagnosis.json",
        },
    )
    payload.update(overrides)
    payload["contractFingerprint"] = overrides.get("contractFingerprint", "a" * 64)
    payload["evidenceFingerprint"] = _sha256_without_evidence_fingerprint(payload)
    return payload


def test_task_gate_envelope_accepts_approved_contract_and_canonicalizes():
    envelope = CanonicalEvidenceEnvelope.from_dict(_task_gate_payload(), expected_kind="task_gate")
    assert envelope.canonical_fingerprint() == _sha256_without_evidence_fingerprint(envelope.to_dict())


def test_contract_fingerprint_is_validated_as_approval_owned_not_registry_owned():
    envelope = CanonicalEvidenceEnvelope.from_dict(
        _task_gate_payload(contractFingerprint="e" * 64), expected_kind="task_gate",
    )

    assert envelope.contract_fingerprint == "e" * 64


@pytest.mark.parametrize(("status", "reason"), [
    ("passed", None), ("failed", "gate_failed"), ("failed", "missing_evidence"),
    ("failed", "schema_violation"), ("blocked", "blocked_dependency"),
    ("blocked", "missing_evidence"),
])
def test_task_gate_accepts_each_exact_status_reason_mapping(status, reason):
    payload = _task_gate_payload(status=status, reasonCode=reason)
    payload["payload"]["decision"] = status
    payload["evidenceFingerprint"] = _sha256_without_evidence_fingerprint(payload)
    CanonicalEvidenceEnvelope.from_dict(payload, expected_kind="task_gate")


@pytest.mark.parametrize(("status", "reason"), [
    ("completed", "protected_incident"), ("completed", "diagnosis_failed"),
    ("blocked", "blocked_missing_evidence"), ("blocked", "runner_error"),
    ("not_required", None),
])
def test_terra_accepts_each_exact_status_reason_mapping(status, reason):
    CanonicalEvidenceEnvelope.from_dict(_terra_payload(status=status, reasonCode=reason), expected_kind="terra_diagnosis")


@pytest.mark.parametrize(("status", "reason"), [
    (status, reason)
    for status in ("detected", "contained", "closed")
    for reason in ("policy_violation", "data_integrity", "security_boundary", "protected_incident")
] + [("blocked", "blocked_missing_evidence"), ("blocked", "security_boundary")])
def test_protected_incident_accepts_each_exact_status_reason_mapping(status, reason):
    CanonicalEvidenceEnvelope.from_dict(_incident_payload(status=status, reasonCode=reason), expected_kind="protected_incident")


@pytest.mark.parametrize("payload", [
    _task_gate_payload(reasonCode="made_up"),
    _task_gate_payload(status="failed", reasonCode=None),
    _task_gate_payload(status="passed", reasonCode="gate_failed"),
    _task_gate_payload(payload={"taskId": "task-1", "decision": "made_up", "requiredEvidenceKinds": [], "missingEvidenceKinds": []}),
    _task_gate_payload(payload={"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": ["nope"], "missingEvidenceKinds": []}),
    _task_gate_payload(payload={"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": [], "missingEvidenceKinds": ["implementation"]}),
    _terra_payload(payload={"diagnosisKind": "nope", "outcome": "diagnosed", "incidentRef": "protected-incident.json", "findingCode": "unknown"}),
    _terra_payload(payload={"diagnosisKind": "task_gate", "outcome": "nope", "incidentRef": "../outside.json", "findingCode": "unknown"}),
    _incident_payload(payload={"incidentCode": "nope", "severity": "low", "affectedScope": "workflow_artifact", "terraDiagnosisRequired": True, "terraDiagnosisRef": "terra-diagnosis.json"}),
    _incident_payload(payload={"incidentCode": "policy_violation", "severity": "low", "affectedScope": "workflow_artifact", "terraDiagnosisRequired": 1, "terraDiagnosisRef": "terra-diagnosis.json"}),
])
def test_unknown_reason_status_or_payload_enum_is_invalid(payload):
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(payload)


@pytest.mark.parametrize("payload", [
    _task_gate_payload(lifecycle={"createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:00Z", "finalizedAt": "2026-07-28T00:00:02Z"}),
    _task_gate_payload(lifecycle={"createdAt": "2026-07-28T00:00:00+00:00", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:02Z"}),
    _task_gate_payload(contractFingerprint="A" * 64),
    _task_gate_payload(contractFingerprint="a" * 63),
    _task_gate_payload(runId="../run-1"),
    {**_task_gate_payload(), "unknown": "field"},
])
def test_lifecycle_order_and_contract_fingerprint_are_strict(payload):
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(payload, expected_kind="task_gate")


def test_incomplete_lifecycle_is_schema_valid_but_not_finalized():
    envelope = CanonicalEvidenceEnvelope.from_dict(_task_gate_payload(lifecycle={
        "createdAt": "2026-07-28T00:00:00Z",
        "startedAt": "2026-07-28T00:00:01Z",
    }))

    assert envelope.is_finalized is False


@pytest.mark.parametrize("reference", [".", ".."])
def test_reference_rejects_bare_dot_components(reference):
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(_terra_payload(payload={
            "diagnosisKind": "protected_incident",
            "outcome": "diagnosed",
            "incidentRef": reference,
            "findingCode": "protected_incident",
        }))


@pytest.mark.parametrize("payload", [
    _task_gate_payload(payload={"taskId": "x" * 129, "decision": "passed", "requiredEvidenceKinds": [], "missingEvidenceKinds": []}),
    _task_gate_payload(payload={"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": ["implementation"] * 17, "missingEvidenceKinds": []}),
    _task_gate_payload(payload={"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": ["x" * 65], "missingEvidenceKinds": []}),
    _terra_payload(payload={"diagnosisKind": "task_gate", "outcome": "diagnosed", "incidentRef": "x" * 129, "findingCode": "unknown"}),
    _incident_payload(payload={"incidentCode": "policy_violation", "severity": "low", "affectedScope": "workflow_artifact", "terraDiagnosisRequired": False, "terraDiagnosisRef": -1}),
])
def test_payload_caps_and_wrong_scalar_types_are_rejected(payload):
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(payload)


def test_registry_is_fixed_to_three_immutable_schema_entries():
    registry = CanonicalEvidenceRegistry()
    entries = registry.entries()
    assert [entry.artifact_kind for entry in entries] == ["task_gate", "terra_diagnosis", "protected_incident"]
    assert [(entry.filename, entry.writer, entry.entrypoint) for entry in entries] == [
        ("task-gate.json", "task_gate_writer", "backend.agents.canonical_evidence_writer:write_task_gate"),
        ("terra-diagnosis.json", "terra_diagnosis_runner", "backend.agents.canonical_evidence_writer:write_terra_diagnosis"),
        ("protected-incident.json", "protected_incident_recorder", "backend.agents.canonical_evidence_writer:write_protected_incident"),
    ]
    assert all(entry.schema_version == "governance-canonical-evidence-v1" for entry in entries)
    assert all(not hasattr(entry, "contract_fingerprint") for entry in entries)
    with pytest.raises((AttributeError, TypeError)):
        entries.append("new writer")


def test_writer_version_must_be_in_the_registry_range():
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(_task_gate_payload(writerVersion="9.9.9"))


def test_evidence_fingerprint_and_contract_format_fail_closed_when_tampered():
    evidence_tampered = _task_gate_payload()
    evidence_tampered["status"] = "failed"
    evidence_tampered["reasonCode"] = "gate_failed"
    evidence_tampered["payload"]["decision"] = "failed"
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(evidence_tampered)

    contract_tampered = _task_gate_payload(contractFingerprint="A" * 64)
    with pytest.raises(CanonicalEvidenceSchemaError):
        CanonicalEvidenceEnvelope.from_dict(contract_tampered)


def test_envelope_internal_lifecycle_and_payload_are_deeply_immutable():
    envelope = CanonicalEvidenceEnvelope.from_dict(_task_gate_payload())
    with pytest.raises(TypeError):
        envelope.lifecycle["createdAt"] = "2026-07-29T00:00:00Z"
    with pytest.raises(TypeError):
        envelope.payload["taskId"] = "other-task"
    assert isinstance(envelope.payload["requiredEvidenceKinds"], tuple)
