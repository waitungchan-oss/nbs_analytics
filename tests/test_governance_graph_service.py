from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, WorkflowManifest, WorkflowStatus
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
