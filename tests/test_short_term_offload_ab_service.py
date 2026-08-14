from dataclasses import replace

from backend.agents.short_term_offload_ab_service import compare_short_term_offload_runs
from tests.test_short_term_offload_ab_models import _run


def test_comparison_uses_total_observed_tokens_and_latency_delta():
    evidence = compare_short_term_offload_runs(
        _run("off", 1, "control-4", input_tokens=900, output_tokens=100),
        _run("on", 2, "treatment-4", input_tokens=450, output_tokens=50),
        workload_fingerprint="8" * 64, control_receipt_ref="c4", treatment_receipt_ref="t4",
        provenance_refs=("artifact/brief.md", "artifact/plan.md"),
    )
    assert evidence.token_reduction_ratio == 0.5
    assert evidence.latency_delta_ratio == 0.0
    assert evidence.result == "pass"


def test_mismatched_immutable_workload_is_blocked():
    control = _run("off", 1, "control-5")
    treatment = _run("on", 2, "treatment-5")
    treatment = replace(treatment, git_head="b" * 40)
    evidence = compare_short_term_offload_runs(
        control, treatment, workload_fingerprint="8" * 64, control_receipt_ref="c5", treatment_receipt_ref="t5",
        provenance_refs=("artifact/brief.md",),
    )
    assert evidence.result == "blocked_runner_capability"
    assert "immutable_inputs_mismatch" in evidence.reasons


def test_latency_delta_is_bounded_and_fail_closed():
    control = replace(_run("off", 1, "control-6"), p95_ms=100)
    treatment = replace(_run("on", 2, "treatment-6"), p95_ms=1000)
    evidence = compare_short_term_offload_runs(
        control, treatment, workload_fingerprint="8" * 64, control_receipt_ref="c6", treatment_receipt_ref="t6",
        provenance_refs=("artifact/brief.md",),
    )
    assert evidence.latency_delta_ratio == 1.0
    assert evidence.result == "blocked_runner_capability"
    assert "latency_delta_out_of_bounds" in evidence.reasons


def test_zero_control_latency_is_missing_and_blocks_claim():
    control = replace(_run("off", 1, "control-7"), p95_ms=0)
    treatment = replace(_run("on", 2, "treatment-7", input_tokens=400, output_tokens=100), p95_ms=100)
    evidence = compare_short_term_offload_runs(
        control, treatment, workload_fingerprint="8" * 64, control_receipt_ref="c7", treatment_receipt_ref="t7",
        provenance_refs=("artifact/brief.md",),
    )
    assert evidence.result == "blocked_runner_capability"
    assert "latency_usage_missing" in evidence.reasons


def test_negative_reduction_is_serializable_no_reduction():
    evidence = compare_short_term_offload_runs(
        _run("off", 1, "control-8", input_tokens=1, output_tokens=0),
        _run("on", 2, "treatment-8", input_tokens=4, output_tokens=0),
        workload_fingerprint="8" * 64, control_receipt_ref="c8", treatment_receipt_ref="t8",
        provenance_refs=("artifact/brief.md",),
    )
    assert evidence.result == "no_reduction"
    assert evidence.token_reduction_ratio == -3.0
