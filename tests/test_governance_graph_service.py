from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.agents.canonical_evidence_models import CanonicalEvidenceEnvelope
from backend.agents.canonical_evidence_registry import CanonicalEvidenceRegistry
from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriter
from backend.agents.governance_graph_service import GovernanceGraphBuilder
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


def _manifest(run_id: str = "graph-run") -> WorkflowManifest:
    return WorkflowManifest(
        MANIFEST_SCHEMA, run_id, "docs/brief.md", "a" * 64, "main", "b" * 40,
        (), "2026-07-27T10:00:00+00:00", "c" * 64,
    )


def _status(run_id: str = "graph-run", status: str = "completed") -> WorkflowStatus:
    return WorkflowStatus(
        STATUS_SCHEMA, run_id, "hermes", status,
        "2026-07-27T10:00:00+00:00", "2026-07-27T10:01:00+00:00",
        "2026-07-27T10:01:00+00:00" if status == "completed" else None,
        "fixture", None, 0,
    )


def _node(snapshot, node_id: str):
    return next(node for node in snapshot.nodes if node.node_id == node_id)


def _canonical_bytes(root: Path, run_id: str) -> bytes:
    run_dir = root / ".nbs_agent_runtime" / "runs" / run_id
    payload = {}
    for path in sorted(run_dir.iterdir()):
        if path.name in {"governance-graph.json", ".lock"} or not path.is_file():
            continue
        payload[path.name] = path.read_bytes().hex()
    return json.dumps(payload, sort_keys=True).encode()


def _write_verified_artifacts(store: WorkflowStore, run_id: str, *, git_head: str = "b" * 40) -> None:
    store.write_artifact(run_id, "risk-classification.json", {
        "schemaVersion": "nbs-governance-risk-v1", "level": "R1", "surfaces": [],
        "evidenceRefs": [],
    })
    store.write_artifact(run_id, "design-spec-gate.json", {
        "schemaVersion": "nbs-governance-gate-v1", "gateId": "spec_gate", "status": "passed",
        "fingerprint": "1" * 64, "evidenceRefs": [], "reasonCode": None,
    })
    store.write_artifact(run_id, "plan-gate.json", {
        "schemaVersion": "nbs-governance-gate-v1", "gateId": "plan_gate", "status": "passed",
        "fingerprint": "2" * 64, "evidenceRefs": [], "reasonCode": None,
    })
    store.write_artifact(run_id, "implementation.json", {"status": "completed"})
    store.write_artifact(run_id, "targeted-verification.json", {"commands": [{"exitCode": 0}]})
    store.write_artifact(run_id, "review.json", {"verdict": "pass", "gitHead": git_head})
    store.write_artifact(run_id, "full-verification.json", {
        "fullPytest": {"exitCode": 0}, "acceptance": {"status": "passed"},
    })
    store.write_artifact(run_id, "hermes.json", {"overallStatus": "pass", "gitHead": git_head})
    store.write_artifact(run_id, "documentation-application.json", {"status": "applied"})
    store.write_artifact(run_id, "git-integration.json", {"status": "committed", "gitHead": git_head})


def _canonical_evidence(kind: str, run_id: str, *, status: str | None = None) -> CanonicalEvidenceEnvelope:
    entry = CanonicalEvidenceRegistry().for_kind(kind)
    values = {
        "task_gate": (status or "failed", "gate_failed", {
            "taskId": "task-1", "decision": status or "failed",
            "requiredEvidenceKinds": [], "missingEvidenceKinds": [],
        }),
        "terra_diagnosis": (status or "completed", "protected_incident", {
            "diagnosisKind": "protected_incident", "outcome": "diagnosed",
            "incidentRef": "protected-incident.json", "findingCode": "protected_incident",
        }),
        "protected_incident": (status or "detected", "protected_incident", {
            "incidentCode": "protected_incident", "severity": "low",
            "affectedScope": "workflow_artifact", "terraDiagnosisRequired": True,
        }),
    }
    evidence_status, reason, payload = values[kind]
    if evidence_status == "blocked":
        reason = "missing_evidence" if kind == "task_gate" else "blocked_missing_evidence"
    unsigned = {
        "schemaVersion": entry.schema_version, "artifactKind": kind, "runId": run_id,
        "writer": entry.writer, "writerVersion": "1.0.0", "contractFingerprint": entry.contract_fingerprint,
        "status": evidence_status, "reasonCode": reason,
        "lifecycle": {
            "createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z",
            "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:03Z",
        },
        "payload": payload,
    }
    return CanonicalEvidenceEnvelope.from_dict({**unsigned, "evidenceFingerprint": canonical_sha256(unsigned)})


def _write_canonical_evidence(root: Path, store: WorkflowStore, run_id: str, kind: str, *, status: str | None = None) -> CanonicalEvidenceEnvelope:
    envelope = _canonical_evidence(kind, run_id, status=status)
    store.write_approval(run_id, WorkflowApproval(
        APPROVAL_SCHEMA, run_id, "task-1.json", envelope.contract_fingerprint,
        "d" * 40, "2026-07-28T00:01:00+00:00", "approved",
    ))
    CanonicalEvidenceWriter(root).write_final(run_id, envelope)
    return envelope


def test_existing_run_without_risk_is_not_auto_classified(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())

    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)

    assert snapshot.risk is None
    assert _node(snapshot, "risk").status == "not_started"
    assert "implementation" not in snapshot.allowed_next_nodes


def test_persist_writes_only_projection_and_validate_is_zero_write(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    _write_verified_artifacts(store, run_id)
    builder = GovernanceGraphBuilder(tmp_path)
    before = _canonical_bytes(tmp_path, run_id)

    persisted = builder.persist(run_id)
    projection = tmp_path / ".nbs_agent_runtime" / "runs" / run_id / "governance-graph.json"
    projection_before = projection.read_bytes()

    assert projection.is_file()
    assert _canonical_bytes(tmp_path, run_id) == before
    assert builder.validate(run_id).graph_fingerprint == persisted.graph_fingerprint
    assert projection.read_bytes() == projection_before
    assert persisted.overall_status == "completed"


def test_malformed_canonical_artifact_blocks_only_the_affected_graph(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    store.write_artifact(run_id, "risk-classification.json", {"schemaVersion": "wrong"})

    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)

    assert _node(snapshot, "risk").status == "blocked"
    assert _node(snapshot, "risk").reason_code == "malformed_artifact"
    assert snapshot.overall_status == "blocked"


def test_dangling_canonical_symlink_is_blocked_not_missing(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    risk_path = store._run_file(run_id, "risk-classification.json")
    risk_path.symlink_to(tmp_path / "missing-risk.json")

    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)

    assert _node(snapshot, "risk").status == "blocked"
    assert _node(snapshot, "risk").reason_code == "malformed_artifact"


def test_changed_git_identity_invalidates_review_and_hermes(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    _write_verified_artifacts(store, run_id)
    manifest_path = tmp_path / ".nbs_agent_runtime" / "runs" / run_id / "manifest.json"
    changed = {**store.load_manifest(run_id).to_dict(), "gitHead": "d" * 40}
    store._atomic_json(manifest_path, changed)

    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)

    assert _node(snapshot, "review").status != "passed"
    assert _node(snapshot, "hermes").status != "passed"


def test_deterministic_no_doc_and_only_valid_git_outcomes_can_complete(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    _write_verified_artifacts(store, run_id)
    store.write_artifact(run_id, "documentation-application.json", {
        "status": "skipped", "reasonCode": "no_documentation_needed",
    })

    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)
    assert _node(snapshot, "documentation").status == "passed"
    assert _node(snapshot, "documentation").reason_code == "deterministic_no_doc"
    assert snapshot.overall_status == "completed"

    store._atomic_json(
        store._run_file(run_id, "git-integration.json"), {"status": "applied"}
    )
    assert GovernanceGraphBuilder(tmp_path).build(run_id).overall_status == "blocked"


@pytest.mark.parametrize("kind", ("task_gate", "terra_diagnosis", "protected_incident"))
def test_graph_projects_validated_canonical_evidence_as_bounded_lineage_without_inference(tmp_path, kind):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    expected = _write_canonical_evidence(tmp_path, store, run_id, kind)
    before = _canonical_bytes(tmp_path, run_id)

    snapshot = GovernanceGraphBuilder(tmp_path).persist(run_id)

    assert snapshot.allowed_next_nodes == ("risk",)
    assert _node(snapshot, "risk").status == "not_started"
    node = _node(snapshot, kind)
    assert node.node_type == kind
    assert node.status == "available"
    assert node.reason_code == expected.reason_code
    assert node.fingerprint == expected.evidence_fingerprint
    assert [ref.to_dict() for ref in node.evidence_refs] == [{
        "schemaVersion": "nbs-governance-evidence-ref-v1",
            "path": CanonicalEvidenceRegistry().for_kind(kind).filename,
            "sha256": expected.evidence_fingerprint,
            "status": "available",
            "generatedAt": "2026-07-28T00:00:03+00:00",
    }]
    assert _canonical_bytes(tmp_path, run_id) == before
    serialized = json.dumps(snapshot.to_dict())
    assert "task-1" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize(
    ("kind", "mode", "expected_status"),
    [
        ("task_gate", "missing", "unknown"),
        ("terra_diagnosis", "invalid", "invalid"),
        ("protected_incident", "blocked", "blocked"),
    ],
)
def test_graph_preserves_missing_invalid_and_blocked_evidence_without_cross_kind_inference(tmp_path, kind, mode, expected_status):
    store = WorkflowStore(tmp_path)
    run_id = _manifest().run_id
    store.create_run(_manifest(), _status())
    if mode == "missing":
        entry = CanonicalEvidenceRegistry().for_kind(kind)
        store.write_approval(run_id, WorkflowApproval(
            APPROVAL_SCHEMA, run_id, "task-1.json", entry.contract_fingerprint,
            "d" * 40, "2026-07-28T00:01:00+00:00", "approved",
        ))
    elif mode == "invalid":
        envelope = _write_canonical_evidence(tmp_path, store, run_id, kind)
        target = store.runs_root / run_id / CanonicalEvidenceRegistry().for_kind(kind).filename
        target.write_text(json.dumps({**envelope.to_dict(), "unsafe": "field"}), encoding="utf-8")
    elif mode == "blocked":
        _write_canonical_evidence(tmp_path, store, run_id, kind, status="blocked")

    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)

    node = _node(snapshot, kind)
    assert node.status == expected_status
    assert snapshot.allowed_next_nodes == ("risk",)
    assert _node(snapshot, "risk").status == "not_started"
    if expected_status in {"unknown", "invalid"}:
        assert node.evidence_refs == ()
        assert node.reason_code in {"missing", "invalid_evidence"}
