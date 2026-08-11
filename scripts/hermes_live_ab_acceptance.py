"""Read-only, bounded acceptance adapter for a completed live Hermes A/B run."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_RECEIPT_BYTES = 64 * 1024
_BLOCKED = "blocked_runner_capability"
_STATUSES = frozenset({"ready", "acceptance_rejected", _BLOCKED})
_REASONS = frozenset({
    "immutable_inputs_mismatch", "invalid_sequence", "invalid_recall_mode", "reused_run_id",
    "cache_replay_detected", "completion_missing", "live_identity_mismatch", "token_usage_missing",
    "safety_attestation_missing", "run_fingerprint_mismatch", "token_reduction_below_threshold",
    "provenance_coverage_below_full", "sensitive_capture_detected", "latency_exceeds_limit",
    "reused_session_id", "activation_state_missing",
})

from backend.agents.runner_capability_evidence import (
    RunnerCapabilityEvidenceError,
    compare_capability_runs,
    live_receipt_to_run,
)


@dataclass(frozen=True)
class LiveABAcceptanceResult:
    status: str
    reasons: tuple[str, ...]
    metrics: dict[str, float | int | None]
    evidence_paths: tuple[str, str]

    def __post_init__(self) -> None:
        if (self.status not in _STATUSES or len(set(self.reasons)) != len(self.reasons)
                or any(reason not in _REASONS for reason in self.reasons)):
            raise ValueError("acceptance result is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "reasons": list(self.reasons), "metrics": self.metrics, "evidencePaths": list(self.evidence_paths)}


def _runtime_receipt(project_root: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise RunnerCapabilityEvidenceError("receipt path is invalid")
    relative = Path(raw_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RunnerCapabilityEvidenceError("receipt path must be runtime-relative")
    root = project_root.resolve(strict=False) / ".nbs_agent_runtime"
    if root.is_symlink():
        raise RunnerCapabilityEvidenceError("runtime root is unsafe")
    path = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise RunnerCapabilityEvidenceError("runtime receipt path is unsafe")
    try:
        resolved_root = root.resolve(strict=False)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise RunnerCapabilityEvidenceError("receipt is missing or unsafe") from exc
    if not resolved_path.is_file() or resolved_path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RunnerCapabilityEvidenceError("receipt is missing or unsafe")
    return resolved_path


def _load_receipt(project_root: Path, raw_path: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_runtime_receipt(project_root, raw_path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerCapabilityEvidenceError("receipt is invalid") from exc
    if not isinstance(value, Mapping):
        raise RunnerCapabilityEvidenceError("receipt is invalid")
    return value


def _blocked(paths: tuple[str, str], reason: str) -> LiveABAcceptanceResult:
    return LiveABAcceptanceResult(_BLOCKED, (reason,), {}, paths)


def assess_live_ab_receipts(control_receipt_path: str, treatment_receipt_path: str, *, project_root: str | Path = PROJECT_ROOT) -> LiveABAcceptanceResult:
    """Compare two Task 3 receipts without changing runtime or canonical state."""
    paths = (control_receipt_path, treatment_receipt_path)
    root = Path(project_root)
    try:
        control, control_session = live_receipt_to_run(_load_receipt(root, control_receipt_path))
        treatment, treatment_session = live_receipt_to_run(_load_receipt(root, treatment_receipt_path))
    except RunnerCapabilityEvidenceError as exc:
        # Preserve only a fixed classification; never serialize parser detail.
        return _blocked(paths, "activation_state_missing" if "activation receipt" in str(exc) else "completion_missing")
    except (TypeError, ValueError):
        return _blocked(paths, "completion_missing")
    if control_session == treatment_session:
        return _blocked(paths, "reused_session_id")
    comparison = compare_capability_runs(control, treatment)
    metrics = {
        "inputTokenReduction": comparison.token_reduction_ratio,
        "outputTokenDelta": treatment.output_tokens - control.output_tokens if control.output_tokens is not None and treatment.output_tokens is not None else None,
        "p95LatencyDeltaMs": treatment.p95_ms - control.p95_ms,
    }
    return LiveABAcceptanceResult(comparison.result, comparison.reasons, metrics, paths)
