from __future__ import annotations

import json
from pathlib import Path

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
