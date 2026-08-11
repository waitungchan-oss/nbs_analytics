from __future__ import annotations

import json

from backend.agents.evidence_models import canonical_fingerprint
from scripts.hermes_live_ab_acceptance import assess_live_ab_receipts


HEAD = "a" * 40


def _receipt(*, run_id: str, session_id: str, sequence: int, recall_mode: str, **overrides: object) -> dict[str, object]:
    status = "disabled" if recall_mode == "off" else "activated"
    manifest_id = canonical_fingerprint({"arm": run_id})
    value: dict[str, object] = {
        "schemaVersion": "hermes-runner-capability-receipt-v1", "manifestId": manifest_id, "runId": run_id, "sessionId": session_id,
        "sequence": sequence, "recallMode": recall_mode, "gitHead": HEAD,
        "projectId": "nbs_analytics", "workspaceKind": "isolated_worktree",
        "workspaceFingerprint": "c" * 64, "taskFingerprint": "b" * 64,
        "briefFingerprint": "d" * 64, "allowedFilesFingerprint": "e" * 64,
        "commandsFingerprint": "f" * 64, "provider": "hermes",
        "model": "deepseek-v4-flash", "reasoningProfile": "max",
        "cleanWorktreeFingerprint": "1" * 64, "status": "completed",
        "cacheReplayDetected": False, "inputTokens": 1000, "outputTokens": 100,
        "p95Ms": 200, "provenanceCoverage": 1.0, "provenanceSourceCount": 1,
        "provenanceCoveredCount": 1, "responseId": f"response-{run_id}",
        "priorResponseIds": [], "sensitiveCaptureCount": 0,
        "writerDisabled": True, "baselineUnchanged": True, "formalScopeUnchanged": True,
        "reviewNoRegression": True, "hermesNoRegression": True,
        "activationReceipt": {"schemaVersion": "hermes-recall-activation-receipt-v1", "activationId": canonical_fingerprint({"manifestId": manifest_id, "runId": run_id, "sessionId": session_id, "recallMode": recall_mode, "status": status}), "recallMode": recall_mode, "status": status},
    }
    return {**value, **overrides}


def _write(root, relative: str, value: dict[str, object]) -> str:
    path = root / ".nbs_agent_runtime" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return relative


def _paths(tmp_path, **treatment: object) -> tuple[str, str]:
    control = _write(tmp_path, "live-ab/acceptance/control/receipt.json", _receipt(run_id="control-001", session_id="session-control", sequence=1, recall_mode="off"))
    treated = _write(tmp_path, "live-ab/acceptance/treatment/receipt.json", _receipt(run_id="treatment-002", session_id="session-treatment", sequence=2, recall_mode="on", **treatment))
    return control, treated


def test_assessment_reports_ready_with_explicit_safe_metric_deltas(tmp_path):
    control, treatment = _paths(tmp_path, inputTokens=700, outputTokens=120, p95Ms=250)

    result = assess_live_ab_receipts(control, treatment, project_root=tmp_path)

    assert result.status == "ready"
    assert result.reasons == ()
    assert result.metrics == {"inputTokenReduction": 0.3, "outputTokenDelta": 20, "p95LatencyDeltaMs": 50}
    assert result.evidence_paths == (control, treatment)
    assert "prompt" not in json.dumps(result.to_dict()).lower()
    assert str(tmp_path) not in json.dumps(result.to_dict())


def test_assessment_rejects_metric_gates_but_blocks_invalid_receipt_evidence(tmp_path):
    control, treatment = _paths(tmp_path, inputTokens=900, p95Ms=801)
    rejected = assess_live_ab_receipts(control, treatment, project_root=tmp_path)
    assert rejected.status == "acceptance_rejected"
    assert rejected.reasons == ("token_reduction_below_threshold", "latency_exceeds_limit")

    _write(tmp_path, treatment, _receipt(run_id="treatment-002", session_id="session-treatment", sequence=2, recall_mode="on", inputTokens=700, provenanceCoverage=0.9, provenanceSourceCount=10, provenanceCoveredCount=9))
    rejected = assess_live_ab_receipts(control, treatment, project_root=tmp_path)
    assert rejected.status == "acceptance_rejected"
    assert rejected.reasons == ("provenance_coverage_below_full",)


def test_assessment_blocks_identity_replay_sensitive_and_activation_mismatches(tmp_path):
    control, treatment = _paths(tmp_path, cacheReplayDetected=True, priorResponseIds=["response-treatment-002"])
    assert assess_live_ab_receipts(control, treatment, project_root=tmp_path).reasons == ("cache_replay_detected",)

    _write(tmp_path, treatment, _receipt(run_id="treatment-002", session_id="session-control", sequence=2, recall_mode="on"))
    assert assess_live_ab_receipts(control, treatment, project_root=tmp_path).reasons == ("reused_session_id",)

    _write(tmp_path, treatment, _receipt(run_id="treatment-002", session_id="session-treatment", sequence=2, recall_mode="on", inputTokens=700, sensitiveCaptureCount=1))
    assert assess_live_ab_receipts(control, treatment, project_root=tmp_path).reasons == ("sensitive_capture_detected",)

    invalid = _receipt(run_id="treatment-002", session_id="session-treatment", sequence=2, recall_mode="on")
    invalid["activationReceipt"] = {**invalid["activationReceipt"], "status": "disabled"}
    _write(tmp_path, treatment, invalid)
    assert assess_live_ab_receipts(control, treatment, project_root=tmp_path).reasons == ("activation_state_missing",)


def test_assessment_blocks_missing_or_unsafe_receipt_without_echoing_content(tmp_path):
    control, treatment = _paths(tmp_path)
    missing = assess_live_ab_receipts(control, "live-ab/acceptance/missing.json", project_root=tmp_path)
    assert missing.status == "blocked_runner_capability" and missing.reasons == ("completion_missing",)

    unsafe = _receipt(run_id="treatment-002", session_id="session-treatment", sequence=2, recall_mode="on")
    unsafe["rawPrompt"] = "do not expose"
    _write(tmp_path, treatment, unsafe)
    result = assess_live_ab_receipts(control, treatment, project_root=tmp_path)
    assert result.reasons == ("completion_missing",)
    assert "do not expose" not in json.dumps(result.to_dict())


def test_assessment_rejects_missing_schema_and_intermediate_runtime_symlink(tmp_path):
    control, treatment = _paths(tmp_path)
    raw = json.loads((tmp_path / ".nbs_agent_runtime" / control).read_text(encoding="utf-8"))
    raw.pop("schemaVersion")
    _write(tmp_path, control, raw)
    assert assess_live_ab_receipts(control, treatment, project_root=tmp_path).reasons == ("completion_missing",)

    runtime = tmp_path / ".nbs_agent_runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "receipt.json").write_text(json.dumps(_receipt(run_id="control-003", session_id="session-third", sequence=1, recall_mode="off")), encoding="utf-8")
    (runtime / "live-ab" / "link").symlink_to(outside, target_is_directory=True)
    result = assess_live_ab_receipts("live-ab/link/receipt.json", treatment, project_root=tmp_path)
    assert result.status == "blocked_runner_capability"
    assert result.reasons == ("completion_missing",)
