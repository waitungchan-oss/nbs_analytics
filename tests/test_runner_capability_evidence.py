from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from backend.agents.runner_capability_evidence import (
    RUNNER_CAPABILITY_SCHEMA,
    RunnerCapabilityComparison,
    RunnerCapabilityEvidence,
    RunnerCapabilityEvidenceError,
    build_capability_evidence,
    compare_capability_runs,
)
from backend.agents.evidence_models import canonical_fingerprint


GIT_HEAD = "a" * 40
TASK_FINGERPRINT = "b" * 64


def _run(*, run_id: str, sequence: int, recall_mode: str, **overrides: object) -> dict:
    result = {
        "runId": run_id,
        "sequence": sequence,
        "recallMode": recall_mode,
        "gitHead": GIT_HEAD,
        "projectId": "nbs_analytics",
        "workspaceKind": "isolated_worktree",
        "workspaceFingerprint": "c" * 64,
        "taskFingerprint": TASK_FINGERPRINT,
        "briefFingerprint": "d" * 64,
        "allowedFilesFingerprint": "e" * 64,
        "commandsFingerprint": "f" * 64,
        "provider": "hermes",
        "model": "deepseek-v4-flash",
        "status": "completed",
        "cacheReplayDetected": False,
        "inputTokens": 1000,
        "outputTokens": 100,
        "p95Ms": 200,
        "provenanceCoverage": 1.0,
        "sensitiveCaptureCount": 0,
        "writerDisabled": True,
        "baselineUnchanged": True,
        "formalScopeUnchanged": True,
        "reviewNoRegression": True,
        "hermesNoRegression": True,
    }
    return {**result, **overrides}


def _evidence() -> RunnerCapabilityEvidence:
    return build_capability_evidence(
        _run(run_id="control-001", sequence=1, recall_mode="off"),
        _run(run_id="treatment-002", sequence=2, recall_mode="on"),
        expected_git_head=GIT_HEAD,
        expected_task_fingerprint=TASK_FINGERPRINT,
    )


def test_valid_pair_round_trips_and_is_immutable():
    evidence = _evidence()

    assert evidence.to_dict()["schemaVersion"] == RUNNER_CAPABILITY_SCHEMA
    assert RunnerCapabilityEvidence.from_dict(evidence.to_dict()).to_dict() == evidence.to_dict()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        evidence.git_head = "0" * 40


def test_evidence_identity_is_canonical_and_input_bound():
    first = _evidence()
    second = _evidence()

    assert len(first.evidence_id) == 64
    assert first.evidence_id == second.evidence_id
    assert first.evidence_id == first.recompute_evidence_id()
    assert build_capability_evidence(
        _run(run_id="control-003", sequence=1, recall_mode="off"),
        _run(run_id="treatment-004", sequence=2, recall_mode="on"),
        expected_git_head=GIT_HEAD,
        expected_task_fingerprint=TASK_FINGERPRINT,
    ).evidence_id != first.evidence_id


@pytest.mark.parametrize("field, value", [
    ("gitHead", "a" * 12),
    ("gitHead", "codex/runner-capability-evidence"),
    ("provider", None),
    ("model", None),
    ("cacheReplayDetected", "false"),
])
def test_run_rejects_unsafe_identity_and_types(field: str, value: object):
    control = _run(run_id="control-001", sequence=1, recall_mode="off")
    control[field] = value

    with pytest.raises(RunnerCapabilityEvidenceError):
        build_capability_evidence(
            control, _run(run_id="treatment-002", sequence=2, recall_mode="on"),
            expected_git_head=GIT_HEAD, expected_task_fingerprint=TASK_FINGERPRINT,
        )


@pytest.mark.parametrize("field, value", [
    ("unexpected", "not allowed"),
    ("rawPrompt", "sensitive input"),
    ("rawModelOutput", "sensitive response"),
    ("runnerCommand", "runner --secret token"),
    ("runId", "x" * 257),
])
def test_run_rejects_unknown_raw_content_and_unbounded_values(field: str, value: str):
    control = _run(run_id="control-001", sequence=1, recall_mode="off")
    control[field] = value

    with pytest.raises(RunnerCapabilityEvidenceError):
        build_capability_evidence(
            control, _run(run_id="treatment-002", sequence=2, recall_mode="on"),
            expected_git_head=GIT_HEAD, expected_task_fingerprint=TASK_FINGERPRINT,
        )


@pytest.mark.parametrize("field, value", [
    ("gitHead", "0" * 40),
    ("projectId", "other-project"),
    ("workspaceKind", "repo"),
    ("workspaceFingerprint", "0" * 64),
    ("taskFingerprint", "0" * 64),
    ("briefFingerprint", "0" * 64),
    ("allowedFilesFingerprint", "0" * 64),
    ("commandsFingerprint", "0" * 64),
])
def test_evidence_rejects_top_level_identity_that_differs_from_runs(field: str, value: str):
    payload = _evidence().to_dict()
    payload[field] = value
    payload["evidenceId"] = canonical_fingerprint({key: item for key, item in payload.items() if key != "evidenceId"})

    with pytest.raises(RunnerCapabilityEvidenceError):
        RunnerCapabilityEvidence.from_dict(payload)


@pytest.mark.parametrize("field, value", [
    ("sameImmutableInputs", False),
    ("distinctRunIds", False),
    ("cacheReplayDetected", True),
    ("tokenReductionRatio", 0.25),
])
def test_evidence_rejects_tampered_comparison_claims(field: str, value: object):
    payload = _evidence().to_dict()
    payload["comparison"][field] = value
    payload["evidenceId"] = canonical_fingerprint({key: item for key, item in payload.items() if key != "evidenceId"})

    with pytest.raises(RunnerCapabilityEvidenceError):
        RunnerCapabilityEvidence.from_dict(payload)


def test_comparison_accepts_finite_negative_token_reduction_for_later_rejection_classification():
    comparison = RunnerCapabilityComparison(True, True, False, -0.1)

    assert comparison.token_reduction_ratio == -0.1


@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), float("-inf"), -10_000_001, 1.1, 10 ** 100])
def test_comparison_rejects_non_finite_or_out_of_bounds_token_reduction(ratio: float):
    with pytest.raises(RunnerCapabilityEvidenceError):
        RunnerCapabilityComparison(True, True, False, ratio)


def _runs(**treatment_overrides: object):
    control = _evidence().control
    treatment = replace(_evidence().treatment, **treatment_overrides)
    return control, treatment


def test_capability_comparison_marks_valid_off_on_protocol_ready():
    control, treatment = _runs(input_tokens=700)

    comparison = compare_capability_runs(control, treatment)

    assert comparison.result == "ready"
    assert comparison.reasons == ()
    assert comparison.token_reduction_ratio == 0.3


@pytest.mark.parametrize("treatment_overrides, expected_reason", [
    ({"git_head": "0" * 40}, "immutable_inputs_mismatch"),
    ({"task_fingerprint": "0" * 64}, "immutable_inputs_mismatch"),
    ({"sequence": 1}, "invalid_sequence"),
    ({"run_id": "control-001"}, "reused_run_id"),
    ({"cache_replay_detected": True}, "cache_replay_detected"),
    ({"status": "incomplete"}, "completion_missing"),
    ({"input_tokens": None}, "token_usage_missing"),
])
def test_capability_comparison_blocks_unprovable_capability(treatment_overrides: dict, expected_reason: str):
    control, treatment = _runs(**treatment_overrides)

    comparison = compare_capability_runs(control, treatment)

    assert comparison.result == "blocked_runner_capability"
    assert expected_reason in comparison.reasons


def test_capability_comparison_blocks_live_identity_mismatch():
    control, treatment = _runs()
    object.__setattr__(treatment, "model", "unverified-model")

    comparison = compare_capability_runs(control, treatment)

    assert comparison.result == "blocked_runner_capability"
    assert "live_identity_mismatch" in comparison.reasons


@pytest.mark.parametrize("treatment_overrides, expected_reason", [
    ({"input_tokens": 900}, "token_reduction_below_threshold"),
    ({"provenance_coverage": 0.9}, "provenance_coverage_below_full"),
    ({"sensitive_capture_count": 1}, "sensitive_capture_detected"),
    ({"p95_ms": 801}, "latency_exceeds_limit"),
])
def test_capability_comparison_rejects_proven_capability_with_failed_metrics(treatment_overrides: dict, expected_reason: str):
    control, treatment = _runs(**treatment_overrides)

    comparison = compare_capability_runs(control, treatment)

    assert comparison.result == "acceptance_rejected"
    assert expected_reason in comparison.reasons
