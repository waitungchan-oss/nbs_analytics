from __future__ import annotations

import json
from pathlib import Path

from backend.agents.canonical_evidence_registry import CanonicalEvidenceRegistry
from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.workflow_store import WorkflowStore
from backend.services.governance_telemetry_service import GovernanceTelemetryService
from backend.services.governance_telemetry_service import MAX_DURATION_MS, MAX_REPAIR_LOOPS, MAX_TOKEN_COUNT
from backend.services.governance_telemetry_aggregation import TelemetryAggregator


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_policy(root: Path) -> None:
    _write_json(root / "agent_config" / "workflow_retention.json", {
        "schemaVersion": "agent-workflow-retention-v1",
        "retainDays": 90,
        "retainLatestTerminalRuns": 30,
        "stageArtifactMaxBytes": 5 * 1024 * 1024,
        "runArtifactSoftCapBytes": 25 * 1024 * 1024,
        "commandOutputTailCharacters": 12_000,
    })


def _valid_run(root: Path, run_id: str = "run-123") -> Path:
    _write_policy(root)
    run = root / ".nbs_agent_runtime" / "runs" / run_id
    _write_json(run / "manifest.json", {
        "schemaVersion": "agent-workflow-manifest-v1",
        "runId": run_id,
        "briefPath": "docs/briefs/telemetry.md",
        "briefSha256": "a" * 64,
        "gitBranch": "codex/governance-graph-phase-c",
        "gitHead": "b" * 40,
        "dirtyFiles": [],
        "createdAt": "2026-07-28T09:00:00+08:00",
        "contextFingerprint": "c" * 64,
    })
    _write_json(run / "status.json", {
        "schemaVersion": "agent-workflow-status-v1",
        "runId": run_id,
        "stage": "hermes",
        "status": "completed",
        "startedAt": "2026-07-28T09:00:00+08:00",
        "updatedAt": "2026-07-28T09:03:00+08:00",
        "completedAt": "2026-07-28T09:03:00+08:00",
        "message": "Workflow completed",
        "errorCode": None,
        "artifactBytes": 128,
    })
    _write_json(run / "approval.json", {
        "schemaVersion": "agent-workflow-approval-v1",
        "runId": run_id,
        "contractPath": "task-telemetry.json",
        "contractFingerprint": CanonicalEvidenceRegistry().for_kind("task_gate").contract_fingerprint,
        "approvedBaseSha": "d" * 40,
        "approvedAt": "2026-07-28T09:01:00+08:00",
        "authorizationStatus": "approved",
    })
    return run


def _write_implementation(run: Path, *, duration_ms: int = 1200, repair_loops: int = 0, usage: dict | None = None) -> None:
    _write_json(run / "implementation.json", {
        "schemaVersion": "implementation-run-report-v1",
        "status": "completed",
        "taskId": "task-telemetry",
        "contractFingerprint": "d" * 64,
        "startHead": "e" * 40,
        "endHead": "e" * 40,
        "changedFiles": [],
        "diffStat": {"files": 0, "lines": 0},
        "redEvidence": [],
        "greenEvidence": [],
        "repairLoopsUsed": repair_loops,
        "testFilesChanged": [],
        "productionFilesChanged": [],
        "findings": [],
        "durationMs": duration_ms,
        "usage": usage,
    })


def _write_graph(root: Path, run_id: str, *, freshness: str = "fresh", overall_status: str = "completed") -> Path:
    store = WorkflowStore(root)
    run = root / ".nbs_agent_runtime" / "runs" / run_id
    if freshness == "stale":
        _write_json(run / "hermes.json", {"overallStatus": "pass", "gitHead": "b" * 40})
        manifest_path = run / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["gitHead"] = "d" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store.write_artifact(run_id, "risk-classification.json", {
        "schemaVersion": "nbs-governance-risk-v1",
        "level": "R1",
        "surfaces": [],
        "evidenceRefs": [],
    })
    GovernanceGraphBuilder(root, store=store).persist(run_id)
    path = root / ".nbs_agent_runtime" / "runs" / run_id / "governance-graph.json"
    return path


def test_empty_runtime_is_unavailable_with_bounded_coverage(tmp_path):
    snapshot = GovernanceTelemetryService(tmp_path).build_snapshot()

    assert snapshot["schemaVersion"] == "governance-telemetry-snapshot-v1"
    assert snapshot["status"] == "unavailable"
    assert snapshot["coverage"]["eligibleRunCount"] == 0
    assert snapshot["tokenUsage"] is None
    assert snapshot["latestRunUpdatedAt"] is None


def test_supplied_usage_and_repair_loops_are_aggregated(tmp_path):
    run = _valid_run(tmp_path)
    _write_implementation(
        run,
        repair_loops=2,
        usage={"inputTokens": 120, "outputTokens": 30, "totalTokens": 150},
    )

    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()

    assert telemetry["agentActivity"]["lunaRepair"]["observedCount"] == 1
    assert telemetry["agentActivity"]["lunaRepair"]["total"] == 2
    assert telemetry["tokenUsage"]["totalTokens"] == 150
    assert telemetry["tokenUsage"]["runsWithUsage"] == 1
    assert telemetry["latestRunUpdatedAt"] == "2026-07-28T09:03:00+08:00"


def test_missing_task_gate_terra_and_protected_evidence_is_unknown(tmp_path):
    _valid_run(tmp_path)

    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()

    assert telemetry["gateFailures"]["taskGate"]["status"] == "unknown"
    assert telemetry["agentActivity"]["terraDiagnosis"]["status"] == "unknown"
    assert telemetry["protectedIncidents"]["status"] == "unknown"


def test_successful_spec_and_plan_gates_are_observed_with_zero_failures(tmp_path):
    run = _valid_run(tmp_path)
    _write_implementation(run)
    _write_json(run / "design-spec-gate.json", {
        "schemaVersion": "nbs-governance-gate-v1",
        "gateId": "spec-gate",
        "status": "passed",
        "fingerprint": "a" * 64,
        "evidenceRefs": [],
        "reasonCode": None,
    })
    _write_json(run / "plan-gate.json", {
        "schemaVersion": "nbs-governance-gate-v1",
        "gateId": "plan-gate",
        "status": "passed",
        "fingerprint": "b" * 64,
        "evidenceRefs": [],
        "reasonCode": None,
    })
    _write_graph(tmp_path, run.name)

    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()

    assert telemetry["gateFailures"]["specGate"] == {
        "status": "available", "failed": 0, "blocked": 0, "unknownCount": 0,
    }
    assert telemetry["gateFailures"]["planGate"] == {
        "status": "available", "failed": 0, "blocked": 0, "unknownCount": 0,
    }
    assert telemetry["evidenceHealth"]["stale"]["status"] == "available"


def test_oversized_numeric_evidence_is_unknown_and_does_not_enter_aggregates(tmp_path):
    run = _valid_run(tmp_path)
    _write_implementation(
        run,
        duration_ms=MAX_DURATION_MS + 1,
        repair_loops=MAX_REPAIR_LOOPS + 1,
        usage={
            "inputTokens": MAX_TOKEN_COUNT + 1,
            "outputTokens": 1,
            "totalTokens": MAX_TOKEN_COUNT + 2,
        },
    )

    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()

    assert telemetry["cycleTimes"]["implementation"]["observedCount"] == 0
    assert telemetry["agentActivity"]["lunaRepair"]["status"] == "unknown"
    assert telemetry["tokenUsage"] is None
    assert telemetry["status"] == "partial"


def test_unsupported_task_gate_has_per_run_unknown_coverage(tmp_path):
    _valid_run(tmp_path)

    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()

    assert telemetry["gateFailures"]["taskGate"]["unknownCount"] == 1
    assert telemetry["status"] == "partial"


def test_canonical_evidence_metrics_are_exact_and_exclude_deleted_runs():
    compact = {
        "runId": "retained", "briefName": "brief.md", "updatedAt": "2026-07-28T09:03:00+08:00",
        "status": "completed", "retentionState": "complete", "stages": {}, "governanceGraph": {"status": "unavailable"},
        "canonicalEvidence": {
            "task_gate": {"status": "available", "state": "failed"},
            "terra_diagnosis": {"status": "available", "state": "completed"},
            "protected_incident": {"status": "available", "state": "detected"},
        },
    }
    deleted = {**compact, "runId": "deleted", "retentionState": "deleted"}

    telemetry = TelemetryAggregator(reader=None).build([compact, deleted], [], 1)

    assert telemetry["coverage"]["eligibleRunCount"] == 1
    assert telemetry["gateFailures"]["taskGate"]["failed"] == 1
    assert telemetry["agentActivity"]["terraDiagnosis"]["completedCount"] == 1
    assert telemetry["protectedIncidents"]["observedCount"] == 1
