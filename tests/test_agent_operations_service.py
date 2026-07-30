from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.canonical_evidence_models import CanonicalEvidenceEnvelope
from backend.agents.canonical_evidence_registry import CanonicalEvidenceRegistry
from backend.agents.canonical_evidence_writer import CanonicalEvidenceWriter
from backend.agents.context_agent_service import build_context_evidence_payload
from backend.agents.evidence_models import EvidenceBundle
from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.workflow_store import WorkflowStore
from backend.services.agent_operations_service import (
    DEFAULT_STAGE_ARTIFACT_MAX_BYTES,
    AgentOperationsService,
)


def _canonical_task_gate(run_id: str) -> CanonicalEvidenceEnvelope:
    entry = CanonicalEvidenceRegistry().for_kind("task_gate")
    unsigned = {
        "schemaVersion": entry.schema_version, "artifactKind": "task_gate", "runId": run_id,
        "writer": entry.writer, "writerVersion": "1.0.0", "contractFingerprint": "a" * 64,
        "status": "failed", "reasonCode": "gate_failed",
        "lifecycle": {"createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:03Z"},
        "payload": {"taskId": "task-1", "decision": "failed", "requiredEvidenceKinds": ["implementation"], "missingEvidenceKinds": []},
    }
    from backend.agents.workflow_models import canonical_sha256
    return CanonicalEvidenceEnvelope.from_dict({**unsigned, "evidenceFingerprint": canonical_sha256(unsigned)})


def _write_approved_task_gate(root: Path, run: Path) -> CanonicalEvidenceEnvelope:
    envelope = _canonical_task_gate(run.name)
    _write_json(run / "approval.json", {
        "schemaVersion": "agent-workflow-approval-v1", "runId": run.name,
        "contractPath": "task-1.json", "contractFingerprint": envelope.contract_fingerprint,
        "approvedBaseSha": "d" * 40, "approvedAt": "2026-07-28T00:01:00+00:00",
        "authorizationStatus": "approved",
    })
    CanonicalEvidenceWriter(root).write_final(run.name, envelope)
    return envelope


def _write_governance_gate(run: Path, *, filename: str, gate_id: str, status: str, reason: str | None) -> None:
    _write_json(run / filename, {
        "schemaVersion": "nbs-governance-gate-v1",
        "gateId": gate_id,
        "status": status,
        "fingerprint": "a" * 64,
        "evidenceRefs": [],
        "reasonCode": reason,
    })


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_policy(root: Path, *, stage_artifact_max_bytes: int = 5 * 1024 * 1024) -> None:
    _write_json(root / "agent_config" / "workflow_retention.json", {
        "schemaVersion": "agent-workflow-retention-v1",
        "retainDays": 90,
        "retainLatestTerminalRuns": 30,
        "stageArtifactMaxBytes": stage_artifact_max_bytes,
        "runArtifactSoftCapBytes": 25 * 1024 * 1024,
        "commandOutputTailCharacters": 12_000,
    })


def _valid_run(root: Path, run_id: str = "run-123") -> Path:
    if not (root / "agent_config" / "workflow_retention.json").exists():
        _write_policy(root)
    run = root / ".nbs_agent_runtime" / "runs" / run_id
    _write_json(run / "manifest.json", {
        "schemaVersion": "agent-workflow-manifest-v1",
        "runId": run_id,
        "briefPath": "docs/briefs/agent-operations.md",
        "briefSha256": "a" * 64,
        "gitBranch": "codex/agent-orchestrator-phase1",
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


def _write_event(run: Path, event_id: int, stage: str, occurred_at: str) -> None:
    payload = {
        "schemaVersion": "agent-workflow-event-v1",
        "runId": run.name,
        "eventId": f"event-{event_id}",
        "eventType": "status_transition",
        "fromStatus": "context_running",
        "toStatus": "awaiting_authorization",
        "occurredAt": occurred_at,
        "message": "stage transition",
        "metadata": {"stage": stage},
    }
    with (run / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _verification_command() -> dict:
    return {
        "label": "pytest_targeted",
        "argv": [".venv/bin/python", "-m", "pytest", "tests/test_agent_operations_service.py", "-q"],
        "exitCode": 0,
        "stdoutTail": "1 passed",
        "stderrTail": "",
    }


def _full_verification_payload() -> dict:
    return {
        "fullPytest": {
            "exitCode": 0,
            "stdoutTail": "100 passed",
            "stderrTail": "",
            "payload": {},
        },
        "acceptance": {"status": "passed", "checks": {}},
    }


def _write_valid_stage_artifacts(run: Path) -> None:
    _write_json(run / "context.json", {
        "schemaVersion": "context-summary-v1",
        "status": "ready",
        "taskUnderstanding": [],
        "systemBoundaries": [],
        "relevantFiles": [],
        "dependencies": [],
        "recommendedTests": [],
        "risks": [],
        "unknowns": [],
        "contextFingerprint": "a" * 64,
    })
    _write_json(run / "implementation.json", {
        "schemaVersion": "implementation-run-report-v1",
        "status": "completed",
        "taskId": "task-2",
        "contractFingerprint": "b" * 64,
        "startHead": "c" * 40,
        "endHead": "c" * 40,
        "changedFiles": [],
        "diffStat": {"files": 0, "lines": 0},
        "redEvidence": [],
        "greenEvidence": [],
        "repairLoopsUsed": 0,
        "testFilesChanged": [],
        "productionFilesChanged": [],
        "findings": [],
        "durationMs": 1200,
        "usage": {"inputTokens": 80, "outputTokens": 20},
    })
    _write_json(run / "targeted-verification.json", {"commands": [_verification_command()]})
    _write_json(run / "review.json", {
        "schemaVersion": "review-report-v1",
        "verdict": "changes_required",
        "durationMs": 400,
        "findings": [{
            "severity": "medium",
            "file": "backend/services/agent_operations_service.py",
            "line": 1,
            "rule": "R1",
            "evidence": "Add coverage",
            "impact": "Missing regression coverage",
            "recommendedAction": "Add coverage",
        }],
        "requirementCoverage": ["Task 2"],
        "testCoverage": ["tests/test_agent_operations_service.py"],
        "baselineRisk": "none",
        "residualRisk": [],
        "hermesRequiredChecks": ["post-change"],
        "reviewFingerprint": "d" * 64,
        "usage": {"inputTokens": 40, "outputTokens": 10},
    })
    _write_json(run / "full-verification.json", _full_verification_payload())
    _write_json(run / "hermes.json", {"overallStatus": "pass"})


def _write_graph_projection(root: Path, run_id: str, *, stale: bool = False) -> Path:
    store = WorkflowStore(root)
    if stale:
        stale_run = root / ".nbs_agent_runtime" / "runs" / run_id
        _write_valid_stage_artifacts(stale_run)
        hermes_path = stale_run / "hermes.json"
        hermes = json.loads(hermes_path.read_text(encoding="utf-8"))
        hermes["gitHead"] = "b" * 40
        _write_json(hermes_path, hermes)
        manifest_path = root / ".nbs_agent_runtime" / "runs" / run_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["gitHead"] = "d" * 40
        _write_json(manifest_path, manifest)
    else:
        store.write_artifact(run_id, "risk-classification.json", {
            "schemaVersion": "nbs-governance-risk-v1",
            "level": "R1",
            "surfaces": [],
            "evidenceRefs": [],
        })
    GovernanceGraphBuilder(root, store=store).persist(run_id)
    return root / ".nbs_agent_runtime" / "runs" / run_id / "governance-graph.json"


def _make_invalid_graph_artifact(path: Path, root: Path, mode: str) -> None:
    if mode == "malformed_json":
        path.write_text("{bad json", encoding="utf-8")
    elif mode == "unknown_schema":
        _write_json(path, {"schemaVersion": "unknown-v1"})
    elif mode == "symlink":
        outside = root / "outside-graph.json"
        _write_json(outside, {})
        path.symlink_to(outside)
    elif mode == "oversize":
        path.write_text("x" * (DEFAULT_STAGE_ARTIFACT_MAX_BYTES + 1), encoding="utf-8")
    else:
        raise AssertionError(f"unknown invalid graph fixture: {mode}")


def test_existing_graph_projection_is_compacted_without_rebuilding(tmp_path, monkeypatch):
    run = _valid_run(tmp_path, "graph-ready")
    graph_path = _write_graph_projection(tmp_path, run.name)
    graph_fingerprint = json.loads(graph_path.read_text(encoding="utf-8"))["graphFingerprint"]
    before = graph_path.read_bytes()
    called = []
    monkeypatch.setattr(
        GovernanceGraphBuilder,
        "persist",
        lambda *args, **kwargs: called.append("persist"),
    )

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert called == []
    assert item["governanceGraph"]["status"] == "available"
    assert item["governanceGraph"]["snapshotFingerprint"] == graph_fingerprint
    assert item["governanceGraph"]["overallStatus"] == "awaiting_authorization"
    assert item["governanceGraph"]["nodes"][0] == {
        "nodeId": "risk", "status": "passed", "reasonCode": None,
    }
    assert graph_path.read_bytes() == before


def test_missing_graph_projection_is_unavailable_not_inferred(tmp_path):
    _valid_run(tmp_path, "graph-missing")

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["governanceGraph"] == {"status": "unavailable"}


@pytest.mark.parametrize("mode", ["malformed_json", "unknown_schema", "symlink", "oversize"])
def test_invalid_graph_projection_is_isolated_and_never_leaks_paths(tmp_path, mode):
    good = _valid_run(tmp_path, "good")
    bad = _valid_run(tmp_path, "bad")
    _write_graph_projection(tmp_path, good.name)
    graph_path = bad / "governance-graph.json"
    _make_invalid_graph_artifact(graph_path, tmp_path, mode)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    by_id = {item["runId"]: item for item in snapshot["runs"]}
    assert set(by_id) == {"good", "bad"}
    assert by_id["good"]["governanceGraph"]["status"] == "available"
    assert by_id["bad"]["governanceGraph"]["status"] == "invalid"
    assert by_id["bad"]["governanceGraph"]["diagnostics"] in (
        [{"code": "invalid_projection"}], [{"code": "unsafe_projection"}],
    )
    assert str(tmp_path) not in json.dumps(snapshot)


def test_persisted_stale_graph_is_exposed_as_non_pass_state(tmp_path):
    run = _valid_run(tmp_path, "graph-stale")
    graph_path = _write_graph_projection(tmp_path, run.name, stale=True)

    graph = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]["governanceGraph"]

    assert graph_path.is_file()
    assert graph["status"] == "available"
    assert graph["freshness"] == "stale"
    assert graph["overallStatus"] == "blocked"


def test_empty_runtime_returns_valid_snapshot(tmp_path):
    _write_policy(tmp_path)
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
    assert run["gitBranch"] == "codex/agent-orchestrator-phase1"
    assert run["stage"] == "authorization"
    assert run["status"] == "awaiting_authorization"
    assert run["errorCode"] is None
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


def test_completed_run_aggregates_review_verification_hermes_and_tokens(tmp_path):
    run = _valid_run(tmp_path, "run-complete")
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status.update({
        "stage": "hermes",
        "status": "completed",
        "completedAt": "2026-07-16T09:10:00+08:00",
        "updatedAt": "2026-07-16T09:10:00+08:00",
        "message": "Workflow completed",
    })
    _write_json(run / "status.json", status)
    _write_valid_stage_artifacts(run)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["stages"]["implementation"] == {"available": True, "durationMs": 1200}
    assert item["findings"] == {
        "count": 1,
        "highestSeverity": "medium",
        "items": [{"severity": "medium", "code": "R1", "message": "Add coverage"}],
    }
    assert item["verification"] == {"status": "pass"}
    assert item["hermes"] == {"status": "pass"}
    assert item["tokenUsage"] == {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150}
    assert item["retentionState"] == "complete"


def test_completed_run_compacts_documentation_application_sidecars(tmp_path):
    run = _valid_run(tmp_path, "run-documentation")
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status.update({
        "stage": "hermes",
        "status": "completed",
        "completedAt": "2026-07-18T12:00:00+08:00",
        "updatedAt": "2026-07-18T12:00:00+08:00",
        "message": "Workflow completed",
    })
    _write_json(run / "status.json", status)
    _write_valid_stage_artifacts(run)
    _write_json(run / "documentation-application.json", {
        "schemaVersion": "documentation-application-v1",
        "taskId": "task-7",
        "proposalFingerprint": "a" * 64,
        "status": "applied",
        "generatedAt": "2026-07-18T12:00:00+08:00",
        "applications": [{
            "targetKind": "brief_backfill",
            "targetIdentity": "docs/briefs/task-7.md",
            "operation": "update_managed_block",
            "result": "applied",
            "appliedSha256": "b" * 64,
        }],
    })
    _write_json(run / "documentation-telemetry.json", {
        "schemaVersion": "documentation-telemetry-v1",
        "proposalCount": 2,
        "result": "applied",
    })

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["documentation"] == {
        "status": "applied",
        "proposalCount": 2,
        "appliedTargetCount": 1,
        "pendingApprovalCount": 1,
        "updatedAt": "2026-07-18T12:00:00+08:00",
    }


def test_missing_documentation_sidecars_are_not_requested(tmp_path):
    _valid_run(tmp_path)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["documentation"] == {"status": "not_requested"}


def test_invalid_documentation_sidecar_returns_bounded_diagnostic(tmp_path):
    run = _valid_run(tmp_path)
    _write_json(run / "documentation-application.json", {"status": "applied"})

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "invalid_run"
    assert len(snapshot["diagnostics"][0]["reason"]) <= 160
    assert str(tmp_path) not in json.dumps(snapshot["diagnostics"])


def test_missing_usage_is_not_estimated(tmp_path):
    _valid_run(tmp_path)

    assert AgentOperationsService(tmp_path).build_snapshot()["runs"][0]["tokenUsage"] is None


@pytest.mark.parametrize(
    ("artifact_name", "payload"),
    [
        ("context.json", {"schemaVersion": "context-summary-v1", "status": "ready"}),
        ("implementation.json", {"schemaVersion": "implementation-run-report-v0", "status": "completed"}),
        ("implementation.json", {"schemaVersion": "implementation-run-report-v1", "status": "completed"}),
        ("targeted-verification.json", {"commands": [{"exitCode": 0}]}),
        ("review.json", {"schemaVersion": "review-report-v0", "verdict": "pass", "findings": []}),
        ("review.json", {"schemaVersion": "review-report-v1", "verdict": "pass", "findings": []}),
        ("full-verification.json", {"schemaVersion": "unknown-full-v1", **_full_verification_payload()}),
        ("hermes.json", {"schemaVersion": "unknown-hermes-v1", "overallStatus": "pass"}),
    ],
)
def test_stage_artifact_schema_or_fake_pass_fails_closed_without_token_usage(tmp_path, artifact_name, payload):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    _write_json(run / artifact_name, payload)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["summary"]["runCount"] == 0
    assert snapshot["diagnostics"][0]["code"] == "invalid_run"
    assert "tokenUsage" not in json.dumps(snapshot["runs"])


def test_legacy_full_verification_and_hermes_payloads_without_schema_remain_available(tmp_path):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["verification"] == {"status": "pass"}
    assert item["hermes"] == {"status": "pass"}


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"fullPytest": _full_verification_payload()["fullPytest"]}, "blocked"),
        ({
            "fullPytest": {**_full_verification_payload()["fullPytest"], "exitCode": 1},
        }, "fail"),
        ({
            "fullPytest": _full_verification_payload()["fullPytest"],
            "acceptance": {"status": "failed"},
        }, "fail"),
        ({"acceptance": {"status": "passed"}}, "blocked"),
    ],
)
def test_full_verification_real_partial_or_failed_artifacts_remain_available(tmp_path, payload, expected_status):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    _write_json(run / "full-verification.json", payload)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["verification"] == {"status": expected_status}


@pytest.mark.parametrize(
    "payload",
    [
        {"fullPytest": {**_full_verification_payload()["fullPytest"], "exitCode": True}},
        {"fullPytest": {"exitCode": 0, "stdoutTail": "", "stderrTail": ""}},
        {"fullPytest": {**_full_verification_payload()["fullPytest"], "unexpected": "field"}},
        {"fullPytest": _full_verification_payload()["fullPytest"], "unknown": {}},
        {"status": "pass", "commands": [_verification_command()]},
        {"fullPytest": None},
        {"acceptance": None},
    ],
)
def test_full_verification_requires_exact_real_artifact_fields(tmp_path, payload):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    _write_json(run / "full-verification.json", payload)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "invalid_run"


def test_hermes_warning_artifact_remains_available(tmp_path):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    _write_json(run / "hermes.json", {"overallStatus": "warning"})

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["hermes"] == {"status": "warning"}


def test_review_pass_without_strict_evidence_fails_closed(tmp_path):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    review_path = run / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review.update({
        "verdict": "pass",
        "findings": [],
        "requirementCoverage": [],
        "testCoverage": [],
        "residualRisk": [],
        "hermesRequiredChecks": [],
    })
    _write_json(review_path, review)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "invalid_run"


def test_context_evidence_v1_artifact_remains_available(tmp_path):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    evidence = build_context_evidence_payload(EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"objective": "Task 2", "scope": [], "forbidden": []},
        repository={"branch": "codex/agent-orchestrator-phase1", "head": "b" * 40, "dirtyFiles": []},
        guardrails={},
    ))
    _write_json(run / "context.json", evidence)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["stages"]["context"]["available"] is True


def test_retention_config_and_archive_state_are_compacted_without_apply(tmp_path):
    run = _valid_run(tmp_path)
    _write_json(run / "archive-summary.json", {
        "schemaVersion": "agent-workflow-archive-summary-v1",
        "runId": run.name,
    })

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["retention"] == {
        "retainDays": 90,
        "retainLatestTerminalRuns": 30,
        "stageArtifactMaxBytes": 5 * 1024 * 1024,
        "runArtifactSoftCapBytes": 25 * 1024 * 1024,
        "commandOutputTailCharacters": 12_000,
    }
    assert snapshot["runs"][0]["retentionState"] == "archived_summary"
    assert (run / "archive-summary.json").exists()


def test_invalid_retention_config_is_reported_as_unavailable(tmp_path):
    _valid_run(tmp_path)
    _write_json(tmp_path / "agent_config" / "workflow_retention.json", {"schemaVersion": "unknown"})

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["retention"] == {"status": "unavailable"}
    assert snapshot["diagnostics"] == [{
        "code": "retention_config_invalid",
        "path": "agent_config/workflow_retention.json",
        "reason": "retention policy is invalid",
    }]


def test_final_500_valid_events_supply_bounded_stage_timing_without_raw_events(tmp_path):
    run = _valid_run(tmp_path)
    for index in range(501):
        _write_event(run, index, "implementation", f"2026-07-16T09:{index // 60:02d}:{index % 60:02d}+00:00")
    (run / "events.jsonl").write_text("{bad json}\n" + (run / "events.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["stages"]["implementation"]["durationMs"] == 499_000
    assert "events" not in item
    assert "stage transition" not in json.dumps(item)


@pytest.mark.parametrize("artifact_name", ["review.json", "archive-summary.json", "events.jsonl"])
def test_symlink_or_non_regular_artifact_is_rejected(tmp_path, artifact_name):
    run = _valid_run(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"verdict":"pass"}', encoding="utf-8")
    (run / artifact_name).symlink_to(outside)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "unsafe_artifact"
    assert str(tmp_path) not in json.dumps(snapshot["diagnostics"])


def test_stage_directory_is_rejected_as_a_non_regular_artifact(tmp_path):
    run = _valid_run(tmp_path)
    (run / "review.json").mkdir()

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "unsafe_artifact"


def test_oversize_stage_and_unknown_manifest_schema_are_isolated(tmp_path):
    _write_policy(tmp_path, stage_artifact_max_bytes=2_048)
    good = _valid_run(tmp_path, "good")
    bad = _valid_run(tmp_path, "bad")
    _write_json(bad / "review.json", {"message": "x" * 4_096})
    unknown = _valid_run(tmp_path, "unknown")
    manifest = json.loads((unknown / "manifest.json").read_text(encoding="utf-8"))
    manifest["schemaVersion"] = "unknown-manifest-v1"
    _write_json(unknown / "manifest.json", manifest)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert [item["runId"] for item in snapshot["runs"]] == [good.name]
    assert {item["runId"] for item in snapshot["diagnostics"]} == {"bad", "unknown"}
    assert {item["code"] for item in snapshot["diagnostics"]} == {"unsafe_artifact", "invalid_run"}


def test_bad_run_is_isolated_without_leaking_paths_or_sensitive_stage_content(tmp_path):
    good = _valid_run(tmp_path, "good")
    _write_valid_stage_artifacts(good)
    bad = _valid_run(tmp_path, "bad")
    (bad / "manifest.json").write_text("{bad json", encoding="utf-8")
    review_path = good / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["findings"][0]["severity"] = "high"
    review["findings"][0]["rule"] = "R2"
    review["findings"][0]["evidence"] = f"runner argv /Users/test/prompt stdout {tmp_path}"
    _write_json(review_path, review)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert [item["runId"] for item in snapshot["runs"]] == ["good"]
    assert snapshot["diagnostics"][0]["runId"] == "bad"
    rendered = json.dumps(snapshot)
    assert str(tmp_path) not in rendered
    assert "argv" not in rendered
    assert "stdout" not in rendered
    assert "prompt" not in rendered


def test_status_message_is_sanitized_before_it_reaches_the_snapshot(tmp_path):
    run = _valid_run(tmp_path)
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status["message"] = f"runner argv /Users/test/prompt stdout {tmp_path}"
    _write_json(run / "status.json", status)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["message"] == "finding detail unavailable"


@pytest.mark.parametrize(
    "message",
    [
        "note-/Users/secret",
        "value._/Users/secret",
        "tag~/Users/secret",
        ". /Users/secret",
    ],
)
def test_status_message_rejects_absolute_posix_path_after_any_delimiter(tmp_path, message):
    run = _valid_run(tmp_path)
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status["message"] = message
    _write_json(run / "status.json", status)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["message"] == "finding detail unavailable"
    assert "/Users/secret" not in json.dumps(item)


def test_general_free_text_rejects_relative_branch_path():
    assert AgentOperationsService._safe_message("codex/branch") == "finding detail unavailable"


@pytest.mark.parametrize("uri", ["mailto:ops@example.com", "data:text/plain,secret", "urn:nbs:run-123"])
def test_general_free_text_rejects_no_slash_uri_schemes(uri):
    assert AgentOperationsService._safe_message(uri) == "finding detail unavailable"


@pytest.mark.parametrize("stage", ["database_write", "service_management", "unknown_stage"])
def test_stage_display_uses_exact_allowlist(stage):
    assert AgentOperationsService._safe_stage(stage) == "value unavailable"


@pytest.mark.parametrize(
    "stage",
    ["context", "authorization", "implementation", "targeted_verification", "review", "full_verification", "hermes"],
)
def test_stage_display_preserves_exact_allowlisted_values(stage):
    assert AgentOperationsService._safe_stage(stage) == stage


@pytest.mark.parametrize(
    "branch",
    [
        "codex//agent-orchestrator-phase1",
        "/codex/agent-orchestrator-phase1",
        "codex/agent-orchestrator-phase1/",
        "codex/./agent-orchestrator-phase1",
        "codex/../agent-orchestrator-phase1",
        "codex/agent..orchestrator",
        "codex/agent.",
        "codex/agent.lock",
        "codex/@{agent",
        "codex\\agent",
        "codex/agent branch",
        "codex/agent:branch",
        "codex/agent?branch",
        "codex/agent*branch",
        "codex/[agent]",
        "codex/agent\x01branch",
        ".hidden",
        "codex/.hidden",
    ],
)
def test_git_branch_rejects_invalid_refname_forms(branch):
    assert AgentOperationsService._safe_git_branch(branch) == "value unavailable"


def test_git_branch_preserves_normal_codex_branch():
    assert AgentOperationsService._safe_git_branch("codex/agent-orchestrator-phase1") == "codex/agent-orchestrator-phase1"


@pytest.mark.parametrize("unsafe_text", [
    "note=/Users//secret",
    "stdoutTail=/Users/[secret]/file",
    "Exception: failed",
])
def test_general_free_text_fails_closed_for_sensitive_or_path_like_values(tmp_path, unsafe_text):
    run = _valid_run(tmp_path)
    _write_valid_stage_artifacts(run)
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status.update({"message": unsafe_text, "errorCode": unsafe_text})
    _write_json(run / "status.json", status)
    review = json.loads((run / "review.json").read_text(encoding="utf-8"))
    review["findings"][0]["evidence"] = unsafe_text
    _write_json(run / "review.json", review)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["message"] == "finding detail unavailable"
    assert item["errorCode"] == "finding detail unavailable"
    assert item["findings"]["items"][0]["message"] == "finding detail unavailable"
    assert unsafe_text not in json.dumps(item)


@pytest.mark.parametrize(
    ("message", "error_code"),
    [
        ("Cannot open '/Users/analyst/private/secret.db'", "OSError: '/Users/analyst/private/secret.db'"),
        ("See file:///Users/analyst/private/secret.db", "file:///Users/analyst/private/secret.db"),
        ("detail=[/Users/analyst/secret]", "detail=[/Users/analyst/secret]"),
        ("path=/Users/analyst/secret", "path=/Users/analyst/secret"),
        ("argument=[/Users/analyst/secret]", "option=/Users/analyst/secret"),
    ],
)
def test_status_free_text_is_sanitized_for_quoted_paths_uris_and_exception_text(tmp_path, message, error_code):
    run = _valid_run(tmp_path)
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status.update({"message": message, "errorCode": error_code})
    _write_json(run / "status.json", status)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["message"] == "finding detail unavailable"
    assert item["errorCode"] == "finding detail unavailable"
    rendered = json.dumps(item)
    assert "/Users/" not in rendered
    assert "file://" not in rendered
    assert "OSError" not in rendered
    assert "analyst" not in rendered
    assert "secret" not in rendered


@pytest.mark.parametrize(
    ("field", "unsafe_value", "snapshot_field"),
    [
        ("briefPath", "file:///Users/analyst/secret", "briefName"),
        ("gitBranch", "detail=[/Users/analyst/secret]", "gitBranch"),
        ("stage", "path=/Users/analyst/secret", "stage"),
    ],
)
def test_artifact_identity_fields_are_sanitized_before_reaching_snapshot(tmp_path, field, unsafe_value, snapshot_field):
    run = _valid_run(tmp_path)
    manifest_path = run / "manifest.json"
    status_path = run / "status.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    target = manifest if field in {"briefPath", "gitBranch"} else status
    target[field] = unsafe_value
    _write_json(manifest_path, manifest)
    _write_json(status_path, status)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item[snapshot_field] == "value unavailable"
    rendered = json.dumps(item)
    assert "file://" not in rendered
    assert "/Users/" not in rendered
    assert "analyst" not in rendered
    assert "secret" not in rendered


def test_unsafe_run_id_is_isolated_without_echoing_its_identity(tmp_path):
    _valid_run(tmp_path, "run-123")
    _valid_run(tmp_path, "file:secret")

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert [item["runId"] for item in snapshot["runs"]] == ["run-123"]
    assert snapshot["diagnostics"][-1]["code"] == "invalid_run"
    assert "runId" not in snapshot["diagnostics"][-1]
    assert "file:" not in json.dumps(snapshot["diagnostics"])
    assert "secret" not in json.dumps(snapshot["diagnostics"])


@pytest.mark.parametrize(
    "archive_summary",
    [
        {"schemaVersion": "unknown", "runId": "run-123"},
        {"schemaVersion": "agent-workflow-archive-summary-v1", "runId": "file:///Users/analyst/secret"},
    ],
)
def test_archive_summary_requires_matching_schema_and_run_identity(tmp_path, archive_summary):
    run = _valid_run(tmp_path)
    _write_json(run / "archive-summary.json", archive_summary)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"][0]["retentionState"] == "complete"
    assert snapshot["diagnostics"] == [{
        "code": "invalid_archive_summary",
        "path": ".nbs_agent_runtime/runs/run-123",
        "reason": "archive summary is invalid",
        "runId": "run-123",
    }]
    assert "file://" not in json.dumps(snapshot)
    assert "/Users/" not in json.dumps(snapshot)


def test_non_object_archive_summary_is_not_treated_as_archived(tmp_path):
    run = _valid_run(tmp_path)
    (run / "archive-summary.json").write_text("[]", encoding="utf-8")

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "invalid_run"
    assert snapshot["diagnostics"][0]["runId"] == "run-123"


@pytest.mark.parametrize("artifact_name", ["manifest.json", "status.json"])
def test_oversize_core_artifact_is_isolated_before_json_load(tmp_path, monkeypatch, artifact_name):
    _write_policy(tmp_path, stage_artifact_max_bytes=2_048)
    run = _valid_run(tmp_path)
    artifact_path = run / artifact_name
    artifact_path.write_bytes(b"x" * 2_049)
    original_open = Path.open
    opened_core_artifact = False

    def guarded_open(path, *args, **kwargs):
        nonlocal opened_core_artifact
        if path == artifact_path:
            opened_core_artifact = True
            raise AssertionError("oversize core artifact must be rejected before json.load")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert opened_core_artifact is False
    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "unsafe_artifact"
    assert str(tmp_path) not in json.dumps(snapshot["diagnostics"])


def test_oversize_retention_config_is_not_opened_and_uses_bounded_default(tmp_path, monkeypatch):
    run = _valid_run(tmp_path)
    retention_path = tmp_path / "agent_config" / "workflow_retention.json"
    retention_path.write_bytes(b"x" * (DEFAULT_STAGE_ARTIFACT_MAX_BYTES + 1))
    original_open = Path.open
    opened_retention_config = False

    def guarded_open(path, *args, **kwargs):
        nonlocal opened_retention_config
        if path == retention_path:
            opened_retention_config = True
            raise AssertionError("oversize retention config must be rejected before json.load")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    assert opened_retention_config is False
    assert [item["runId"] for item in snapshot["runs"]] == [run.name]
    assert snapshot["retention"] == {"status": "unavailable"}
    assert snapshot["diagnostics"] == [{
        "code": "retention_config_invalid",
        "path": "agent_config/workflow_retention.json",
        "reason": "retention policy is invalid",
    }]


def test_event_reader_tails_from_end_without_iterating_or_reading_the_whole_file(tmp_path, monkeypatch):
    run = _valid_run(tmp_path)
    events_path = run / "events.jsonl"
    events_path.write_text(
        "".join(
            json.dumps({
                "schemaVersion": "agent-workflow-event-v1",
                "runId": run.name,
                "eventId": f"event-{index}",
                "eventType": "status_transition",
                "fromStatus": "context_running",
                "toStatus": "awaiting_authorization",
                "occurredAt": f"2026-07-16T00:{index // 60:02d}:{index % 60:02d}+00:00",
                "message": "stage transition",
                "metadata": {"stage": "implementation"},
            }) + "\n"
            for index in range(3_000)
        ),
        encoding="utf-8",
    )

    class TailReadSpy:
        def __init__(self, handle):
            self._handle = handle
            self.bytes_read = 0

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def read(self, size=-1):
            assert size != -1, "event reader must use fixed-size reads"
            data = self._handle.read(size)
            self.bytes_read += len(data)
            return data

        def __iter__(self):
            raise AssertionError("event reader must not iterate the whole file")

        def seek(self, *args):
            return self._handle.seek(*args)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    original_open = Path.open
    readers = []

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == events_path:
            reader = TailReadSpy(handle)
            readers.append(reader)
            return reader
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["stages"]["implementation"]["durationMs"] == 499_000
    assert readers
    assert readers[0].bytes_read < events_path.stat().st_size


def test_event_reader_stops_at_fixed_scan_budget_for_invalid_or_other_run_events(tmp_path, monkeypatch):
    run = _valid_run(tmp_path)
    events_path = run / "events.jsonl"
    event_scan_budget = 1 * 1024 * 1024
    invalid_line = (b"x" * 2_047) + b"\n"
    events_path.write_bytes(invalid_line * ((event_scan_budget // len(invalid_line)) + 2))
    assert event_scan_budget < events_path.stat().st_size < DEFAULT_STAGE_ARTIFACT_MAX_BYTES

    class TailReadSpy:
        def __init__(self, handle):
            self._handle = handle
            self.bytes_read = 0

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def read(self, size=-1):
            assert size != -1, "event reader must use fixed-size reads"
            data = self._handle.read(size)
            self.bytes_read += len(data)
            return data

        def __iter__(self):
            raise AssertionError("event reader must not iterate the whole file")

        def seek(self, *args):
            return self._handle.seek(*args)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    original_open = Path.open
    readers = []

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == events_path:
            reader = TailReadSpy(handle)
            readers.append(reader)
            return reader
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)

    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

    assert item["stages"]["implementation"]["durationMs"] is None
    assert readers
    assert readers[0].bytes_read == event_scan_budget


def test_agent_operations_exposes_compact_canonical_evidence_without_writing(tmp_path):
    run = _valid_run(tmp_path, "canonical-evidence")
    envelope = _write_approved_task_gate(tmp_path, run)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    snapshot = AgentOperationsService(tmp_path).build_snapshot()

    evidence = snapshot["runs"][0]["canonicalEvidence"]["task_gate"]
    assert evidence["status"] == "available"
    assert evidence["state"] == "failed"
    assert evidence["sha256"] == envelope.evidence_fingerprint
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    serialized = json.dumps(snapshot)
    assert str(tmp_path) not in serialized
    assert "taskId" not in serialized


def test_agent_operations_compacts_verified_spec_plan_gates_without_graph(tmp_path):
    run = _valid_run(tmp_path, "compact-gates")
    _write_governance_gate(
        run, filename="design-spec-gate.json", gate_id="spec-gate", status="passed", reason=None,
    )
    _write_governance_gate(
        run, filename="plan-gate.json", gate_id="plan-gate", status="failed", reason="gate_failed",
    )

    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    gates = snapshot["runs"][0]["governanceGates"]

    assert gates["specGate"]["status"] == "available"
    assert gates["specGate"]["state"] == "passed"
    assert gates["planGate"]["status"] == "available"
    assert gates["planGate"]["state"] == "failed"
    assert str(tmp_path) not in json.dumps(gates)
