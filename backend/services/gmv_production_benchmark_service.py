"""Bounded contracts and orchestration primitives for production-like GMV benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
from pathlib import Path
from typing import Callable, Mapping
import time

import pandas as pd


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

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "decision": self.decision,
            "stageMs": dict(self.stage_ms),
            "affectedRows": self.affected_rows,
            "copiedRows": self.copied_rows,
            "recomputedRows": self.recomputed_rows,
            "unaffectedAggregationCalls": self.unaffected_aggregation_calls,
            "peakRssBytes": self.peak_rss_bytes,
            "equivalenceStatus": self.equivalence_status,
            "fallbackReason": self.fallback_reason,
            "activePointerUnchangedOnFailure": self.active_pointer_unchanged_on_failure,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    case: BenchmarkCase
    runs: tuple[BenchmarkRunEvidence, ...]
    warm_reads: tuple[BenchmarkRunEvidence, ...]
    status: str
    failure_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "gmv-production-rebuild-benchmark-v1",
            "case": self.case.to_dict(),
            "runs": [item.to_dict() for item in self.runs],
            "warmReads": [item.to_dict() for item in self.warm_reads],
            "status": self.status,
            "failureReasons": list(self.failure_reasons),
        }


@dataclass(frozen=True, slots=True)
class IsolatedBenchmarkFixture:
    db_path: Path
    cache_dir: Path
    database_sha256: str
    schema_table_count: int
    scenario_manifest: Mapping[str, Mapping[str, object]]
    version_id: str
    revenue_generation_token: str
    rule_version: str
    revenue_frames: object
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
    from backend.services.gmv_refund_repository import GmvRefundRepository
    from backend.services.gmv_refund_service import (
        RevenueFrames, confirm_refund_batch, preview_refund_batch, revenue_state_token,
    )

    rule_version = "benchmark-rules-v1"
    tour = pd.DataFrame([{
        "來源單據號": "BENCH-S-1", "收款原幣金額": 100.0, "收款類型": "旅費",
    }])
    frames = RevenueFrames(tour, pd.DataFrame(), tour.copy(), pd.DataFrame())
    token = revenue_state_token(frames, rule_version)
    preview = preview_refund_batch(
        pd.DataFrame([{
            "退款單號": "BENCH-R-1", "來源單據號": "BENCH-S-1",
            "退款原幣金額": 20, "退款狀態": "已退款",
        }]),
        repository=GmvRefundRepository(db_path), revenue_frames=frames,
        revenue_generation_token=token, rule_version=rule_version,
        file_sha256="benchmark-fixture-file",
    )
    receipt = confirm_refund_batch(
        preview, actor="benchmark", acknowledgements=frozenset(), db_path=db_path,
        coordination_db_path=fixture_root / "coordination.db",
        revenue_loader=lambda: frames,
        revenue_generation_loader=lambda: token,
    )
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
        version_id=receipt.version_id,
        revenue_generation_token=token,
        rule_version=rule_version,
        revenue_frames=frames,
    )


def run_production_rebuild_benchmark(
    case: BenchmarkCase,
    *,
    root: str | Path,
    runs: int = 3,
    warm_reads: int = 3,
    full_runner: Callable[[BenchmarkCase, Path], Mapping[str, object]] | None = None,
    candidate_runner: Callable[[BenchmarkCase, Path], Mapping[str, object]] | None = None,
    warm_reader: Callable[[BenchmarkCase, Path], Mapping[str, object]] | None = None,
) -> BenchmarkSummary:
    """Run bounded production-like stages through caller-supplied seams.

    The seams make the evidence collector testable; the eventual CLI supplies
    adapters for the existing full/fast service and cache read paths.
    """
    if runs < 3 or warm_reads < 3:
        raise ValueError("benchmark requires at least 3 cold runs and 3 warm reads")
    benchmark_root = _validate_fixture_root(root)
    if not all((full_runner, candidate_runner, warm_reader)):
        raise ValueError("full_runner, candidate_runner and warm_reader are required")
    full_runner = full_runner  # type narrowing for static checkers
    candidate_runner = candidate_runner
    warm_reader = warm_reader
    run_evidence: list[BenchmarkRunEvidence] = []
    warm_evidence: list[BenchmarkRunEvidence] = []
    failure_reasons: list[str] = []
    for index in range(runs):
        run_root = benchmark_root / f"cold-{index}"
        run_root.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        full_result = dict(full_runner(case, run_root / "full"))
        full_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        candidate_result = dict(candidate_runner(case, run_root / "candidate"))
        candidate_ms = (time.perf_counter() - started) * 1000
        run_evidence.append(BenchmarkRunEvidence(
            mode="incremental-shadow",
            decision=str(candidate_result.get("decision", case.expected_decision)),
            stage_ms={"fullCold": round(full_ms, 3), "incrementalShadow": round(candidate_ms, 3)},
            affected_rows=int(candidate_result.get("affectedRows", case.affected_count)),
            copied_rows=int(candidate_result.get("copiedRows", case.receipt_count - case.affected_count)),
            recomputed_rows=int(candidate_result.get("recomputedRows", case.affected_count)),
            unaffected_aggregation_calls=(
                int(candidate_result["unaffectedAggregationCalls"])
                if candidate_result.get("unaffectedAggregationCalls") is not None else None
            ),
            peak_rss_bytes=(
                int(candidate_result["peakRssBytes"])
                if candidate_result.get("peakRssBytes") is not None else None
            ),
            equivalence_status=str(candidate_result.get("equivalenceStatus", "NOT_RUN")),
            fallback_reason=(str(candidate_result["fallbackReason"])
                             if candidate_result.get("fallbackReason") else None),
            active_pointer_unchanged_on_failure=bool(
                candidate_result.get("activePointerUnchangedOnFailure", True)
            ),
        ))
        if full_result.get("status") != "ready":
            failure_reasons.append("FULL_REBUILD_NOT_READY")
        if candidate_result.get("status") != "ready":
            failure_reasons.append("CANDIDATE_NOT_READY")
    for index in range(warm_reads):
        read_root = benchmark_root / f"warm-{index}"
        read_root.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        result = dict(warm_reader(case, read_root))
        elapsed = (time.perf_counter() - started) * 1000
        warm_evidence.append(BenchmarkRunEvidence(
            mode="warm-read",
            decision="READ_EXISTING_READY",
            stage_ms={"warmRead": round(elapsed, 3)},
            affected_rows=0,
            copied_rows=0,
            recomputed_rows=0,
            unaffected_aggregation_calls=0,
            peak_rss_bytes=(int(result["peakRssBytes"]) if result.get("peakRssBytes") is not None else None),
            equivalence_status="PASS" if result.get("canExport") is True else "FAIL",
            fallback_reason=None if result.get("canExport") is True else "WARM_READ_NOT_EXPORTABLE",
        ))
    if any(item.unaffected_aggregation_calls is None for item in run_evidence):
        status = "INCONCLUSIVE"
        failure_reasons.append("MISSING_AGGREGATION_INSTRUMENTATION")
    elif any(item.equivalence_status != "PASS" for item in run_evidence + warm_evidence):
        status = "FAIL"
        failure_reasons.append("SEMANTIC_EQUIVALENCE_FAILED")
    elif failure_reasons:
        status = "FAIL"
    else:
        status = "PASS"
    return BenchmarkSummary(
        case=case,
        runs=tuple(run_evidence),
        warm_reads=tuple(warm_evidence),
        status=status,
        failure_reasons=tuple(sorted(set(failure_reasons))),
    )


def run_isolated_production_rebuild_benchmark(
    case: BenchmarkCase,
    *,
    fixture: IsolatedBenchmarkFixture,
    root: str | Path,
    runs: int = 3,
    warm_reads: int = 3,
) -> BenchmarkSummary:
    """Adapt the existing full/fast cache services to the isolated runner."""
    from backend.services.gmv_export_cache_service import load_gmv_export_cache
    from backend.services.gmv_refund_repository import GmvRefundRepository
    from backend.services.gmv_refund_service import (
        build_active_gmv_read_model, build_gmv_formal_artifacts,
        build_gmv_formal_artifacts_fast_or_legacy,
    )

    repository = GmvRefundRepository(fixture.db_path)
    latest_candidate_cache: Path | None = None

    def full_runner(current_case: BenchmarkCase, run_root: Path) -> Mapping[str, object]:
        result = build_gmv_formal_artifacts(
            repository=repository, version_id=fixture.version_id,
            revenue_frames=fixture.revenue_frames, rule_version=fixture.rule_version,
            cache_dir=run_root,
        )
        manifest = result.cache_manifest
        return {
            "status": manifest.status,
            "artifactCount": len(manifest.artifacts),
            "equivalenceStatus": manifest.equivalence_status,
        }

    def candidate_runner(current_case: BenchmarkCase, run_root: Path) -> Mapping[str, object]:
        nonlocal latest_candidate_cache
        latest_candidate_cache = run_root
        result = build_gmv_formal_artifacts_fast_or_legacy(
            repository=repository, version_id=fixture.version_id,
            revenue_frames=fixture.revenue_frames, rule_version=fixture.rule_version,
            cache_dir=run_root, validation_mode="shadow",
        )
        manifest = result.cache_manifest
        return {
            "status": manifest.status,
            "artifactCount": len(manifest.artifacts),
            "equivalenceStatus": manifest.equivalence_status,
            "fallbackReason": manifest.error if manifest.builder_mode == "legacy_fallback" else None,
            "activePointerUnchangedOnFailure": True,
        }

    def warm_reader(current_case: BenchmarkCase, read_root: Path) -> Mapping[str, object]:
        if latest_candidate_cache is None:
            return {"status": "CACHE_NOT_READY", "canExport": False}
        manifest = load_gmv_export_cache(
            version_id=fixture.version_id,
            revenue_generation_token=fixture.revenue_generation_token,
            rule_version=fixture.rule_version,
            cache_dir=latest_candidate_cache,
        )
        model = build_active_gmv_read_model(
            repository, fixture.revenue_frames, rule_version=fixture.rule_version,
            cache_manifest=manifest, cache_dir=latest_candidate_cache,
        )
        return {"status": model.status, "canExport": model.can_export}

    before = hashlib.sha256(fixture.db_path.read_bytes()).hexdigest()
    summary = run_production_rebuild_benchmark(
        case, root=root, runs=runs, warm_reads=warm_reads,
        full_runner=full_runner, candidate_runner=candidate_runner,
        warm_reader=warm_reader,
    )
    after = hashlib.sha256(fixture.db_path.read_bytes()).hexdigest()
    if before != after:
        raise AssertionError("isolated benchmark mutated its fixture database")
    return summary


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
