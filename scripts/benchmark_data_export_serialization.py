"""Compare legacy and shared-facts Data Export serialization in isolation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _reference_builder(raw_tour: pd.DataFrame, raw_others: pd.DataFrame):
    from app_workflows import _compute_export_workbooks

    return _compute_export_workbooks(raw_tour, raw_others)


def _facts_builder(intermediate):
    from backend.services.export_intermediate_service import ExportScope, build_scope_report_facts

    return {
        scope.value: build_scope_report_facts(intermediate, scope)
        for scope in ExportScope
    }


def _write_dashboard_facts(facts, path: Path) -> None:
    from app_workflows import _buffer_to_bytes, _current_rules
    from pipeline import build_dashboard_data

    branch_mapping, target_branches, cruise_depts, sales_reps, _ = _current_rules()
    buffer, _, _ = build_dashboard_data(
        facts.tour.copy(deep=True), facts.others.copy(deep=True),
        branch_mapping, target_branches, cruise_depts, sales_reps,
        make_workbook=True,
        include_branch_salesperson_sheet=facts.scope_id == "official",
        _already_normalized=True,
    )
    path.write_bytes(_buffer_to_bytes(buffer) or b"")


def build_benchmark_report(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    samples: int = 3,
    worker_count: int = 3,
) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be positive")
    if worker_count < 1 or worker_count > 3:
        raise ValueError("worker_count must be between 1 and 3")
    from backend.services.export_benchmark_service import measure_legacy_export
    from backend.services.export_fast_path_service import build_fast_export_job_from_facts
    from backend.services.export_manifest_service import load_ready_export_manifest

    legacy_measurement = measure_legacy_export(_reference_builder, raw_tour, raw_others)
    fast_runs = []
    cache_scenarios = []
    for index in range(samples):
        cache_root = Path(tempfile.mkdtemp(prefix=f"nbs-export-benchmark-{index}-"))
        try:
            kwargs = dict(
                raw_tour=raw_tour,
                raw_others=raw_others,
                generation_token=f"benchmark-{index}",
                rules_fingerprint="benchmark-rules",
                export_schema_version="benchmark-schema",
                cache_root=cache_root,
                reference_builder=_reference_builder,
                facts_builder=_facts_builder,
                writer=_write_dashboard_facts,
                worker_count=worker_count,
            )
            # First run establishes the trusted reference.  The second run
            # deliberately reuses the exact identity to measure the real HIT
            # path instead of inferring it from a cold-run timing.
            result = build_fast_export_job_from_facts(**kwargs)
            manifest = load_ready_export_manifest(result.manifest_path) if result.manifest_path else None
            if result.status != "READY" or manifest is None:
                raise RuntimeError(result.fallback_reason or "fast export benchmark did not reach READY")
            first_run = {
                "status": result.status,
                "timings": dict(result.timings),
                "serialization_ms": dict(manifest.telemetry.get("serialization_ms") or {}),
                "package_ms": int(manifest.telemetry.get("package_ms", 0)),
                "artifact_bytes": {key: item.size for key, item in manifest.artifacts.items()},
                "equivalence_status": manifest.equivalence_status,
                "reference_status": str((manifest.reference or {}).get("status", "UNKNOWN")),
                "deep_diff_skipped": bool((manifest.reference or {}).get("deep_diff_skipped", False)),
            }
            fast_runs.append(first_run)

            hit_started = time.perf_counter()
            hit_result = build_fast_export_job_from_facts(**kwargs)
            hit_elapsed_ms = round((time.perf_counter() - hit_started) * 1000)
            hit_manifest = load_ready_export_manifest(hit_result.manifest_path) if hit_result.manifest_path else None
            if hit_result.status != "READY" or hit_manifest is None:
                raise RuntimeError(hit_result.fallback_reason or "trusted reference cache-hit benchmark did not reach READY")
            cache_scenarios.append({
                "scenario": "same_identity_cache_hit",
                "status": hit_result.status,
                "elapsed_ms": hit_elapsed_ms,
                "reference_status": str((hit_manifest.reference or {}).get("status", "UNKNOWN")),
                "deep_diff_skipped": bool((hit_manifest.reference or {}).get("deep_diff_skipped", False)),
                "equivalence_status": hit_manifest.equivalence_status,
                "reference_lookup_ms": int(hit_result.timings.get("reference_lookup_ms", 0)),
                "equivalence_digest_ms": int(hit_result.timings.get("equivalence_digest_ms", 0)),
                "equivalence_deep_diff_ms": int(hit_result.timings.get("equivalence_deep_diff_ms", 0)),
            })
            stale_result = build_fast_export_job_from_facts(
                **dict(kwargs, rules_fingerprint="benchmark-rules-stale")
            )
            stale_manifest = load_ready_export_manifest(stale_result.manifest_path) if stale_result.manifest_path else None
            if stale_result.status != "READY" or stale_manifest is None:
                raise RuntimeError(stale_result.fallback_reason or "stale identity benchmark did not reach READY")
            cache_scenarios.append({
                "scenario": "stale_identity_materialization",
                "status": stale_result.status,
                "elapsed_ms": int(stale_result.timings.get("total_ms", 0)),
                "reference_status": str((stale_manifest.reference or {}).get("status", "UNKNOWN")),
                "deep_diff_skipped": bool((stale_manifest.reference or {}).get("deep_diff_skipped", False)),
                "equivalence_status": stale_manifest.equivalence_status,
                "reference_lookup_ms": int(stale_result.timings.get("reference_lookup_ms", 0)),
                "equivalence_digest_ms": int(stale_result.timings.get("equivalence_digest_ms", 0)),
                "equivalence_deep_diff_ms": int(stale_result.timings.get("equivalence_deep_diff_ms", 0)),
            })
        finally:
            shutil.rmtree(cache_root, ignore_errors=True)
    last = fast_runs[-1]
    fast_timings = last["timings"]
    cache_hit_scenario = next(
        (item for item in cache_scenarios if item["scenario"] == "same_identity_cache_hit"),
        None,
    )
    return {
        "schema_version": "data-export-serialization-benchmark-v1",
        "database_mutated": False,
        "formal_scope": "不含掛賬核銷與TT退款轉團款",
        "sample_count": samples,
        "worker_count": worker_count,
        "equivalence_status": "PASS" if all(run["equivalence_status"] == "PASS" for run in fast_runs) else "FAIL",
        "legacy": {
            "serialization_ms": legacy_measurement.timings["serialization_ms"],
            "total_ms": legacy_measurement.timings["total_ms"],
            "artifact_bytes": {key: item.bytes_written for key, item in legacy_measurement.artifacts.items()},
        },
        "fast": {
            "serialization_ms": last["serialization_ms"],
            "package_ms": last["package_ms"],
            "total_ms": fast_timings["total_ms"],
            "reference_lookup_ms": int(fast_timings.get("reference_lookup_ms", 0)),
            "reference_materialize_ms": int(
                fast_timings.get("reference_materialize_ms", fast_timings.get("reference_ms", 0))
            ),
            "equivalence_digest_ms": int(fast_timings.get("equivalence_digest_ms", 0)),
            "equivalence_deep_diff_ms": int(
                fast_timings.get("equivalence_deep_diff_ms", fast_timings.get("equivalence_ms", 0))
            ),
            "cache_hit_ms": int(cache_hit_scenario["elapsed_ms"] if cache_hit_scenario else 0),
            "reference_status": last.get("reference_status", "UNKNOWN"),
            "deep_diff_skipped": last.get("deep_diff_skipped", False),
            "artifact_bytes": last["artifact_bytes"],
            "runs": fast_runs,
            "scenarios": cache_scenarios,
        },
    }


def build_reference_benchmark_report(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    worker_count: int = 3,
) -> dict[str, Any]:
    """Return bounded named scenarios for the trusted-reference rollout gate."""
    report = build_benchmark_report(
        raw_tour, raw_others, samples=1, worker_count=worker_count
    )
    scenarios = report["fast"].get("scenarios", [])
    first = report["fast"]["runs"][0]
    hit = next(item for item in scenarios if item["scenario"] == "same_identity_cache_hit")
    stale = next(item for item in scenarios if item["scenario"] == "stale_identity_materialization")
    return {
        "schema_version": "trusted-reference-benchmark-v1",
        "database_mutated": report["database_mutated"],
        "equivalence_status": report["equivalence_status"],
        "first_materialization": {
            "reference_status": first["reference_status"],
            "deep_diff_skipped": first["deep_diff_skipped"],
            "equivalence_status": first["equivalence_status"],
            "total_ms": first["timings"]["total_ms"],
        },
        "same_identity_hit": hit,
        "stale_identity": stale,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from database import load_all_data_from_db

    tour, others = load_all_data_from_db()
    report = build_benchmark_report(tour, others, samples=args.samples, worker_count=args.workers)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["equivalence_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
