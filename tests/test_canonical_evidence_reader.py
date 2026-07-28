from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.canonical_evidence_models import CanonicalEvidenceEnvelope
from backend.agents.canonical_evidence_registry import CanonicalEvidenceRegistry
from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriter
from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
    canonical_sha256,
)
from backend.agents.workflow_store import WorkflowStore


def _envelope(kind: str, run_id: str = "run-1", *, status: str | None = None) -> CanonicalEvidenceEnvelope:
    entry = CanonicalEvidenceRegistry().for_kind(kind)
    payloads = {
        "task_gate": (status or "failed", "missing_evidence" if status == "blocked" else "gate_failed", {"taskId": "task-1", "decision": status or "failed", "requiredEvidenceKinds": ["implementation"], "missingEvidenceKinds": []}),
        "terra_diagnosis": (status or "completed", "protected_incident", {"diagnosisKind": "protected_incident", "outcome": "diagnosed", "incidentRef": "protected-incident.json", "findingCode": "protected_incident"}),
        "protected_incident": (status or "detected", "protected_incident", {"incidentCode": "protected_incident", "severity": "low", "affectedScope": "workflow_artifact", "terraDiagnosisRequired": True}),
    }
    evidence_status, reason, payload = payloads[kind]
    if evidence_status == "passed" or evidence_status == "not_required":
        reason = None
    unsigned = {
        "schemaVersion": entry.schema_version, "artifactKind": kind, "runId": run_id,
        "writer": entry.writer, "writerVersion": "1.0.0", "contractFingerprint": "a" * 64,
        "status": evidence_status, "reasonCode": reason,
        "lifecycle": {"createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:03Z"},
        "payload": payload,
    }
    return CanonicalEvidenceEnvelope.from_dict({**unsigned, "evidenceFingerprint": canonical_sha256(unsigned)})


def _approved_run(root: Path, run_id: str = "run-1") -> Path:
    store = WorkflowStore(root)
    store.create_run(
        WorkflowManifest(MANIFEST_SCHEMA, run_id, "brief.md", "a" * 64, "codex/test", "b" * 40, (), "2026-07-28T00:00:00+00:00", "c" * 64),
        WorkflowStatus(STATUS_SCHEMA, run_id, "hermes", "completed", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00", "completed", None, 0),
    )
    store.write_approval(run_id, WorkflowApproval(APPROVAL_SCHEMA, run_id, "task-1.json", "a" * 64, "d" * 40, "2026-07-28T00:01:00+00:00", "approved"))
    return store.runs_root / run_id


def _write(root: Path, kind: str, *, status: str | None = None) -> CanonicalEvidenceEnvelope:
    envelope = _envelope(kind, status=status)
    run = _approved_run(root)
    approval = json.loads((run / "approval.json").read_text(encoding="utf-8"))
    approval["contractFingerprint"] = envelope.contract_fingerprint
    (run / "approval.json").write_text(json.dumps(approval), encoding="utf-8")
    CanonicalEvidenceWriter(root).write_final("run-1", envelope)
    return envelope


def test_reader_compacts_valid_evidence_without_payload_or_path_leaks(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    envelope = _write(tmp_path, "task_gate")
    result = CanonicalEvidenceReader(tmp_path).read(tmp_path / ".nbs_agent_runtime" / "runs" / "run-1")

    evidence = result["task_gate"]
    assert evidence == {
        "state": "failed", "status": "available", "reason": "gate_failed",
        "finalizedAt": "2026-07-28T00:00:03Z", "artifact": "task-gate.json",
        "sha256": envelope.evidence_fingerprint,
    }
    serialized = json.dumps(result)
    assert "taskId" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize("mode", ["malformed", "unknown_key", "symlink", "oversize", "fingerprint_mismatch"])
def test_reader_rejects_unsafe_or_invalid_artifact_with_invalid_precedence(tmp_path, mode):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    target = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1" / "task-gate.json"
    if mode == "malformed":
        target.write_text("{invalid", encoding="utf-8")
    elif mode == "unknown_key":
        payload = json.loads(target.read_text(encoding="utf-8")); payload["unsafe"] = "value"; target.write_text(json.dumps(payload), encoding="utf-8")
    elif mode == "symlink":
        outside = tmp_path / "outside.json"; outside.write_text(target.read_text(encoding="utf-8"), encoding="utf-8"); target.unlink(); target.symlink_to(outside)
    elif mode == "oversize":
        target.write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
    else:
        payload = json.loads(target.read_text(encoding="utf-8")); payload["evidenceFingerprint"] = "0" * 64; target.write_text(json.dumps(payload), encoding="utf-8")

    evidence = CanonicalEvidenceReader(tmp_path).read(target.parent)["task_gate"]

    assert evidence["status"] == "invalid"
    assert evidence["state"] == "invalid"
    assert evidence["artifact"] == "task-gate.json"
    assert set(evidence) == {"state", "status", "reason", "finalizedAt", "artifact", "sha256"}


def test_reader_marks_missing_as_unknown_and_valid_blocked_as_blocked(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    run = _approved_run(tmp_path)
    result = CanonicalEvidenceReader(tmp_path).read(run)
    assert result["task_gate"]["status"] == "unknown"

    envelope = _envelope("task_gate", status="blocked")
    approval = json.loads((run / "approval.json").read_text(encoding="utf-8")); approval["contractFingerprint"] = envelope.contract_fingerprint; (run / "approval.json").write_text(json.dumps(approval), encoding="utf-8")
    CanonicalEvidenceWriter(tmp_path).write_final("run-1", envelope)
    assert CanonicalEvidenceReader(tmp_path).read(run)["task_gate"]["status"] == "blocked"


def test_reader_rejects_artifact_when_approval_contract_does_not_bind_it(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    approval_path = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1" / "approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8")); approval["contractFingerprint"] = "0" * 64; approval_path.write_text(json.dumps(approval), encoding="utf-8")

    assert CanonicalEvidenceReader(tmp_path).read(approval_path.parent)["task_gate"]["status"] == "invalid"


def test_reader_accepts_matching_approval_owned_contract(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1"
    approval_path = run / "approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["contractFingerprint"] = "e" * 64
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    target = run / "task-gate.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["contractFingerprint"] = "e" * 64
    unsigned = dict(payload)
    unsigned.pop("evidenceFingerprint")
    payload["evidenceFingerprint"] = canonical_sha256(unsigned)
    target.write_text(json.dumps(payload), encoding="utf-8")

    evidence = CanonicalEvidenceReader(tmp_path).read(run)["task_gate"]

    assert evidence["status"] == "available"


def test_reader_invalid_approval_precedes_missing_artifact_unknown(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    run = _approved_run(tmp_path)
    approval_path = run / "approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["authorizationStatus"] = "rejected"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")

    evidence = CanonicalEvidenceReader(tmp_path).read(run)["task_gate"]

    assert evidence["status"] == "invalid"
    assert evidence["state"] == "invalid"


def test_reader_rejects_duplicate_json_keys_fail_closed(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    target = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1" / "task-gate.json"
    target.write_text('{"schemaVersion":"first","schemaVersion":"second"}', encoding="utf-8")

    assert CanonicalEvidenceReader(tmp_path).read(target.parent)["task_gate"]["status"] == "invalid"


def test_reader_rejects_traversal_run_path_fail_closed(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    run = _approved_run(tmp_path)
    evidence = CanonicalEvidenceReader(tmp_path).read(run / ".." / run.name)["task_gate"]

    assert evidence["status"] == "invalid"


def test_reader_rejects_symlinked_runs_root_before_reading_evidence(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1"
    runs_root = run.parent
    target = tmp_path / "runs-target"
    runs_root.rename(target)
    runs_root.symlink_to(target, target_is_directory=True)

    evidence = CanonicalEvidenceReader(tmp_path).read(runs_root / run.name)["task_gate"]

    assert evidence["status"] == "invalid"


def test_reader_rejects_non_directory_runs_root_before_reading_evidence(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    runs_root = tmp_path / ".nbs_agent_runtime" / "runs"
    runs_root.parent.mkdir()
    runs_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError):
        CanonicalEvidenceReader(tmp_path)._safe_runs_root()


def test_reader_rejects_manifest_run_id_mismatch_before_evidence_projection(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1"
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runId"] = "other-run"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    evidence = CanonicalEvidenceReader(tmp_path).read(run)["task_gate"]

    assert evidence["status"] == "invalid"


def test_reader_marks_incomplete_lifecycle_unknown_before_blocked_classification(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1"
    target = run / "task-gate.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    payload["reasonCode"] = "missing_evidence"
    payload["payload"]["decision"] = "blocked"
    payload["lifecycle"].pop("finalizedAt")
    unsigned = dict(payload)
    unsigned.pop("evidenceFingerprint")
    payload["evidenceFingerprint"] = canonical_sha256(unsigned)
    target.write_text(json.dumps(payload), encoding="utf-8")

    evidence = CanonicalEvidenceReader(tmp_path).read(run)["task_gate"]

    assert evidence["status"] == "unknown"
    assert evidence["state"] == "unknown"


def test_reader_marks_incomplete_lifecycle_invalid_when_fingerprint_is_tampered(tmp_path):
    from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader

    _write(tmp_path, "task_gate")
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1"
    target = run / "task-gate.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["lifecycle"].pop("finalizedAt")
    payload["evidenceFingerprint"] = "0" * 64
    target.write_text(json.dumps(payload), encoding="utf-8")

    evidence = CanonicalEvidenceReader(tmp_path).read(run)["task_gate"]

    assert evidence["status"] == "invalid"
    assert evidence["state"] == "invalid"
