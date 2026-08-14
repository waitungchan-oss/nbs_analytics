import pytest

from backend.agents.runner_capability_evidence import RunnerCapabilityRun
from backend.agents.short_term_offload_ab_models import ShortTermOffloadABEvidence, ShortTermOffloadABEvidenceError
from backend.agents.short_term_offload_ab_service import compare_short_term_offload_runs


def _run(mode: str, sequence: int, run_id: str, *, input_tokens=800, output_tokens=200, status="completed"):
    return RunnerCapabilityRun(
        run_id=run_id, sequence=sequence, recall_mode=mode,
        git_head="a" * 40, project_id="nbs_analytics", workspace_kind="repo",
        workspace_fingerprint="1" * 64, task_fingerprint="2" * 64, brief_fingerprint="3" * 64,
        allowed_files_fingerprint="4" * 64, commands_fingerprint="5" * 64, provider="hermes",
        model="deepseek-v4-flash", reasoning_profile="max", clean_worktree_fingerprint="6" * 64,
        status=status, cache_replay_detected=False, input_tokens=input_tokens, output_tokens=output_tokens,
        p95_ms=100, provenance_coverage=1.0, sensitive_capture_count=0, writer_disabled=True,
        baseline_unchanged=True, formal_scope_unchanged=True, review_no_regression=True, hermes_no_regression=True,
    )


def _evidence():
    return compare_short_term_offload_runs(
        _run("off", 1, "control-1"), _run("on", 2, "treatment-1", input_tokens=500, output_tokens=100),
        workload_fingerprint="7" * 64, control_receipt_ref="receipts/control.json",
        treatment_receipt_ref="receipts/treatment.json", provenance_refs=("artifact/spec.md",),
    )


def test_evidence_round_trips_with_exact_schema_and_fingerprint():
    evidence = _evidence()
    assert evidence.result == "pass"
    payload = evidence.to_dict()
    assert ShortTermOffloadABEvidence.from_dict(payload) == evidence
    assert set(payload) == {"schemaVersion", "workloadFingerprint", "control", "treatment", "controlReceiptRef", "treatmentReceiptRef", "provenanceRefs", "tokenReductionRatio", "latencyDeltaRatio", "result", "reasons", "evidenceFingerprint"}


def test_tampered_evidence_is_rejected():
    payload = _evidence().to_dict()
    payload["tokenReductionRatio"] = 0.99
    with pytest.raises(ShortTermOffloadABEvidenceError):
        ShortTermOffloadABEvidence.from_dict(payload)


def test_missing_usage_is_fail_closed_without_claiming_reduction():
    evidence = compare_short_term_offload_runs(
        _run("off", 1, "control-2", input_tokens=None), _run("on", 2, "treatment-2", input_tokens=None),
        workload_fingerprint="7" * 64, control_receipt_ref="c", treatment_receipt_ref="t",
        provenance_refs=("artifact/spec.md",),
    )
    assert evidence.result == "blocked_runner_capability"
    assert evidence.token_reduction_ratio == 0.0
    assert "token_usage_missing" in evidence.reasons


def test_incomplete_runs_are_completion_missing():
    evidence = compare_short_term_offload_runs(
        _run("off", 1, "control-3", status="incomplete"), _run("on", 2, "treatment-3"),
        workload_fingerprint="7" * 64, control_receipt_ref="c3", treatment_receipt_ref="t3",
        provenance_refs=("artifact/spec.md",),
    )
    assert evidence.result == "completion_missing"


def test_payload_requires_strict_numeric_latency_and_reasons_list():
    payload = _evidence().to_dict()
    payload["latencyDeltaRatio"] = "0.1"
    with pytest.raises(ShortTermOffloadABEvidenceError):
        ShortTermOffloadABEvidence.from_dict(payload)
    payload = _evidence().to_dict()
    payload["reasons"] = "tampered"
    with pytest.raises(ShortTermOffloadABEvidenceError):
        ShortTermOffloadABEvidence.from_dict(payload)
