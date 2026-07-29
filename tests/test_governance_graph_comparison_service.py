from __future__ import annotations

from pathlib import Path

from backend.agents.governance_graph_comparison_service import GovernanceGraphComparisonService
from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, WorkflowManifest, WorkflowStatus
from backend.agents.workflow_store import WorkflowStore


def _write_run(tmp_path: Path, run_id: str) -> Path:
    store = WorkflowStore(tmp_path)
    store.create_run(
        WorkflowManifest(MANIFEST_SCHEMA, run_id, "docs/brief.md", "a" * 64, "main", "b" * 40, (), "2026-07-28T00:00:00+00:00", "c" * 64),
        WorkflowStatus(STATUS_SCHEMA, run_id, "created", "created", "2026-07-28T00:00:00+00:00", "2026-07-28T00:00:00+00:00", None, "fixture", None, 0),
    )
    return store.runs_root / run_id


def _write_valid_snapshot(tmp_path: Path, run_id: str) -> Path:
    run_dir = _write_run(tmp_path, run_id)
    GovernanceGraphBuilder(tmp_path).persist(run_id)
    return run_dir


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_same_snapshot_is_zero_diff_and_deterministic(tmp_path):
    _write_valid_snapshot(tmp_path, "run-1")
    service = GovernanceGraphComparisonService(tmp_path)

    result = service.compare(left_run_id="run-1", right_run_id="run-1")
    repeated = service.compare(left_run_id="run-1", right_run_id="run-1")

    assert result.status == "available"
    assert result.summary["unchangedNodes"] >= 1
    assert result.node_changes == ()
    assert result.comparison_fingerprint == repeated.comparison_fingerprint


def test_missing_side_is_unavailable_without_fallback_or_write(tmp_path):
    _write_valid_snapshot(tmp_path, "run-left")
    before = _tree_bytes(tmp_path)

    result = GovernanceGraphComparisonService(tmp_path).compare(
        left_run_id="run-left", right_run_id="run-missing"
    )

    assert result.status == "unavailable"
    assert result.node_changes == ()
    assert _tree_bytes(tmp_path) == before


def test_edges_are_not_inferred_when_snapshot_has_no_edges(tmp_path):
    _write_valid_snapshot(tmp_path, "run-left")
    _write_valid_snapshot(tmp_path, "run-right")

    result = GovernanceGraphComparisonService(tmp_path).compare(
        left_run_id="run-left", right_run_id="run-right"
    )

    assert result.edge_changes == ()
    assert result.summary["addedEdges"] == result.summary["removedEdges"] == 0


def test_reversed_sides_change_fingerprint(tmp_path):
    _write_valid_snapshot(tmp_path, "run-left")
    _write_valid_snapshot(tmp_path, "run-right")
    service = GovernanceGraphComparisonService(tmp_path)

    forward = service.compare(left_run_id="run-left", right_run_id="run-right")
    reverse = service.compare(left_run_id="run-right", right_run_id="run-left")

    assert forward.comparison_fingerprint != reverse.comparison_fingerprint
