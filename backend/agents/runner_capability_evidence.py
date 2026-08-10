from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint


RUNNER_CAPABILITY_SCHEMA = "runner-capability-evidence-v1"
ALLOWED_PROVIDER = "hermes"
ALLOWED_MODEL = "deepseek-v4-flash"
RECALL_MODES = frozenset({"off", "on"})
CAPABILITY_RESULTS = frozenset({"ready", "blocked_runner_capability", "acceptance_rejected"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_RUN_FIELDS = frozenset({
    "runId", "sequence", "recallMode", "gitHead", "projectId", "workspaceKind",
    "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint",
    "commandsFingerprint", "provider", "model", "status", "cacheReplayDetected",
    "inputTokens", "outputTokens", "p95Ms", "provenanceCoverage", "sensitiveCaptureCount",
    "writerDisabled", "baselineUnchanged", "formalScopeUnchanged", "reviewNoRegression",
    "hermesNoRegression",
})
_RAW_CONTENT_FIELDS = frozenset({
    "prompt", "rawPrompt", "output", "rawModelOutput", "runnerCommand", "command", "logs",
    "fullLogs", "credentials", "secret", "absolutePath", "path", "hints", "rawHints",
})
_MAX_TOKENS = 10_000_000
_MAX_LATENCY_MS = 3_600_000


class RunnerCapabilityEvidenceError(ValueError):
    """Raised when runner capability evidence is not bounded and verifiable."""


def _require_exact_fields(value: Mapping[str, Any], fields: frozenset[str], *, kind: str) -> None:
    keys = set(value)
    raw = keys & _RAW_CONTENT_FIELDS
    if raw:
        raise RunnerCapabilityEvidenceError(f"{kind} contains forbidden raw-content fields: {sorted(raw)}")
    unknown = keys - fields
    missing = fields - keys
    if unknown or missing:
        raise RunnerCapabilityEvidenceError(f"{kind} has unknown or missing fields")


def _require_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RunnerCapabilityEvidenceError(f"{field_name} must be a canonical SHA-256 fingerprint")
    return value


def _require_git_head(value: object, *, field_name: str = "gitHead") -> str:
    if not isinstance(value, str) or not _GIT_HEAD.fullmatch(value):
        raise RunnerCapabilityEvidenceError(f"{field_name} must be an immutable 40-character Git SHA")
    return value


def _require_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise RunnerCapabilityEvidenceError(f"{field_name} must be a bounded identifier")
    return value


def _require_int(value: object, *, field_name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise RunnerCapabilityEvidenceError(f"{field_name} must be a bounded non-negative integer")
    return value


def _require_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RunnerCapabilityEvidenceError(f"{field_name} must be a boolean")
    return value


@dataclass(frozen=True)
class RunnerCapabilityRun:
    run_id: str
    sequence: int
    recall_mode: str
    git_head: str
    project_id: str
    workspace_kind: str
    workspace_fingerprint: str
    task_fingerprint: str
    brief_fingerprint: str
    allowed_files_fingerprint: str
    commands_fingerprint: str
    provider: str
    model: str
    status: str
    cache_replay_detected: bool
    input_tokens: int
    output_tokens: int
    p95_ms: int
    provenance_coverage: float
    sensitive_capture_count: int
    writer_disabled: bool
    baseline_unchanged: bool
    formal_scope_unchanged: bool
    review_no_regression: bool
    hermes_no_regression: bool
    run_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.run_id, field_name="runId")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1 or self.sequence > 2:
            raise RunnerCapabilityEvidenceError("sequence must be 1 or 2")
        if self.recall_mode not in RECALL_MODES:
            raise RunnerCapabilityEvidenceError("recallMode is unsupported")
        _require_git_head(self.git_head)
        _require_identifier(self.project_id, field_name="projectId")
        if self.workspace_kind not in {"repo", "isolated_worktree"}:
            raise RunnerCapabilityEvidenceError("workspaceKind is unsupported")
        for name in ("workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint"):
            _require_sha256(getattr(self, _snake_case(name)), field_name=name)
        if self.provider != ALLOWED_PROVIDER or self.model != ALLOWED_MODEL:
            raise RunnerCapabilityEvidenceError("provider and model must be live allowed identities")
        if self.status != "completed":
            raise RunnerCapabilityEvidenceError("status must be completed")
        _require_bool(self.cache_replay_detected, field_name="cacheReplayDetected")
        _require_int(self.input_tokens, field_name="inputTokens", maximum=_MAX_TOKENS)
        _require_int(self.output_tokens, field_name="outputTokens", maximum=_MAX_TOKENS)
        _require_int(self.p95_ms, field_name="p95Ms", maximum=_MAX_LATENCY_MS)
        if isinstance(self.provenance_coverage, bool) or not isinstance(self.provenance_coverage, (int, float)) or not 0.0 <= self.provenance_coverage <= 1.0:
            raise RunnerCapabilityEvidenceError("provenanceCoverage must be between 0 and 1")
        _require_int(self.sensitive_capture_count, field_name="sensitiveCaptureCount", maximum=_MAX_TOKENS)
        for name in ("writer_disabled", "baseline_unchanged", "formal_scope_unchanged", "review_no_regression", "hermes_no_regression"):
            _require_bool(getattr(self, name), field_name=name)
        object.__setattr__(self, "run_fingerprint", canonical_fingerprint(self.unsigned_dict()))

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id, "sequence": self.sequence, "recallMode": self.recall_mode,
            "gitHead": self.git_head, "projectId": self.project_id, "workspaceKind": self.workspace_kind,
            "workspaceFingerprint": self.workspace_fingerprint, "taskFingerprint": self.task_fingerprint,
            "briefFingerprint": self.brief_fingerprint, "allowedFilesFingerprint": self.allowed_files_fingerprint,
            "commandsFingerprint": self.commands_fingerprint, "provider": self.provider, "model": self.model,
            "status": self.status, "cacheReplayDetected": self.cache_replay_detected,
            "inputTokens": self.input_tokens, "outputTokens": self.output_tokens, "p95Ms": self.p95_ms,
            "provenanceCoverage": self.provenance_coverage, "sensitiveCaptureCount": self.sensitive_capture_count,
            "writerDisabled": self.writer_disabled, "baselineUnchanged": self.baseline_unchanged,
            "formalScopeUnchanged": self.formal_scope_unchanged, "reviewNoRegression": self.review_no_regression,
            "hermesNoRegression": self.hermes_no_regression,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "runFingerprint": self.run_fingerprint}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunnerCapabilityRun":
        expected = _RUN_FIELDS | {"runFingerprint"}
        _require_exact_fields(value, expected, kind="runner capability run")
        run = cls(**{
            "run_id": value["runId"], "sequence": value["sequence"], "recall_mode": value["recallMode"],
            "git_head": value["gitHead"], "project_id": value["projectId"], "workspace_kind": value["workspaceKind"],
            "workspace_fingerprint": value["workspaceFingerprint"], "task_fingerprint": value["taskFingerprint"],
            "brief_fingerprint": value["briefFingerprint"], "allowed_files_fingerprint": value["allowedFilesFingerprint"],
            "commands_fingerprint": value["commandsFingerprint"], "provider": value["provider"], "model": value["model"],
            "status": value["status"], "cache_replay_detected": value["cacheReplayDetected"],
            "input_tokens": value["inputTokens"], "output_tokens": value["outputTokens"], "p95_ms": value["p95Ms"],
            "provenance_coverage": value["provenanceCoverage"], "sensitive_capture_count": value["sensitiveCaptureCount"],
            "writer_disabled": value["writerDisabled"], "baseline_unchanged": value["baselineUnchanged"],
            "formal_scope_unchanged": value["formalScopeUnchanged"], "review_no_regression": value["reviewNoRegression"],
            "hermes_no_regression": value["hermesNoRegression"],
        })
        if value["runFingerprint"] != run.run_fingerprint:
            raise RunnerCapabilityEvidenceError("run fingerprint does not match payload")
        return run


def _snake_case(name: str) -> str:
    return re.sub(r"([A-Z])", lambda match: "_" + match.group(1).lower(), name).lstrip("_")


@dataclass(frozen=True)
class RunnerCapabilityComparison:
    same_immutable_inputs: bool
    distinct_run_ids: bool
    cache_replay_detected: bool
    token_reduction_ratio: float | None
    alternative_evidence: bool = False

    def __post_init__(self) -> None:
        for name in ("same_immutable_inputs", "distinct_run_ids", "cache_replay_detected", "alternative_evidence"):
            _require_bool(getattr(self, name), field_name=name)
        if self.token_reduction_ratio is not None and (isinstance(self.token_reduction_ratio, bool) or not isinstance(self.token_reduction_ratio, (int, float))):
            raise RunnerCapabilityEvidenceError("tokenReductionRatio must be numeric or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sameImmutableInputs": self.same_immutable_inputs, "distinctRunIds": self.distinct_run_ids,
            "cacheReplayDetected": self.cache_replay_detected, "tokenReductionRatio": self.token_reduction_ratio,
            "alternativeEvidence": self.alternative_evidence,
        }


@dataclass(frozen=True)
class RunnerCapabilityEvidence:
    git_head: str
    project_id: str
    workspace_kind: str
    task_fingerprint: str
    brief_fingerprint: str
    allowed_files_fingerprint: str
    commands_fingerprint: str
    provider: str
    model: str
    control: RunnerCapabilityRun
    treatment: RunnerCapabilityRun
    comparison: RunnerCapabilityComparison
    result: str = "blocked_runner_capability"
    schema_version: str = RUNNER_CAPABILITY_SCHEMA
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != RUNNER_CAPABILITY_SCHEMA or self.result not in CAPABILITY_RESULTS:
            raise RunnerCapabilityEvidenceError("unsupported evidence schema or result")
        _require_git_head(self.git_head)
        _require_identifier(self.project_id, field_name="projectId")
        if self.workspace_kind not in {"repo", "isolated_worktree"} or self.provider != ALLOWED_PROVIDER or self.model != ALLOWED_MODEL:
            raise RunnerCapabilityEvidenceError("invalid evidence identity")
        for name in ("task_fingerprint", "brief_fingerprint", "allowed_files_fingerprint", "commands_fingerprint"):
            _require_sha256(getattr(self, name), field_name=name)
        if not isinstance(self.control, RunnerCapabilityRun) or not isinstance(self.treatment, RunnerCapabilityRun) or not isinstance(self.comparison, RunnerCapabilityComparison):
            raise RunnerCapabilityEvidenceError("evidence must contain bounded typed records")
        object.__setattr__(self, "evidence_id", canonical_fingerprint(self.unsigned_dict()))

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version, "gitHead": self.git_head, "projectId": self.project_id,
            "workspaceKind": self.workspace_kind, "taskFingerprint": self.task_fingerprint,
            "briefFingerprint": self.brief_fingerprint, "allowedFilesFingerprint": self.allowed_files_fingerprint,
            "commandsFingerprint": self.commands_fingerprint, "provider": self.provider, "model": self.model,
            "control": self.control.to_dict(), "treatment": self.treatment.to_dict(),
            "comparison": self.comparison.to_dict(),
            "provenance": {"coverage": self.treatment.provenance_coverage, "sensitiveCaptureCount": self.treatment.sensitive_capture_count},
            "latency": {"p95Ms": self.treatment.p95_ms}, "result": self.result,
        }

    def recompute_evidence_id(self) -> str:
        return canonical_fingerprint(self.unsigned_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "evidenceId": self.evidence_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunnerCapabilityEvidence":
        fields = frozenset({"schemaVersion", "evidenceId", "gitHead", "projectId", "workspaceKind", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "control", "treatment", "comparison", "provenance", "latency", "result"})
        _require_exact_fields(value, fields, kind="runner capability evidence")
        comparison = value["comparison"]
        if not isinstance(comparison, Mapping) or set(comparison) != {"sameImmutableInputs", "distinctRunIds", "cacheReplayDetected", "tokenReductionRatio", "alternativeEvidence"}:
            raise RunnerCapabilityEvidenceError("comparison has unknown or missing fields")
        evidence = cls(
            git_head=value["gitHead"], project_id=value["projectId"], workspace_kind=value["workspaceKind"],
            task_fingerprint=value["taskFingerprint"], brief_fingerprint=value["briefFingerprint"],
            allowed_files_fingerprint=value["allowedFilesFingerprint"], commands_fingerprint=value["commandsFingerprint"],
            provider=value["provider"], model=value["model"], control=RunnerCapabilityRun.from_dict(value["control"]),
            treatment=RunnerCapabilityRun.from_dict(value["treatment"]),
            comparison=RunnerCapabilityComparison(comparison["sameImmutableInputs"], comparison["distinctRunIds"], comparison["cacheReplayDetected"], comparison["tokenReductionRatio"], comparison["alternativeEvidence"]),
            result=value["result"], schema_version=value["schemaVersion"],
        )
        if value["provenance"] != evidence.unsigned_dict()["provenance"] or value["latency"] != evidence.unsigned_dict()["latency"] or value["evidenceId"] != evidence.evidence_id:
            raise RunnerCapabilityEvidenceError("evidence payload does not match its canonical identity")
        return evidence


def build_capability_evidence(control: Mapping[str, Any], treatment: Mapping[str, Any], *, expected_git_head: str, expected_task_fingerprint: str) -> RunnerCapabilityEvidence:
    _require_git_head(expected_git_head, field_name="expected_git_head")
    _require_sha256(expected_task_fingerprint, field_name="expected_task_fingerprint")
    _require_exact_fields(control, _RUN_FIELDS, kind="control run")
    _require_exact_fields(treatment, _RUN_FIELDS, kind="treatment run")
    control_run = RunnerCapabilityRun.from_dict({**control, "runFingerprint": canonical_fingerprint(dict(control))})
    treatment_run = RunnerCapabilityRun.from_dict({**treatment, "runFingerprint": canonical_fingerprint(dict(treatment))})
    if control_run.git_head != expected_git_head or treatment_run.git_head != expected_git_head:
        raise RunnerCapabilityEvidenceError("run gitHead does not match expected immutable head")
    if control_run.task_fingerprint != expected_task_fingerprint or treatment_run.task_fingerprint != expected_task_fingerprint:
        raise RunnerCapabilityEvidenceError("run taskFingerprint does not match expected task")
    immutable = ("git_head", "project_id", "workspace_kind", "workspace_fingerprint", "task_fingerprint", "brief_fingerprint", "allowed_files_fingerprint", "commands_fingerprint", "provider", "model")
    same_immutable_inputs = all(getattr(control_run, name) == getattr(treatment_run, name) for name in immutable)
    ratio = None if control_run.input_tokens == 0 else (control_run.input_tokens - treatment_run.input_tokens) / control_run.input_tokens
    comparison = RunnerCapabilityComparison(same_immutable_inputs, control_run.run_id != treatment_run.run_id, control_run.cache_replay_detected or treatment_run.cache_replay_detected, ratio)
    return RunnerCapabilityEvidence(
        git_head=control_run.git_head, project_id=control_run.project_id, workspace_kind=control_run.workspace_kind,
        task_fingerprint=control_run.task_fingerprint, brief_fingerprint=control_run.brief_fingerprint,
        allowed_files_fingerprint=control_run.allowed_files_fingerprint, commands_fingerprint=control_run.commands_fingerprint,
        provider=control_run.provider, model=control_run.model, control=control_run, treatment=treatment_run,
        comparison=comparison,
    )
