"""Compare legacy and shared-facts Data Export serialization in isolation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
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
    for index in range(samples):
        cache_root = Path(tempfile.mkdtemp(prefix=f"nbs-export-benchmark-{index}-"))
        try:
            result = build_fast_export_job_from_facts(
                raw_tour,
                raw_others,
                generation_token=f"benchmark-{index}",
                rules_fingerprint="benchmark-rules",
                export_schema_version="benchmark-schema",
                cache_root=cache_root,
                reference_builder=_reference_builder,
                facts_builder=_facts_builder,
                writer=_write_dashboard_facts,
                worker_count=worker_count,
            )
            manifest = load_ready_export_manifest(result.manifest_path) if result.manifest_path else None
            if result.status != "READY" or manifest is None:
                raise RuntimeError(result.fallback_reason or "fast export benchmark did not reach READY")
            fast_runs.append({
                "status": result.status,
                "timings": dict(result.timings),
                "serialization_ms": dict(manifest.telemetry.get("serialization_ms") or {}),
                "package_ms": int(manifest.telemetry.get("package_ms", 0)),
                "artifact_bytes": {key: item.size for key, item in manifest.artifacts.items()},
                "equivalence_status": manifest.equivalence_status,
            })
        finally:
            shutil.rmtree(cache_root, ignore_errors=True)
    last = fast_runs[-1]
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
            "total_ms": last["timings"]["total_ms"],
            "artifact_bytes": last["artifact_bytes"],
            "runs": fast_runs,
        },
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
