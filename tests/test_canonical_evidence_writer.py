from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents.canonical_evidence_models import CanonicalEvidenceEnvelope
from backend.agents.canonical_evidence_registry import CanonicalEvidenceRegistry
from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
)
from backend.agents.workflow_store import WorkflowStore


def _envelope(kind: str, run_id: str = "run-1") -> CanonicalEvidenceEnvelope:
    entry = CanonicalEvidenceRegistry().for_kind(kind)
    payloads = {
        "task_gate": {
            "status": "passed", "reasonCode": None,
            "payload": {"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": ["implementation"], "missingEvidenceKinds": []},
        },
        "terra_diagnosis": {
            "status": "completed", "reasonCode": "protected_incident",
            "payload": {"diagnosisKind": "protected_incident", "outcome": "diagnosed", "incidentRef": "protected-incident.json", "findingCode": "protected_incident"},
        },
        "protected_incident": {
            "status": "detected", "reasonCode": "protected_incident",
            "payload": {"incidentCode": "protected_incident", "severity": "low", "affectedScope": "workflow_artifact", "terraDiagnosisRequired": True, "terraDiagnosisRef": "terra-diagnosis.json"},
        },
    }
    unsigned = {
        "schemaVersion": entry.schema_version, "artifactKind": kind, "runId": run_id,
        "writer": entry.writer, "writerVersion": "1.0.0",
        "contractFingerprint": entry.contract_fingerprint,
        "lifecycle": {"createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:02Z"},
        **payloads[kind],
    }
    from backend.agents.workflow_models import canonical_sha256

    return CanonicalEvidenceEnvelope.from_dict({
        **unsigned, "evidenceFingerprint": canonical_sha256(unsigned),
    })


def _write_approved_run(root: Path, run_id: str = "run-1", *, fingerprint: str | None = None) -> WorkflowStore:
    store = WorkflowStore(root)
    store.create_run(
        WorkflowManifest(MANIFEST_SCHEMA, run_id, "brief.md", "a" * 64, "codex/test", "b" * 40, (), "2026-07-28T00:00:00+00:00", "c" * 64),
        WorkflowStatus(STATUS_SCHEMA, run_id, "authorization", "created", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00", None, "created", None, 0),
    )
    store.write_approval(run_id, WorkflowApproval(
        APPROVAL_SCHEMA, run_id, ".nbs_agent_runtime/contracts/task-1.json",
        fingerprint or _envelope("task_gate", run_id).contract_fingerprint,
        "d" * 40, "2026-07-28T00:01:00+00:00", "approved",
    ))
    return store


@pytest.mark.parametrize("kind", ["task_gate", "terra_diagnosis", "protected_incident"])
def test_writer_creates_registered_final_artifact_for_each_approved_kind(tmp_path, kind):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriter

    envelope = _envelope(kind)
    _write_approved_run(tmp_path, fingerprint=envelope.contract_fingerprint)

    path = CanonicalEvidenceWriter(tmp_path).write_final("run-1", envelope)

    assert path == tmp_path / ".nbs_agent_runtime" / "runs" / "run-1" / CanonicalEvidenceRegistry().for_kind(kind).filename
    assert CanonicalEvidenceEnvelope.from_dict(__import__("json").loads(path.read_text(encoding="utf-8"))) == envelope


def test_writer_creates_one_final_artifact_and_rejects_duplicate(tmp_path):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriteError, CanonicalEvidenceWriter

    envelope = _envelope("task_gate")
    _write_approved_run(tmp_path, fingerprint=envelope.contract_fingerprint)
    writer = CanonicalEvidenceWriter(tmp_path)
    first = writer.write_final("run-1", envelope)
    before = first.read_bytes()

    with pytest.raises(CanonicalEvidenceWriteError):
        writer.write_final("run-1", envelope)

    assert first.read_bytes() == before


@pytest.mark.parametrize("setup", ["missing", "unapproved", "mismatched"])
def test_writer_rejects_missing_unapproved_or_mismatched_approval_without_writing(tmp_path, setup):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriteError, CanonicalEvidenceWriter

    envelope = _envelope("task_gate")
    store = WorkflowStore(tmp_path)
    if setup == "missing":
        store.create_run(
            WorkflowManifest(MANIFEST_SCHEMA, "run-1", "brief.md", "a" * 64, "codex/test", "b" * 40, (), "2026-07-28T00:00:00+00:00", "c" * 64),
            WorkflowStatus(STATUS_SCHEMA, "run-1", "authorization", "created", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00", None, "created", None, 0),
        )
    else:
        store = _write_approved_run(tmp_path, fingerprint=("0" * 64 if setup == "mismatched" else envelope.contract_fingerprint))
        if setup == "unapproved":
            approval = store.runs_root / "run-1" / "approval.json"
            payload = __import__("json").loads(approval.read_text(encoding="utf-8"))
            payload["authorizationStatus"] = "rejected"
            approval.write_text(__import__("json").dumps(payload), encoding="utf-8")

    with pytest.raises(CanonicalEvidenceWriteError):
        CanonicalEvidenceWriter(tmp_path).write_final("run-1", envelope)
    assert not (store.runs_root / "run-1" / "task-gate.json").exists()


def test_writer_rejects_run_and_registry_contract_mismatch_without_writing(tmp_path):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriteError, CanonicalEvidenceWriter

    envelope = _envelope("task_gate")
    _write_approved_run(tmp_path, fingerprint=envelope.contract_fingerprint)
    writer = CanonicalEvidenceWriter(tmp_path)

    for invalid in (replace(envelope, run_id="run-2"), replace(envelope, contract_fingerprint="0" * 64)):
        with pytest.raises(CanonicalEvidenceWriteError):
            writer.write_final("run-1", invalid)
    assert not (tmp_path / ".nbs_agent_runtime" / "runs" / "run-1" / "task-gate.json").exists()


def test_writer_entrypoints_reject_writer_kind_mismatch(tmp_path):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriteError, write_task_gate

    envelope = _envelope("terra_diagnosis")
    _write_approved_run(tmp_path, fingerprint=envelope.contract_fingerprint)

    with pytest.raises(CanonicalEvidenceWriteError):
        write_task_gate(tmp_path, "run-1", envelope)


@pytest.mark.parametrize("target_kind", ["symlink", "directory"])
def test_writer_rejects_non_regular_or_symlink_registered_target(tmp_path, target_kind):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriteError, CanonicalEvidenceWriter

    envelope = _envelope("task_gate")
    store = _write_approved_run(tmp_path, fingerprint=envelope.contract_fingerprint)
    target = store.runs_root / "run-1" / "task-gate.json"
    if target_kind == "symlink":
        target.symlink_to(store.runs_root / "run-1" / "outside.json")
    else:
        target.mkdir()

    with pytest.raises(CanonicalEvidenceWriteError):
        CanonicalEvidenceWriter(tmp_path).write_final("run-1", envelope)


def test_writer_rejects_traversal_run_id_without_writing(tmp_path):
    from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriteError, CanonicalEvidenceWriter

    envelope = _envelope("task_gate")
    _write_approved_run(tmp_path, fingerprint=envelope.contract_fingerprint)

    with pytest.raises(CanonicalEvidenceWriteError):
        CanonicalEvidenceWriter(tmp_path).write_final("../run-1", envelope)
    assert not (tmp_path / ".nbs_agent_runtime" / "runs" / "task-gate.json").exists()
