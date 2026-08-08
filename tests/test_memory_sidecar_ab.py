from __future__ import annotations

import pytest

from backend.agents.memory_sidecar_ab import (
    AbCohort,
    AbInvariantMismatch,
    MemorySidecarAbRun,
    MemorySidecarAbReport,
    build_ab_report,
    p95_latency_ms,
)
from backend.agents.evidence_models import canonical_fingerprint


HEAD = "a" * 40
TASK_FINGERPRINT = canonical_fingerprint({"task": "memory-sidecar-task-3"})
BRIEF_FINGERPRINT = canonical_fingerprint({"brief": "brief-task-3"})
ALLOWED_FILES = (
    "backend/agents/memory_sidecar_ab.py",
    "backend/agents/memory_sidecar_telemetry.py",
    "tests/test_memory_sidecar_ab.py",
    "tests/test_memory_sidecar_telemetry.py",
)
COMMANDS = ("pytest tests/test_memory_sidecar_ab.py -q",)
_FP_64 = "c" * 64


def _latest_ab_dict() -> dict:
    return {
        "schemaVersion": "memory-sidecar-ab-run-v1",
        "runId": "run-ab-task3-off",
        "cohort": "recall_off",
        "head": HEAD,
        "taskFingerprint": TASK_FINGERPRINT,
        "briefFingerprint": BRIEF_FINGERPRINT,
        "allowedFiles": list(ALLOWED_FILES),
        "commands": list(COMMANDS),
        "provider": "hermes",
        "model": "deepseek-v4-flash",
        "estimatedInputTokens": 8000,
        "explorationCount": 4,
        "findings": 1,
        "sensitiveCaptureCount": 0,
        "fallback": False,
        "evidenceCoverage": 1.0,
        "latencyMs": 300,
        "reviewNoRegression": True,
        "baselineScopeUnchanged": True,
    }


def _off_run(**changes) -> MemorySidecarAbRun:
    values = {
        "run_id": "run-ab-task3-off",
        "cohort": "recall_off",
        "head": HEAD,
        "task_fingerprint": TASK_FINGERPRINT,
        "brief_fingerprint": BRIEF_FINGERPRINT,
        "allowed_files": ALLOWED_FILES,
        "commands": COMMANDS,
        "provider": "hermes",
        "model": "deepseek-v4-flash",
        "estimated_input_tokens": 8000,
        "exploration_count": 4,
        "findings": 1,
        "sensitive_capture_count": 0,
        "fallback": False,
        "evidence_coverage": 1.0,
        "latency_ms": 300,
        "review_no_regression": True,
        "baseline_scope_unchanged": True,
    }
    values.update(changes)
    return MemorySidecarAbRun(**values)


def _on_run(**changes) -> MemorySidecarAbRun:
    values = {
        "run_id": "run-ab-task3-on",
        "cohort": "recall_on",
        "head": HEAD,
        "task_fingerprint": TASK_FINGERPRINT,
        "brief_fingerprint": BRIEF_FINGERPRINT,
        "allowed_files": ALLOWED_FILES,
        "commands": COMMANDS,
        "provider": "hermes",
        "model": "deepseek-v4-flash",
        "estimated_input_tokens": 6000,
        "exploration_count": 3,
        "findings": 1,
        "sensitive_capture_count": 0,
        "fallback": False,
        "evidence_coverage": 1.0,
        "latency_ms": 320,
        "review_no_regression": True,
        "baseline_scope_unchanged": True,
    }
    values.update(changes)
    return MemorySidecarAbRun(**values)


# ---------------------------------------------------------------------------
# Immutable run record and cohort
# ---------------------------------------------------------------------------


def test_ab_cohort_only_allows_recall_off_or_recall_on():
    assert AbCohort.members() == ("recall_off", "recall_on")
    assert AbCohort("recall_off") == AbCohort.RECALL_OFF
    assert AbCohort("recall_on") == AbCohort.RECALL_ON
    with pytest.raises(ValueError, match="cohort"):
        AbCohort("shadow")


def test_ab_run_is_immutable_and_bounded():
    run = _off_run()
    with pytest.raises(Exception):
        run.estimated_input_tokens = 0
    assert run.cohort == AbCohort.RECALL_OFF
    assert run.head == HEAD
    assert run.task_fingerprint == TASK_FINGERPRINT
    assert run.brief_fingerprint == BRIEF_FINGERPRINT
    assert run.allowed_files == ALLOWED_FILES
    assert run.commands == COMMANDS


@pytest.mark.parametrize(
    ("field", "value"),
    [("allowedFiles", "abc.py"), ("commands", "pytest")],
)
def test_ab_run_from_dict_rejects_bare_string_collections(field, value):
    payload = _off_run().to_dict()
    payload[field] = value

    with pytest.raises(AbInvariantMismatch, match="schema"):
        MemorySidecarAbRun.from_dict(payload)


def test_ab_run_rejects_invalid_head_or_fingerprints():
    with pytest.raises(ValueError, match="head"):
        _off_run(head="short")
    with pytest.raises(ValueError, match="task"):
        _off_run(task_fingerprint="not-hex")
    with pytest.raises(ValueError, match="brief"):
        _off_run(brief_fingerprint="not-hex")


def test_ab_run_rejects_out_of_range_counts():
    for field, value in (
        ("estimated_input_tokens", -1),
        ("exploration_count", -1),
        ("findings", -1),
        ("sensitive_capture_count", -1),
        ("sensitive_capture_count", 1),
    ):
        with pytest.raises(ValueError, match="out of range"):
            _off_run(**{field: value})
    with pytest.raises(ValueError, match="out of range"):
        _off_run(evidence_coverage=-0.01)
    with pytest.raises(ValueError, match="out of range"):
        _off_run(evidence_coverage=1.01)


def test_ab_run_rejects_unbounded_metric_values_over_finite_caps():
    # Every numeric run metric must have an explicit finite upper bound so
    # diagnostic A/B metrics can never be unbounded.
    boundary_plus_one = {
        "estimated_input_tokens": 1_000_001,
        "exploration_count": 10_001,
        "findings": 1_001,
        "sensitive_capture_count": 101,
        "latency_ms": 10_001,
    }
    for field, value in boundary_plus_one.items():
        with pytest.raises(ValueError, match="out of range"):
            _off_run(**{field: value})


def test_ab_run_accepts_boundary_metric_values():
    # The finite boundary itself must remain constructible.
    _off_run(estimated_input_tokens=1_000_000)
    _off_run(exploration_count=10_000)
    _off_run(findings=1_000)
    _off_run(latency_ms=10_000)


def test_ab_run_keeps_latency_over_800_constructible_for_gate_to_reject():
    # Latency must stay constructible above the 800ms gate so the *acceptance
    # gate* (not model construction) is the authoritative enforcer.
    on = _on_run(latency_ms=999)
    assert on.latency_ms == 999
    report = build_ab_report(_off_run(), on)
    assert report.decision == "acceptance_rejected"
    assert report.p95_latency_ms == 999


def test_ab_run_requires_full_evidence_and_clean_review_for_off_cohort():
    # The recall-off baseline is the canonical control: it must have full
    # evidence coverage and no review regression to be a valid A/B comparator.
    _off_run()  # defaults satisfy the invariant
    with pytest.raises(AbInvariantMismatch, match="coverage"):
        _off_run(evidence_coverage=0.5)
    with pytest.raises(AbInvariantMismatch, match="regression"):
        _off_run(review_no_regression=False)
    with pytest.raises(AbInvariantMismatch, match="baseline"):
        _off_run(baseline_scope_unchanged=False)


def test_ab_run_rejects_sensitive_capture_in_recall_on_cohort():
    # recall-on may carry captured sensitive paths only as a rejected/blanked
    # status; a real sensitive capture forces an explicit rejection.
    with pytest.raises(AbInvariantMismatch, match="sensitive"):
        _on_run(sensitive_capture_count=1)


def test_ab_report_serializes_without_secrets_or_full_prompt():
    report = build_ab_report(_off_run(), _on_run())
    payload = report.to_dict()
    serialized = str(payload)
    assert payload["schemaVersion"] == "memory-sidecar-ab-report-v1"
    assert "secret" not in serialized.lower()
    assert "query" not in {key.lower() for key in payload if "fingerprint" not in key.lower()}
    # No full prompt text anywhere: only hashes and bounded metrics.
    assert "full prompt" not in serialized.lower()


def test_ab_report_sanitizes_run_id_to_safe_slug():
    report = build_ab_report(_off_run(run_id="run-ab@off"), _on_run())
    assert report.ab_report_id  # no exception


# ---------------------------------------------------------------------------
# Shared inputs invariant: HEAD, task, brief, allowed files, commands
# ---------------------------------------------------------------------------


def test_ab_report_requires_shared_head_task_brief_files_commands():
    # All shared fields identical: valid pair.
    build_ab_report(_off_run(), _on_run())

    cases = {
        "head": "b" * 40,
        "task_fingerprint": canonical_fingerprint({"task": "other"}),
        "brief_fingerprint": canonical_fingerprint({"brief": "other"}),
        "allowed_files": ("different/file.py",),
        "commands": ("pytest other.py -q",),
    }
    for field, value in cases.items():
        with pytest.raises(AbInvariantMismatch, match="shared"):
            build_ab_report(_off_run(**{field: value}), _on_run())


def test_ab_report_pairs_one_off_and_one_on_cohort():
    with pytest.raises(AbInvariantMismatch, match="cohort"):
        build_ab_report(_off_run(), _off_run(run_id="run-ab-task3-off-2"))
    with pytest.raises(AbInvariantMismatch, match="cohort"):
        build_ab_report(_on_run(), _on_run(run_id="run-ab-task3-on-2"))


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "openai"), ("model", "gpt-4o")],
)
def test_ab_run_rejects_unapproved_provider_model_identity(field, value):
    with pytest.raises(AbInvariantMismatch, match="allowlisted"):
        _off_run(**{field: value})


# ---------------------------------------------------------------------------
# Metrics: token delta, p95 latency, evidence coverage
# ---------------------------------------------------------------------------


def test_p95_latency_ms_computes_nearest_rank_estimate():
    assert p95_latency_ms([10, 20, 30, 40, 800]) == 800
    assert p95_latency_ms([100]) == 100
    assert p95_latency_ms([]) == 0


def test_report_calculates_bounded_token_delta_and_reduction():
    off = _off_run(estimated_input_tokens=10000)
    on = _on_run(estimated_input_tokens=7000)
    report = build_ab_report(off, on)
    assert report.token_delta == -3000
    assert report.input_reduction_fraction == pytest.approx(0.3)


def test_report_requires_explicit_alternative_evidence_flag_when_under_reduction_threshold():
    # recall-on at 90% input (10% reduction) with no alternative evidence must
    # be rejected even if every other gate passes.
    on = _on_run(estimated_input_tokens=9000, evidence_coverage=1.0, review_no_regression=True)
    report = build_ab_report(_off_run(), on)
    assert report.decision == "acceptance_rejected"


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------


def test_gate_accepts_when_strong_input_reduction_and_all_gates_pass():
    report = build_ab_report(_off_run(estimated_input_tokens=10000), _on_run(estimated_input_tokens=7000))
    assert report.decision == "accepted"


def test_gate_accepts_when_alternative_evidence_present_even_under_reduction_threshold():
    # Input reduction below the 20% threshold but explicit alternative evidence
    # is present: the gate must accept on the alternative-evidence branch.
    on = _on_run(
        estimated_input_tokens=9200,  # 8% reduction: under the 20% threshold
        alternative_evidence=True,
        evidence_coverage=1.0,
        review_no_regression=True,
        baseline_scope_unchanged=True,
        sensitive_capture_count=0,
        latency_ms=320,
        findings=1,
    )
    report = build_ab_report(_off_run(), on)
    assert report.decision == "accepted"


def test_gate_rejects_when_coverage_below_100():
    on = _on_run(evidence_coverage=0.99)
    report = build_ab_report(_off_run(), on)
    assert report.decision == "acceptance_rejected"
    assert "coverage" in " ".join(report.failed_reasons).lower()


def test_gate_rejects_when_p95_latency_over_800ms():
    on = _on_run(latency_ms=801)
    report = build_ab_report(_off_run(), on)
    assert report.decision == "acceptance_rejected"
    assert "p95" in " ".join(report.failed_reasons).lower()


def test_gate_rejects_when_review_or_hermes_regression():
    unregressed = _on_run(review_no_regression=False)
    report = build_ab_report(_off_run(), unregressed)
    assert report.decision == "acceptance_rejected"
    assert "regression" in " ".join(report.failed_reasons).lower()


def test_gate_rejects_when_sensitive_capture_in_recall_on():
    with pytest.raises(AbInvariantMismatch, match="sensitive"):
        _on_run(sensitive_capture_count=1)


def test_gate_rejects_when_baseline_or_formal_scope_changed():
    changed = _on_run(baseline_scope_unchanged=False)
    report = build_ab_report(_off_run(), changed)
    assert report.decision == "acceptance_rejected"
    assert "baseline" in " ".join(report.failed_reasons).lower()


def test_gate_rejection_must_not_auto_enable_recall():
    report = build_ab_report(_off_run(), _on_run(estimated_input_tokens=9000))
    assert report.decision == "acceptance_rejected"
    assert report.recall_auto_enabled is False


def test_accepted_report_still_does_not_auto_enable_recall():
    report = build_ab_report(_off_run(), _on_run())
    assert report.decision == "accepted"
    # Acceptance is evidence only; it never flips the pilot feature flag.
    assert report.recall_auto_enabled is False


def test_ab_report_deterministic_on_same_inputs():
    off, on = _off_run(), _on_run()
    first = build_ab_report(off, on)
    second = build_ab_report(_off_run(), _on_run())
    assert first.to_dict() == second.to_dict()
    assert first.to_dict()["abReportId"] == second.to_dict()["abReportId"]


# ---------------------------------------------------------------------------
# Scoped re-review Finding 1: bounded safe-string validation for every element
# of allowed_files/commands and for provider/model.
# ---------------------------------------------------------------------------


def test_ab_run_rejects_plain_string_iterable_for_allowed_files_or_commands():
    # Iterating a bare string yields characters; it must not be accepted as a
    # tuple of file paths or commands.
    with pytest.raises(ValueError, match="allowed files"):
        _off_run(allowed_files="backend/agents/x.py")
    with pytest.raises(ValueError, match="commands"):
        _off_run(commands="pytest x.py -q")


def test_ab_run_rejects_oversized_allowed_file_element():
    with pytest.raises(ValueError, match="allowed files"):
        _off_run(allowed_files=("x" * 513,) + ALLOWED_FILES[1:])


def test_ab_run_rejects_unsafe_allowed_file_content():
    unsafe = (
        "/absolute/path.py",
        "../escaped/context.json",
        "creds/.env",
        "config with\ttab.py",
        "line\nbreak.py",
    )
    for bad in unsafe:
        with pytest.raises(ValueError, match="allowed files"):
            _off_run(allowed_files=(bad,))


def test_ab_run_rejects_secret_prompt_style_command():
    bad_commands = (
        "cat -----BEGIN RSA PRIVATE KEY-----",
        "exec sk-xxxxxxxxxxxxxxxxxxxxxxxx\ncat /etc/passwd",
    )
    for bad in bad_commands:
        with pytest.raises(ValueError, match="commands"):
            _off_run(commands=(bad,))


def test_ab_run_rejects_oversized_or_unsafe_provider_model():
    with pytest.raises(ValueError, match="provider"):
        _off_run(provider="h" * 129)
    with pytest.raises(ValueError, match="model"):
        _off_run(model="m" * 129)
    with pytest.raises(ValueError, match="provider"):
        _off_run(provider="openai\nwith-secret")
    with pytest.raises(ValueError, match="model"):
        _off_run(model="/abs/model")


def test_ab_run_accepts_valid_allowed_files_commands_provider_model():
    _off_run()
    _off_run(allowed_files=("a" * 512,) + ALLOWED_FILES[1:])
    _off_run(commands=("pytest tests -q",))
    _off_run(provider="hermes", model="deepseek-v4-flash")


# ---------------------------------------------------------------------------
# Scoped re-review Finding 2: ab_report_id fingerprint must include the
# acceptance-driving metrics so materially different gate outcomes differ.
# ---------------------------------------------------------------------------


def test_ab_report_id_differs_when_gate_outcome_differs():
    accepted = build_ab_report(_off_run(), _on_run())
    assert accepted.decision == "accepted"
    # Under the 20% reduction threshold with no alternative evidence -> reject.
    rejected = build_ab_report(_off_run(), _on_run(estimated_input_tokens=9000))
    assert rejected.decision == "acceptance_rejected"
    assert accepted.ab_report_id != rejected.ab_report_id


def test_ab_report_id_differs_on_coverage_failure():
    ok = build_ab_report(_off_run(), _on_run())
    bad = build_ab_report(_off_run(), _on_run(evidence_coverage=0.99))
    assert bad.decision == "acceptance_rejected"
    assert ok.ab_report_id != bad.ab_report_id


def test_ab_report_id_differs_on_p95_latency_failure():
    ok = build_ab_report(_off_run(), _on_run())
    bad = build_ab_report(_off_run(), _on_run(latency_ms=999))
    assert bad.decision == "acceptance_rejected"
    assert ok.ab_report_id != bad.ab_report_id


def test_ab_report_id_differs_on_review_no_regression_failure():
    ok = build_ab_report(_off_run(), _on_run())
    bad = build_ab_report(_off_run(), _on_run(review_no_regression=False))
    assert bad.decision == "acceptance_rejected"
    assert ok.ab_report_id != bad.ab_report_id


def test_ab_report_id_differs_on_alternative_evidence_flag():
    # Same accepted-reduction run but alternative_evidence toggled must change
    # the report identity even when the decision happens to be identical.
    with_alt = build_ab_report(_off_run(), _on_run(estimated_input_tokens=9200, alternative_evidence=True))
    without_alt = build_ab_report(_off_run(), _on_run(estimated_input_tokens=9200))
    assert with_alt.decision == "accepted"
    assert without_alt.decision == "acceptance_rejected"
    assert with_alt.ab_report_id != without_alt.ab_report_id
