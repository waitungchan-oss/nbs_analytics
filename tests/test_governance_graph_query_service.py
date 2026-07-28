from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.governance_graph_query_service import GovernanceGraphQueryService
from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, WorkflowManifest, WorkflowStatus
from backend.agents.workflow_store import WorkflowStore


def _write_run(tmp_path: Path, run_id: str = "run-1") -> Path:
    store = WorkflowStore(tmp_path)
    store.create_run(
        WorkflowManifest(MANIFEST_SCHEMA, run_id, "docs/brief.md", "a" * 64, "main", "b" * 40, (), "2026-07-28T00:00:00+00:00", "c" * 64),
        WorkflowStatus(STATUS_SCHEMA, run_id, "created", "created", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00", None, "fixture", None, 0),
    )
    return store.runs_root / run_id


def _write_valid_snapshot(tmp_path: Path, run_id: str = "run-1") -> Path:
    run_dir = _write_run(tmp_path, run_id)
    GovernanceGraphBuilder(tmp_path).persist(run_id)
    return run_dir


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_query_returns_deterministically_filtered_nodes_and_refs(tmp_path):
    _write_valid_snapshot(tmp_path)

    result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1", node_type="task_gate")

    assert result.status == "invalid"
    assert [node["nodeId"] for node in result.matched_nodes] == ["task_gate"]
    assert result.query_fingerprint == GovernanceGraphQueryService(tmp_path).query(
        run_id="run-1", node_type="task_gate"
    ).query_fingerprint


def test_missing_snapshot_is_unavailable_without_fallback_or_write(tmp_path):
    _write_run(tmp_path)
    before = _tree_bytes(tmp_path)

    result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")

    assert result.status == "unavailable"
    assert _tree_bytes(tmp_path) == before


def test_missing_run_is_unavailable_without_fallback(tmp_path):
    _write_run(tmp_path, "other-run")

    result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")

    assert result.status == "unavailable"


def test_artifact_kind_uses_canonical_path_mapping_and_never_infers_missing_refs(tmp_path):
    assert GovernanceGraphQueryService._ref_matches(
        {"path": "design-spec-gate.json", "status": "available"},
        {"artifactKind": "spec_gate", "evidenceStatus": None},
    )
    assert not GovernanceGraphQueryService._ref_matches(
        {"path": "design-spec-gate.json", "status": "available"},
        {"artifactKind": "design_spec_gate", "evidenceStatus": None},
    )
    assert not GovernanceGraphQueryService._node_matches(
        {"nodeType": "task_gate", "status": "blocked", "evidenceRefs": []},
        {"artifactKind": "task_gate", "evidenceStatus": None},
    )


def test_invalid_snapshot_is_invalid_and_does_not_fallback(tmp_path):
    run_dir = _write_valid_snapshot(tmp_path)
    snapshot_path = run_dir / "governance-graph.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["graphFingerprint"] = "0" * 64
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")

    assert result.status == "invalid"
    assert result.diagnostics[0]["code"] == "invalid_snapshot"


def test_exact_artifact_and_evidence_filters_return_bounded_results(tmp_path):
    _write_valid_snapshot(tmp_path)

    result = GovernanceGraphQueryService(tmp_path).query(
        run_id="run-1", artifact_kind="task_gate", evidence_status="invalid"
    )

    assert result.status == "available"
    assert result.matched_nodes == ()
    assert result.evidence_refs == ()


def test_snapshot_path_symlink_is_rejected(tmp_path):
    run_dir = _write_run(tmp_path)
    external = tmp_path / "external.json"
    external.write_text("{}", encoding="utf-8")
    (run_dir / "governance-graph.json").symlink_to(external)

    result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")

    assert result.status == "invalid"


@pytest.mark.parametrize("bad_run_id", ["../escape", ".", ".."])
def test_query_rejects_unsafe_run_id(tmp_path, bad_run_id):
    _write_run(tmp_path, "safe-run")

    result = GovernanceGraphQueryService(tmp_path).query(run_id=bad_run_id)

    assert result.status == "invalid"
    assert result.diagnostics[0]["code"] == "invalid_query"


def test_dangling_run_directory_symlink_is_invalid(tmp_path):
    (tmp_path / ".nbs_agent_runtime" / "runs").mkdir(parents=True)
    runs_root = tmp_path / ".nbs_agent_runtime" / "runs"
    (runs_root / "run-1").symlink_to(runs_root / "missing-run")

    result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")

    assert result.status == "invalid"
    assert result.diagnostics[0]["code"] == "invalid_run_id"
