"""Bounded contracts and orchestration primitives for production-like GMV benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


FORMAL_SCOPE = "不含掛賬核銷與TT退款轉團款"
FROZEN_BASELINE = "HKD 12,057,968"


def _normalize_flags(flags: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    values = {str(flag).strip() for flag in (flags or ()) if str(flag).strip()}
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    affected_ratio: float
    receipt_count: int
    affected_count: int
    scenario_flags: tuple[str, ...] = ()
    formal_scope: str = FORMAL_SCOPE
    frozen_baseline: str = FROZEN_BASELINE
    database_mutated: bool = False
    expected_decision: str = "INCREMENTAL_ELIGIBLE"

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "gmv-production-rebuild-benchmark-v1",
            "caseId": self.case_id,
            "affectedRatio": self.affected_ratio,
            "receiptCount": self.receipt_count,
            "affectedCount": self.affected_count,
            "scenarioFlags": list(self.scenario_flags),
            "formalScope": self.formal_scope,
            "frozenBaseline": self.frozen_baseline,
            "databaseMutated": self.database_mutated,
            "expectedDecision": self.expected_decision,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRunEvidence:
    mode: str
    decision: str
    stage_ms: Mapping[str, float]
    affected_rows: int
    copied_rows: int
    recomputed_rows: int
    unaffected_aggregation_calls: int | None
    peak_rss_bytes: int | None
    equivalence_status: str
    fallback_reason: str | None = None
    active_pointer_unchanged_on_failure: bool = True


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    case: BenchmarkCase
    runs: tuple[BenchmarkRunEvidence, ...]
    warm_reads: tuple[BenchmarkRunEvidence, ...]
    status: str
    failure_reasons: tuple[str, ...] = ()


def build_benchmark_case(
    *,
    affected_ratio: float,
    receipt_count: int,
    scenario_flags: tuple[str, ...] | list[str] | None = None,
    max_affected_ratio: float = 0.2,
) -> BenchmarkCase:
    """Build a deterministic synthetic case without loading business data."""
    if not 0 <= affected_ratio <= 1:
        raise ValueError("affected_ratio must be between 0 and 1")
    if receipt_count <= 0:
        raise ValueError("receipt_count must be positive")
    if not 0 <= max_affected_ratio <= 1:
        raise ValueError("max_affected_ratio must be between 0 and 1")
    affected_count = round(receipt_count * affected_ratio)
    over_guardrail = affected_ratio > max_affected_ratio
    return BenchmarkCase(
        case_id="over-guardrail" if over_guardrail else f"ratio-{affected_ratio:.3f}",
        affected_ratio=affected_ratio,
        receipt_count=receipt_count,
        affected_count=affected_count,
        scenario_flags=_normalize_flags(scenario_flags),
        expected_decision="FULL_REBUILD_REQUIRED" if over_guardrail else "INCREMENTAL_ELIGIBLE",
    )
