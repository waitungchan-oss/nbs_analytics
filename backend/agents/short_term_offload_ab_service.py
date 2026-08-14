from __future__ import annotations

from backend.agents.runner_capability_evidence import RunnerCapabilityRun
from .short_term_offload_ab_models import ShortTermOffloadABEvidence, ShortTermOffloadABEvidenceError


def _total_tokens(run: RunnerCapabilityRun) -> int | None:
    if run.input_tokens is None or run.output_tokens is None:
        return None
    total = run.input_tokens + run.output_tokens
    return total if total > 0 else None


def compare_short_term_offload_runs(
    control: RunnerCapabilityRun,
    treatment: RunnerCapabilityRun,
    *,
    workload_fingerprint: str,
    control_receipt_ref: str,
    treatment_receipt_ref: str,
    provenance_refs: tuple[str, ...],
) -> ShortTermOffloadABEvidence:
    if not isinstance(control, RunnerCapabilityRun) or not isinstance(treatment, RunnerCapabilityRun):
        raise ShortTermOffloadABEvidenceError("comparison requires typed runner runs")
    reasons: list[str] = []
    if control.recall_mode != "off" or treatment.recall_mode != "on":
        reasons.append("invalid_recall_mode")
    if control.sequence != 1 or treatment.sequence != 2:
        reasons.append("invalid_sequence")
    immutable = ("git_head", "project_id", "workspace_kind", "workspace_fingerprint", "task_fingerprint",
                 "brief_fingerprint", "allowed_files_fingerprint", "commands_fingerprint", "provider", "model",
                 "reasoning_profile", "clean_worktree_fingerprint")
    if any(getattr(control, field) != getattr(treatment, field) for field in immutable):
        reasons.append("immutable_inputs_mismatch")
    if control.run_id == treatment.run_id:
        reasons.append("reused_run_id")
    if control.status != "completed" or treatment.status != "completed":
        reasons.append("completion_missing")
    if control.cache_replay_detected or treatment.cache_replay_detected:
        reasons.append("cache_replay_detected")
    if any(getattr(run, flag) is not True for run in (control, treatment) for flag in
           ("writer_disabled", "baseline_unchanged", "formal_scope_unchanged", "review_no_regression", "hermes_no_regression")):
        reasons.append("safety_attestation_missing")
    control_total, treatment_total = _total_tokens(control), _total_tokens(treatment)
    if control_total is None or treatment_total is None:
        reasons.append("token_usage_missing")
        token_ratio = 0.0
    else:
        token_ratio = (control_total - treatment_total) / control_total
    if control.provenance_coverage != 1.0 or treatment.provenance_coverage != 1.0:
        reasons.append("provenance_coverage_below_full")
    if control.sensitive_capture_count or treatment.sensitive_capture_count:
        reasons.append("sensitive_capture_detected")
    if control.p95_ms <= 0 or treatment.p95_ms <= 0:
        reasons.append("latency_usage_missing")
    if reasons:
        result = "completion_missing" if "completion_missing" in reasons else "blocked_runner_capability"
    elif token_ratio <= 0:
        result = "no_reduction"
        reasons.append("token_reduction_not_observed")
    else:
        result = "pass"
    raw_latency_ratio = (treatment.p95_ms - control.p95_ms) / control.p95_ms if control.p95_ms else 0.0
    latency_ratio = max(-1.0, min(1.0, raw_latency_ratio))
    if raw_latency_ratio != latency_ratio:
        reasons.append("latency_delta_out_of_bounds")
        result = "blocked_runner_capability"
    return ShortTermOffloadABEvidence(
        control=control, treatment=treatment, workload_fingerprint=workload_fingerprint,
        control_receipt_ref=control_receipt_ref, treatment_receipt_ref=treatment_receipt_ref,
        provenance_refs=provenance_refs, token_reduction_ratio=token_ratio,
        latency_delta_ratio=latency_ratio, result=result, reasons=tuple(reasons),
    )
