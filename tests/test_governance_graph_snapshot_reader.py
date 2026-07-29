from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.governance_graph_snapshot_reader import GovernanceGraphSnapshotReader
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


def test_reader_missing_snapshot_is_unavailable_without_write(tmp_path):
    _write_run(tmp_path)
    before = _tree_bytes(tmp_path)

    result = GovernanceGraphSnapshotReader(tmp_path).read("run-1")

    assert result.status == "unavailable"
    assert result.snapshot is None
    assert _tree_bytes(tmp_path) == before


def test_reader_returns_verified_snapshot_identity(tmp_path):
    _write_valid_snapshot(tmp_path)

    result = GovernanceGraphSnapshotReader(tmp_path).read("run-1")

    assert result.status == "available"
    assert result.snapshot is not None
    assert result.snapshot_identity == {
        "runId": "run-1",
        "graphFingerprint": result.snapshot.graph_fingerprint,
        "generatedAt": result.snapshot.generated_at,
        "freshness": result.snapshot.freshness["status"],
    }
    assert result.diagnostics == ()


def _make_symlink(path: Path) -> None:
    path.unlink()
    path.symlink_to(path.parent / "external.json")


def _make_duplicate_json(path: Path) -> None:
    path.write_text('{"schemaVersion": "nbs-governance-graph-v1", "schemaVersion": "duplicate"}', encoding="utf-8")


def _make_bad_fingerprint(path: Path) -> None:
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "graphFingerprint": "0" * 64}), encoding="utf-8")


@pytest.mark.parametrize("mutator", [_make_symlink, _make_duplicate_json, _make_bad_fingerprint])
def test_reader_rejects_unsafe_or_invalid_snapshot(tmp_path, mutator):
    run_dir = _write_valid_snapshot(tmp_path)
    snapshot_path = run_dir / "governance-graph.json"
    if mutator is _make_symlink:
        (run_dir / "external.json").write_text("{}", encoding="utf-8")
    mutator(snapshot_path)

    result = GovernanceGraphSnapshotReader(tmp_path).read("run-1")

    assert result.status == "invalid"
    assert result.snapshot is None
    assert result.diagnostics


def test_reader_rejects_unsafe_run_id(tmp_path):
    _write_run(tmp_path)

    result = GovernanceGraphSnapshotReader(tmp_path).read("../escape")

    assert result.status == "invalid"
    assert result.diagnostics[0]["code"] == "invalid_run_id"
