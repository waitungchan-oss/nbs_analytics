from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .evidence_models import canonical_fingerprint
from .memory_sidecar_models import MODEL_ALLOWLIST, PROVIDER_ALLOWLIST

AB_RUN_SCHEMA = "memory-sidecar-ab-run-v1"
AB_REPORT_SCHEMA = "memory-sidecar-ab-report-v1"
AB_INPUT_REDUCTION_THRESHOLD = 0.20
AB_P95_LATENCY_CAP_MS = 800
AB_EVIDENCE_COVERAGE_REQUIRED = 1.0

# Explicit finite upper bounds for every numeric diagnostic metric so A/B
# reports can never carry unbounded values.
AB_MAX_INPUT_TOKENS = 1_000_000
AB_MAX_EXPLORATION_COUNT = 10_000
AB_MAX_FINDINGS = 1_000
AB_MAX_SENSITIVE_CAPTURE_COUNT = 100
# The latency construction cap is deliberately above AB_P95_LATENCY_CAP_MS so
# the acceptance gate (not model construction) is the authoritative enforcer.
AB_MAX_LATENCY_MS = 10_000

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COHORT_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,160}$")
_ABS_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# Secret / full-prompt markers never belong in a bounded short label or command.
_FORBIDDEN_STRING_MARKERS = ("-----begin", "sk-", ".env", " api_key", "api_key=", "password", "bearer ")
AB_MAX_ELEMENT_CHARS = 512
AB_MAX_IDENTITY_CHARS = 128


class AbInvariantMismatch(ValueError):
    """Raised when the immutable A/B inputs violate a canonical invariant."""


@dataclass(frozen=True)
class AbCohort:
    """The only two cohorts a controlled A/B run may belong to."""

    _value: str = "recall_off"

    RECALL_OFF = "recall_off"
    RECALL_ON = "recall_on"

    def __init__(self, value: str = "recall_off") -> None:
        if not isinstance(value, str) or not _COHORT_RE.fullmatch(value):
            raise ValueError("ab cohort is invalid")
        normalized = value if value in (self.RECALL_OFF, self.RECALL_ON) else None
        if normalized is None:
            raise ValueError("ab cohort must be recall_off or recall_on")
        object.__setattr__(self, "_value", normalized)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self._value == other
        if isinstance(other, AbCohort):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    @property
    def value(self) -> str:
        return self._value

    @classmethod
    def members(cls) -> tuple[str, ...]:
        return (cls.RECALL_OFF, cls.RECALL_ON)


def _positive_int(value: int, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"memory sidecar A/B {name} is out of range")
    return value


def _fraction(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"memory sidecar A/B {name} is out of range")
    return float(value)


def _safe_string_element(value: str, *, name: str, max_chars: int) -> str:
    """Validate one bounded safe-string element of a controlled A/B run.

    Rejects non-strings, empty/oversized values, plain string iterables, and
    unsafe content (path traversal, absolute or URL-scheme paths, control or
    newline characters, and secret/full-prompt markers). A valid element is a
    short, safe, single-line ASCII label or path.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"memory sidecar A/B {name} element is invalid")
    if not 0 < len(value) <= max_chars:
        raise ValueError(f"memory sidecar A/B {name} element is unbounded")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"memory sidecar A/B {name} element contains unsafe control characters")
    if _URL_SCHEME_RE.match(value) or value.startswith("/") or "\\" in value or ".." in value or _ABS_WINDOWS_PATH_RE.match(value):
        raise ValueError(f"memory sidecar A/B {name} element is an unsafe path")
    lower = value.lower()
    if any(marker in lower for marker in _FORBIDDEN_STRING_MARKERS):
        raise ValueError(f"memory sidecar A/B {name} element carries forbidden secret/prompt content")
    return value


def _safe_element_iterable(values: object, *, name: str, max_chars: int, max_items: int) -> tuple[str, ...]:
    """Validate a bounded tuple of safe strings, rejecting bare string iterables."""
    if isinstance(values, str) or not isinstance(values, (tuple, list)):
        raise ValueError(f"memory sidecar A/B {name} must be a non-empty tuple of strings")
    if not values or len(values) > max_items:
        raise ValueError(f"memory sidecar A/B {name} are invalid or unbounded")
    normalized = tuple(_safe_string_element(item, name=name, max_chars=max_chars) for item in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"memory sidecar A/B {name} must be unique")
    return normalized


def _alternative_evidence_binding(*, head: str, task_fingerprint: str, brief_fingerprint: str, allowed_files: tuple[str, ...], commands: tuple[str, ...], provider: str, model: str) -> str:
    return canonical_fingerprint({
        "schemaVersion": AB_RUN_SCHEMA,
        "head": head,
        "taskFingerprint": task_fingerprint,
        "briefFingerprint": brief_fingerprint,
        "allowedFiles": list(allowed_files),
        "commands": list(commands),
        "provider": provider,
        "model": model,
    })


@dataclass(frozen=True)
class MemorySidecarAbRun:
    """Immutable record of one controlled A/B execution.

    Every run binds the shared HEAD, task fingerprint, brief fingerprint,
    allowed files and commands; the cohort is exclusively ``recall_off`` or
    ``recall_on``. No secrets or full prompts are stored here, only bounded
    metric counts, flags and hashes.
    """

    run_id: str
    cohort: AbCohort
    head: str
    task_fingerprint: str
    brief_fingerprint: str
    allowed_files: tuple[str, ...]
    commands: tuple[str, ...]
    provider: str
    model: str
    estimated_input_tokens: int
    exploration_count: int
    findings: int
    sensitive_capture_count: int
    fallback: bool
    evidence_coverage: float
    latency_ms: int
    review_no_regression: bool
    baseline_scope_unchanged: bool
    alternative_evidence: bool = False
    alternative_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not _RUN_ID_RE.fullmatch(self.run_id):
            raise AbInvariantMismatch("run id is not a safe identifier")
        object.__setattr__(self, "cohort", self.cohort if isinstance(self.cohort, AbCohort) else AbCohort(self.cohort))
        if not isinstance(self.head, str) or not _SHA40_RE.fullmatch(self.head):
            raise ValueError("memory sidecar A/B head is invalid")
        if not isinstance(self.task_fingerprint, str) or not _SHA256_RE.fullmatch(self.task_fingerprint):
            raise ValueError("memory sidecar A/B task fingerprint is invalid")
        if not isinstance(self.brief_fingerprint, str) or not _SHA256_RE.fullmatch(self.brief_fingerprint):
            raise ValueError("memory sidecar A/B brief fingerprint is invalid")
        object.__setattr__(self, "allowed_files", _safe_element_iterable(self.allowed_files, name="allowed files", max_chars=AB_MAX_ELEMENT_CHARS, max_items=64))
        object.__setattr__(self, "commands", _safe_element_iterable(self.commands, name="commands", max_chars=AB_MAX_ELEMENT_CHARS, max_items=64))
        if not isinstance(self.provider, str) or not self.provider:
            raise ValueError("memory sidecar A/B provider is invalid")
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("memory sidecar A/B model is invalid")
        _safe_string_element(self.provider, name="provider", max_chars=AB_MAX_IDENTITY_CHARS)
        _safe_string_element(self.model, name="model", max_chars=AB_MAX_IDENTITY_CHARS)
        if self.provider not in PROVIDER_ALLOWLIST:
            raise AbInvariantMismatch("provider_identity", "A/B provider is not allowlisted")
        if self.model not in MODEL_ALLOWLIST:
            raise AbInvariantMismatch("model_identity", "A/B model is not allowlisted")
        if not isinstance(self.fallback, bool) or not isinstance(self.review_no_regression, bool) or not isinstance(self.baseline_scope_unchanged, bool) or not isinstance(self.alternative_evidence, bool):
            raise ValueError("memory sidecar A/B boolean flags must be booleans")
        object.__setattr__(self, "estimated_input_tokens", _positive_int(self.estimated_input_tokens, name="input tokens", maximum=AB_MAX_INPUT_TOKENS))
        object.__setattr__(self, "exploration_count", _positive_int(self.exploration_count, name="exploration count", maximum=AB_MAX_EXPLORATION_COUNT))
        object.__setattr__(self, "findings", _positive_int(self.findings, name="findings", maximum=AB_MAX_FINDINGS))
        object.__setattr__(self, "sensitive_capture_count", _positive_int(self.sensitive_capture_count, name="sensitive capture count", maximum=AB_MAX_SENSITIVE_CAPTURE_COUNT))
        object.__setattr__(self, "evidence_coverage", _fraction(self.evidence_coverage, name="evidence coverage"))
        object.__setattr__(self, "latency_ms", _positive_int(self.latency_ms, name="latency", maximum=AB_MAX_LATENCY_MS))
        expected_ref = _alternative_evidence_binding(
            head=self.head, task_fingerprint=self.task_fingerprint, brief_fingerprint=self.brief_fingerprint,
            allowed_files=self.allowed_files, commands=self.commands, provider=self.provider, model=self.model,
        )
        if self.alternative_evidence:
            if not isinstance(self.alternative_evidence_ref, str) or self.alternative_evidence_ref != expected_ref:
                raise AbInvariantMismatch("alternative_evidence_ref", "alternative evidence requires the immutable binding reference")
        elif self.alternative_evidence_ref is not None:
            raise AbInvariantMismatch("alternative_evidence_ref", "alternative evidence reference requires the flag")

        if self.cohort == AbCohort.RECALL_OFF:
            # The recall-off control must be a pristine canonical baseline: no
            # sensitive capture (a hard data-integrity error), full evidence
            # coverage, no review regression and an unchanged baseline scope.
            if self.sensitive_capture_count != 0:
                raise ValueError("memory sidecar A/B sensitive capture count is out of range for the control cohort")
            if self.evidence_coverage < AB_EVIDENCE_COVERAGE_REQUIRED:
                raise AbInvariantMismatch("coverage_full_required", "recall-off control must have full evidence coverage")
            if not self.review_no_regression:
                raise AbInvariantMismatch("review_no_regression_required", "recall-off control must have no review regression")
            if not self.baseline_scope_unchanged:
                raise AbInvariantMismatch("baseline_scope_unchanged_required", "recall-off control must leave the baseline scope unchanged")
        elif self.cohort == AbCohort.RECALL_ON:
            # The recall-on intervention must never produce sensitive capture.
            if self.sensitive_capture_count != 0:
                raise AbInvariantMismatch("sensitive_capture", "recall-on must not capture sensitive data")
        else:  # pragma: no cover - guarded by AbCohort construction
            raise AbInvariantMismatch("cohort", "cohort is invalid")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemorySidecarAbRun":
        expected = {
            "schemaVersion", "runId", "cohort", "head", "taskFingerprint", "briefFingerprint",
            "allowedFiles", "commands", "provider", "model", "estimatedInputTokens",
            "explorationCount", "findings", "sensitiveCaptureCount", "fallback",
            "evidenceCoverage", "latencyMs", "reviewNoRegression", "baselineScopeUnchanged",
            "alternativeEvidence", "alternativeEvidenceRef",
        }
        if not isinstance(payload, dict) or set(payload) != expected or payload.get("schemaVersion") != AB_RUN_SCHEMA:
            raise AbInvariantMismatch("schema", "A/B run envelope is invalid")
        if any(not isinstance(payload[field], list) for field in ("allowedFiles", "commands")):
            raise AbInvariantMismatch("schema", "A/B run collection fields must be lists")
        return cls(
            run_id=payload["runId"],
            cohort=AbCohort(payload["cohort"]),
            head=payload["head"],
            task_fingerprint=payload["taskFingerprint"],
            brief_fingerprint=payload["briefFingerprint"],
            allowed_files=tuple(payload["allowedFiles"]),
            commands=tuple(payload["commands"]),
            provider=payload["provider"],
            model=payload["model"],
            estimated_input_tokens=payload["estimatedInputTokens"],
            exploration_count=payload["explorationCount"],
            findings=payload["findings"],
            sensitive_capture_count=payload["sensitiveCaptureCount"],
            fallback=payload["fallback"],
            evidence_coverage=payload["evidenceCoverage"],
            latency_ms=payload["latencyMs"],
            review_no_regression=payload["reviewNoRegression"],
            baseline_scope_unchanged=payload["baselineScopeUnchanged"],
            alternative_evidence=payload["alternativeEvidence"],
            alternative_evidence_ref=payload["alternativeEvidenceRef"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": AB_RUN_SCHEMA,
            "runId": self.run_id,
            "cohort": self.cohort.value,
            "head": self.head,
            "taskFingerprint": self.task_fingerprint,
            "briefFingerprint": self.brief_fingerprint,
            "allowedFiles": list(self.allowed_files),
            "commands": list(self.commands),
            "provider": self.provider,
            "model": self.model,
            "estimatedInputTokens": self.estimated_input_tokens,
            "explorationCount": self.exploration_count,
            "findings": self.findings,
            "sensitiveCaptureCount": self.sensitive_capture_count,
            "fallback": self.fallback,
            "evidenceCoverage": self.evidence_coverage,
            "latencyMs": self.latency_ms,
            "reviewNoRegression": self.review_no_regression,
            "baselineScopeUnchanged": self.baseline_scope_unchanged,
            "alternativeEvidence": self.alternative_evidence,
            "alternativeEvidenceRef": self.alternative_evidence_ref,
        }


def alternative_evidence_binding_fingerprint(run: MemorySidecarAbRun) -> str:
    if not isinstance(run, MemorySidecarAbRun):
        raise AbInvariantMismatch("run", "alternative evidence binding requires a valid A/B run")
    return _alternative_evidence_binding(
        head=run.head, task_fingerprint=run.task_fingerprint, brief_fingerprint=run.brief_fingerprint,
        allowed_files=run.allowed_files, commands=run.commands, provider=run.provider, model=run.model,
    )


def p95_latency_ms(latencies: Iterable[int]) -> int:
    """Nearest-rank p95 estimate over an iterable of latency samples (ms)."""
    values = sorted(int(value) for value in latencies)
    if not values:
        return 0
    return values[(len(values) * 95 + 99) // 100 - 1]


@dataclass(frozen=True)
class MemorySidecarAbReport:
    """Immutable A/B acceptance report.

    Computes bounded token delta, p95 latency and evidence coverage from the
    two cohort runs, runs the acceptance gate and exposes a fail-closed
    decision. Acceptance is diagnostic evidence only; it never flips the pilot
    feature flag (``recall_auto_enabled`` is always False).
    """

    ab_report_id: str
    off_run: MemorySidecarAbRun
    on_run: MemorySidecarAbRun
    token_delta: int
    input_reduction_fraction: float
    p95_latency_ms: int
    evidence_coverage: float
    decision: str
    failed_reasons: tuple[str, ...]
    recall_auto_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": AB_REPORT_SCHEMA,
            "abReportId": self.ab_report_id,
            "offRun": self.off_run.to_dict(),
            "onRun": self.on_run.to_dict(),
            "tokenDelta": self.token_delta,
            "inputReductionFraction": round(self.input_reduction_fraction, 6),
            "p95LatencyMs": self.p95_latency_ms,
            "evidenceCoverage": self.evidence_coverage,
            "decision": self.decision,
            "failedReasons": list(self.failed_reasons),
            "recallAutoEnabled": self.recall_auto_enabled,
        }


def build_ab_report(off_run: MemorySidecarAbRun, on_run: MemorySidecarAbRun) -> MemorySidecarAbReport:
    """Build the A/B acceptance report from the recall-off and recall-on runs.

    Enforces the shared-inputs invariants (HEAD, task fingerprint, brief,
    allowed files and commands) and the provider/model identity, then computes
    the metrics and gate. Any mismatch or failure fails closed to
    ``acceptance_rejected`` and never auto-enables recall.
    """
    if not isinstance(off_run, MemorySidecarAbRun) or not isinstance(on_run, MemorySidecarAbRun):
        raise AbInvariantMismatch("run", "A/B report requires two valid cohort runs")
    if off_run.cohort != AbCohort.RECALL_OFF or on_run.cohort != AbCohort.RECALL_ON:
        raise AbInvariantMismatch("cohort", "A/B report requires one recall_off and one recall_on run")

    for field, label in (
        ("head", "head"),
        ("task_fingerprint", "task fingerprint"),
        ("brief_fingerprint", "brief fingerprint"),
        ("allowed_files", "allowed files"),
        ("commands", "commands"),
    ):
        if getattr(off_run, field) != getattr(on_run, field):
            raise AbInvariantMismatch("shared_inputs", f"A/B cohorts must share the same {label}")
    if off_run.provider != on_run.provider:
        raise AbInvariantMismatch("provider_identity", "A/B cohorts must share the same provider")
    if off_run.model != on_run.model:
        raise AbInvariantMismatch("model_identity", "A/B cohorts must share the same model")
    if off_run.alternative_evidence != on_run.alternative_evidence or off_run.alternative_evidence_ref != on_run.alternative_evidence_ref:
        raise AbInvariantMismatch("alternative_evidence_ref", "A/B cohorts must share the same alternative evidence reference")
    if off_run.alternative_evidence:
        expected_ref = alternative_evidence_binding_fingerprint(off_run)
        if off_run.alternative_evidence_ref != expected_ref or on_run.alternative_evidence_ref != alternative_evidence_binding_fingerprint(on_run):
            raise AbInvariantMismatch("alternative_evidence_ref", "alternative evidence reference must bind immutable inputs")

    token_delta = on_run.estimated_input_tokens - off_run.estimated_input_tokens
    input_reduction_fraction = (
        (off_run.estimated_input_tokens - on_run.estimated_input_tokens) / off_run.estimated_input_tokens
        if off_run.estimated_input_tokens
        else 0.0
    )
    report_p95 = on_run.latency_ms
    coverage = on_run.evidence_coverage

    failed_reasons: list[str] = []
    has_strong_reduction = input_reduction_fraction >= AB_INPUT_REDUCTION_THRESHOLD
    if not has_strong_reduction and not on_run.alternative_evidence:
        failed_reasons.append("no_strong_input_reduction_no_alternative_evidence")
    if coverage < AB_EVIDENCE_COVERAGE_REQUIRED:
        failed_reasons.append("evidence_coverage_below_100")
    if report_p95 > AB_P95_LATENCY_CAP_MS:
        failed_reasons.append("p95_latency_over_800ms")
    if not on_run.review_no_regression:
        failed_reasons.append("review_hermes_regression")
    if on_run.sensitive_capture_count != 0:
        failed_reasons.append("sensitive_capture")
    if not on_run.baseline_scope_unchanged:
        failed_reasons.append("baseline_scope_changed")

    decision = "accepted" if not failed_reasons else "acceptance_rejected"

    report_id = canonical_fingerprint({
        "schemaVersion": AB_REPORT_SCHEMA,
        "head": off_run.head,
        "taskFingerprint": off_run.task_fingerprint,
        "briefFingerprint": off_run.brief_fingerprint,
        "allowedFiles": sorted(off_run.allowed_files),
        "commands": sorted(off_run.commands),
        "provider": off_run.provider,
        "model": off_run.model,
        "tokenDelta": token_delta,
        "inputReductionFraction": round(input_reduction_fraction, 6),
        # Acceptance-driving metrics and outcome must be part of the identity.
        "p95LatencyMs": report_p95,
        "evidenceCoverage": coverage,
        "decision": decision,
        "failedReasons": sorted(failed_reasons),
        "reviewNoRegression": on_run.review_no_regression,
        "baselineScopeUnchanged": on_run.baseline_scope_unchanged,
        "sensitiveCaptureCount": on_run.sensitive_capture_count,
        "fallback": on_run.fallback,
        "alternativeEvidence": on_run.alternative_evidence,
        "alternativeEvidenceRef": on_run.alternative_evidence_ref,
    })

    return MemorySidecarAbReport(
        ab_report_id=report_id,
        off_run=off_run,
        on_run=on_run,
        token_delta=token_delta,
        input_reduction_fraction=input_reduction_fraction,
        p95_latency_ms=report_p95,
        evidence_coverage=coverage,
        decision=decision,
        failed_reasons=tuple(failed_reasons),
        recall_auto_enabled=False,
    )
