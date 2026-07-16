from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.agent_operations_service import AgentOperationsService


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_run(root: Path, run_id: str = "run-123") -> Path:
    run = root / ".nbs_agent_runtime" / "runs" / run_id
    _write_json(run / "manifest.json", {
        "schemaVersion": "agent-workflow-manifest-v1",
        "runId": run_id,
        "briefPath": "docs/briefs/agent-operations.md",
        "briefSha256": "a" * 64,
        "gitBranch": "codex/agent-operations",
        "gitHead": "b" * 40,
        "dirtyFiles": [],
        "createdAt": "2026-07-16T09:00:00+08:00",
        "contextFingerprint": "c" * 64,
    })
    _write_json(run / "status.json", {
        "schemaVersion": "agent-workflow-status-v1",
        "runId": run_id,
        "stage": "authorization",
        "status": "awaiting_authorization",
        "startedAt": "2026-07-16T09:00:00+08:00",
        "updatedAt": "2026-07-16T09:03:00+08:00",
        "completedAt": None,
        "message": "Context ready",
        "errorCode": None,
        "artifactBytes": 128,
    })
    return run


def test_empty_runtime_returns_valid_snapshot(tmp_path):
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    assert snapshot["schemaVersion"] == "agent-operations-snapshot-v1"
    assert snapshot["summary"]["runCount"] == 0
    assert snapshot["runs"] == []
    assert snapshot["diagnostics"] == []
    assert not (tmp_path / ".nbs_agent_runtime").exists()


def test_runtime_root_symlink_or_external_path_fails_closed(tmp_path):
    external_root = tmp_path.parent / "external-runtime"
    external_root.mkdir()
    symlink_root = tmp_path / "runtime-link"
    symlink_root.symlink_to(tmp_path / ".nbs_agent_runtime", target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        AgentOperationsService(tmp_path, symlink_root)
    with pytest.raises(ValueError, match="must be inside project root"):
        AgentOperationsService(tmp_path, external_root)


def test_valid_run_is_compacted_and_sorted(tmp_path):
    _valid_run(tmp_path)
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    run = snapshot["runs"][0]
    assert run["runId"] == "run-123"
    assert run["briefName"] == "agent-operations.md"
    assert run["gitHeadShort"] == "bbbbbbbb"
    assert run["status"] == "awaiting_authorization"
    assert run["durationMs"] == 180_000
    assert snapshot["summary"]["awaitingAuthorizationCount"] == 1


def test_completed_at_takes_priority_over_updated_at_for_duration(tmp_path):
    run = _valid_run(tmp_path)
    status_path = run / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["completedAt"] = "2026-07-16T09:02:00+08:00"
    status["updatedAt"] = "2026-07-16T09:10:00+08:00"
    _write_json(status_path, status)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"][0]["durationMs"] == 120_000


def test_malformed_or_symlink_run_artifact_returns_bounded_diagnostics(tmp_path):
    malformed = _valid_run(tmp_path, "malformed")
    manifest = json.loads((malformed / "manifest.json").read_text(encoding="utf-8"))
    manifest["x" * 1_000] = "unexpected"
    _write_json(malformed / "manifest.json", manifest)

    symlinked = _valid_run(tmp_path, "symlinked")
    outside = tmp_path / "outside.json"
    _write_json(outside, {})
    (symlinked / "manifest.json").unlink()
    (symlinked / "manifest.json").symlink_to(outside)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert {item["path"] for item in snapshot["diagnostics"]} == {
        ".nbs_agent_runtime/runs/malformed",
        ".nbs_agent_runtime/runs/symlinked",
    }
    assert all("\n" not in item["reason"] and len(item["reason"]) <= 160 for item in snapshot["diagnostics"])
    assert str(tmp_path) not in json.dumps(snapshot["diagnostics"])


def test_os_error_diagnostic_does_not_leak_absolute_artifact_path(tmp_path):
    run = _valid_run(tmp_path)
    manifest_path = run / "manifest.json"
    manifest_path.chmod(0)
    try:
        snapshot = AgentOperationsService(tmp_path).build_snapshot()
    finally:
        manifest_path.chmod(0o600)

    assert snapshot["runs"] == []
    assert str(tmp_path) not in json.dumps(snapshot["diagnostics"])
