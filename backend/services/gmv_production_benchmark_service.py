"""Bounded contracts and orchestration primitives for production-like GMV benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
from pathlib import Path
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


@dataclass(frozen=True, slots=True)
class IsolatedBenchmarkFixture:
    db_path: Path
    cache_dir: Path
    database_sha256: str
    schema_table_count: int
    scenario_manifest: Mapping[str, Mapping[str, object]]
    database_mutated: bool = False


def _validate_fixture_root(root: str | Path) -> Path:
    path = Path(root).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[2]
    temp_root = Path(__import__("tempfile").gettempdir()).resolve()
    if path == project_root or ".nbs_runtime_cache" in {part.lower() for part in path.parts}:
        raise ValueError("fixture root must be isolated from formal runtime")
    if path != temp_root and temp_root not in path.parents:
        raise ValueError("fixture root must be under an isolated temporary directory")
    return path


def create_isolated_benchmark_fixture(
    case: BenchmarkCase, *, root: str | Path
) -> IsolatedBenchmarkFixture:
    """Create a disposable schema fixture without loading formal business rows."""
    fixture_root = _validate_fixture_root(root)
    fixture_root.mkdir(parents=True, exist_ok=True)
    db_path = fixture_root / "fixture.sqlite3"
    cache_dir = fixture_root / "cache"
    cache_dir.mkdir(exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS tour_data (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE IF NOT EXISTS others_data (id INTEGER PRIMARY KEY)")
        from backend.services.gmv_refund_repository import migrate_gmv_schema

        migrate_gmv_schema(db_path)
        schema_table_count = int(connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0])
    scenario_names = (
        "status_transition", "amount_change", "tt_method_transition",
        "over_refund", "multi_member", "unmatched",
    )
    scenario_manifest = {
        name: {"enabled": name in case.scenario_flags, "rowCount": 0}
        for name in scenario_names
    }
    return IsolatedBenchmarkFixture(
        db_path=db_path,
        cache_dir=cache_dir,
        database_sha256=hashlib.sha256(db_path.read_bytes()).hexdigest(),
        schema_table_count=schema_table_count,
        scenario_manifest=scenario_manifest,
    )


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
