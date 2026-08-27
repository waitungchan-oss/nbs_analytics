"""Planning primitives for bounded affected-receipt GMV rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from backend.services.gmv_refund_models import (
    IncrementalRebuildPlan,
    RefundStateDelta,
    RebuildDecision,
    RebuildReasonCode,
    canonical_payload_sha256,
    normalize_text,
)


_METRIC_KEY_COLUMNS = (
    "period_basis", "period_key", "dimension_type", "dimension_key",
    "dimension_label", "refund_dimension", "metric_name", "quantity_basis",
)
_METRIC_VALUE_COLUMNS = ("metric_amount_minor", "metric_count")


@dataclass(frozen=True, slots=True)
class RebuildFingerprints:
    base_revenue_generation_token: str
    current_revenue_generation_token: str
    base_rules_fingerprint: str
    current_rules_fingerprint: str
    base_source_fingerprint: str
    current_source_fingerprint: str

    @property
    def matches(self) -> bool:
        return (
            normalize_text(self.base_revenue_generation_token)
            == normalize_text(self.current_revenue_generation_token)
            and normalize_text(self.base_rules_fingerprint)
            == normalize_text(self.current_rules_fingerprint)
            and normalize_text(self.base_source_fingerprint)
            == normalize_text(self.current_source_fingerprint)
        )


def validate_rebuild_plan_freshness(
    plan: IncrementalRebuildPlan,
    *,
    current_base_version_id: str,
    current_revenue_generation_token: str,
    current_rules_fingerprint: str,
    current_source_fingerprint: str,
) -> tuple[bool, str | None]:
    """Prevent an incremental plan from publishing against changed inputs."""
    checks = (
        ("BASE_VERSION_CHANGED", plan.base_version_id, current_base_version_id),
        (
            "REVENUE_GENERATION_CHANGED",
            plan.revenue_generation_token,
            current_revenue_generation_token,
        ),
        ("RULES_FINGERPRINT_CHANGED", plan.rules_fingerprint, current_rules_fingerprint),
        ("SOURCE_FINGERPRINT_CHANGED", plan.source_fingerprint, current_source_fingerprint),
    )
    for reason, expected, actual in checks:
        if normalize_text(expected) != normalize_text(actual):
            return False, reason
    return True, None


_REBUILD_TELEMETRY_STAGES = frozenset({"plan", "affected", "aggregate", "equivalence", "publish"})


def build_rebuild_stage_telemetry(stage_timings_ms: Mapping[str, float]) -> dict[str, object]:
    """Return a stable, bounded telemetry payload for one rebuild attempt."""
    if not isinstance(stage_timings_ms, Mapping):
        raise ValueError("stage timings must be a mapping")
    stages: dict[str, float] = {}
    for raw_stage, raw_value in stage_timings_ms.items():
        stage = normalize_text(raw_stage)
        if stage not in _REBUILD_TELEMETRY_STAGES:
            raise ValueError(f"unknown rebuild telemetry stage name: {stage}")
        value = float(raw_value)
        if value < 0:
            raise ValueError("stage timing must be non-negative")
        stages[stage] = round(value, 3)
    return {
        "schemaVersion": "gmv-rebuild-telemetry-v1",
        "stages": dict(sorted(stages.items())),
        "totalMs": round(sum(stages.values()), 3),
    }


@dataclass(frozen=True, slots=True)
class IncrementalRebuildThresholds:
    max_affected_receipt_count: int = 100_000
    max_affected_receipt_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_affected_receipt_count < 0:
            raise ValueError("max_affected_receipt_count cannot be negative")
        if not 0 <= self.max_affected_receipt_ratio <= 1:
            raise ValueError("max_affected_receipt_ratio must be between 0 and 1")


def build_incremental_plan(
    *,
    base_version_id: str,
    state_delta: RefundStateDelta,
    fingerprints: RebuildFingerprints,
    source_receipt_universe_count: int | None = None,
    snapshot_complete: bool = True,
    thresholds: IncrementalRebuildThresholds | None = None,
) -> IncrementalRebuildPlan:
    """Create a deterministic plan without loading raw revenue or refund rows."""
    thresholds = thresholds or IncrementalRebuildThresholds()
    affected_receipts = tuple(sorted({normalize_text(item) for item in state_delta.affected_source_receipt_nos if normalize_text(item)}))
    affected_refund_ids = tuple(
        sorted(
            {
                normalize_text(item)
                for values in (
                    state_delta.new_refund_order_nos,
                    state_delta.status_changed_refund_order_nos,
                    state_delta.amount_changed_refund_order_nos,
                    state_delta.identity_conflict_refund_order_nos,
                )
                for item in values
                if normalize_text(item)
            }
        )
    )
    reasons: list[RebuildReasonCode] = []
    decision = RebuildDecision.INCREMENTAL_ELIGIBLE

    if state_delta.identity_conflict_refund_order_nos:
        reasons.append(RebuildReasonCode.IDENTITY_CONFLICT)
        decision = RebuildDecision.BLOCKED
    if not fingerprints.matches:
        reasons.append(RebuildReasonCode.FINGERPRINT_MISMATCH)
        if decision is not RebuildDecision.BLOCKED:
            decision = RebuildDecision.FULL_REBUILD_REQUIRED
    if not snapshot_complete:
        reasons.append(RebuildReasonCode.SNAPSHOT_INCOMPLETE)
        if decision is not RebuildDecision.BLOCKED:
            decision = RebuildDecision.FULL_REBUILD_REQUIRED

    if source_receipt_universe_count is not None:
        if source_receipt_universe_count < 0:
            raise ValueError("source_receipt_universe_count cannot be negative")
        affected_ratio = (
            len(affected_receipts) / source_receipt_universe_count
            if source_receipt_universe_count
            else 0.0
        )
        if (
            len(affected_receipts) > thresholds.max_affected_receipt_count
            or affected_ratio > thresholds.max_affected_receipt_ratio
        ):
            reasons.append(RebuildReasonCode.AFFECTED_SET_TOO_LARGE)
            if decision is not RebuildDecision.BLOCKED:
                decision = RebuildDecision.FULL_REBUILD_REQUIRED

    return IncrementalRebuildPlan(
        base_version_id=base_version_id,
        affected_source_receipt_nos=affected_receipts,
        affected_refund_ids=affected_refund_ids,
        affected_count=len(affected_receipts),
        unaffected_copy_candidate_count=max(
            (source_receipt_universe_count or 0) - len(affected_receipts), 0
        ),
        revenue_generation_token=normalize_text(fingerprints.current_revenue_generation_token),
        rules_fingerprint=normalize_text(fingerprints.current_rules_fingerprint),
        source_fingerprint=normalize_text(fingerprints.current_source_fingerprint),
        decision=decision,
        reason_codes=tuple(reasons),
    )


def resolve_rebuild_strategy(
    plan: IncrementalRebuildPlan,
    *,
    incremental_available: bool,
) -> str:
    """Select a safe execution path; never downgrade a blocked plan."""
    if plan.decision is RebuildDecision.BLOCKED:
        return "BLOCKED"
    if plan.decision is RebuildDecision.FULL_REBUILD_REQUIRED:
        return "FULL_REBUILD"
    return "INCREMENTAL" if incremental_available else "FULL_REBUILD"


def _aggregate_metric_rows(frame: pd.DataFrame) -> pd.DataFrame:
    required = set(_METRIC_KEY_COLUMNS + _METRIC_VALUE_COLUMNS)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("metric schema is missing: " + ",".join(missing))
    if frame.empty:
        return pd.DataFrame(columns=[*_METRIC_KEY_COLUMNS, *_METRIC_VALUE_COLUMNS]).set_index(list(_METRIC_KEY_COLUMNS))
    work = frame.loc[:, [*_METRIC_KEY_COLUMNS, *_METRIC_VALUE_COLUMNS]].copy()
    for column in _METRIC_VALUE_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
        if work[column].isna().any():
            raise ValueError(f"metric column is not numeric: {column}")
    return work.groupby(list(_METRIC_KEY_COLUMNS), dropna=False, sort=True)[list(_METRIC_VALUE_COLUMNS)].sum()


def build_incremental_metric_snapshot(
    base_metrics: pd.DataFrame,
    old_affected_metrics: pd.DataFrame,
    new_affected_metrics: pd.DataFrame,
) -> pd.DataFrame:
    """Apply old/new affected metric deltas without re-aggregating unaffected facts."""
    base = _aggregate_metric_rows(base_metrics)
    old = _aggregate_metric_rows(old_affected_metrics)
    new = _aggregate_metric_rows(new_affected_metrics)
    result = base.subtract(old, fill_value=0).add(new, fill_value=0)
    if (result < 0).any().any():
        raise ValueError("metric delta is negative")
    result = result.reset_index()
    result["metric_amount_minor"] = result["metric_amount_minor"].astype("int64")
    result["metric_count"] = result["metric_count"].astype("int64")
    return result.loc[:, [*_METRIC_KEY_COLUMNS, *_METRIC_VALUE_COLUMNS]]


@dataclass(frozen=True, slots=True)
class EquivalenceReport:
    status: str
    incremental_fingerprint: str
    reference_fingerprint: str
    first_mismatch: dict[str, str] | None


def _canonical_frame_payload(frame: pd.DataFrame) -> dict[str, object]:
    columns = sorted(str(column) for column in frame.columns)
    work = frame.loc[:, columns].copy()
    rows = []
    for row in work.itertuples(index=False, name=None):
        normalized = []
        for value in row:
            try:
                if pd.isna(value):
                    normalized.append("")
                    continue
            except (TypeError, ValueError):
                pass
            normalized.append(str(value).strip())
        rows.append(tuple(normalized))
    return {"columns": columns, "rows": sorted(rows)}


def compare_incremental_to_reference(
    incremental: Mapping[str, pd.DataFrame],
    reference: Mapping[str, pd.DataFrame],
) -> EquivalenceReport:
    """Compare named semantic layers, independent of row ordering or XLSX bytes."""
    layers = sorted(set(incremental) | set(reference))
    incremental_payload = {layer: _canonical_frame_payload(incremental[layer]) for layer in layers if layer in incremental}
    reference_payload = {layer: _canonical_frame_payload(reference[layer]) for layer in layers if layer in reference}
    incremental_fingerprint = canonical_payload_sha256(incremental_payload)
    reference_fingerprint = canonical_payload_sha256(reference_payload)
    if incremental_fingerprint == reference_fingerprint and set(incremental) == set(reference):
        return EquivalenceReport("PASS", incremental_fingerprint, reference_fingerprint, None)
    for layer in layers:
        if layer not in incremental or layer not in reference:
            return EquivalenceReport(
                "FAIL", incremental_fingerprint, reference_fingerprint,
                {"layer": layer, "reason": "MISSING_LAYER"},
            )
        left = canonical_payload_sha256(_canonical_frame_payload(incremental[layer]))
        right = canonical_payload_sha256(_canonical_frame_payload(reference[layer]))
        if left != right:
            return EquivalenceReport(
                "FAIL", incremental_fingerprint, reference_fingerprint,
                {"layer": layer, "incrementalDigest": left, "referenceDigest": right},
            )
    return EquivalenceReport("FAIL", incremental_fingerprint, reference_fingerprint, {"layer": "<unknown>", "reason": "PAYLOAD_MISMATCH"})
