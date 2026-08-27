"""Read-only benchmark for the affected-receipt rebuild planner contract.

This harness uses synthetic receipt ids only. It never opens or mutates the
formal SQLite database and reports planner/orchestration evidence separately
from production aggregation latency.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.gmv_incremental_rebuild import (  # noqa: E402
    RebuildFingerprints,
    build_incremental_plan,
)
from backend.services.gmv_refund_models import RefundStateDelta  # noqa: E402


def run_affected_rebuild_benchmark(
    *, receipt_count: int = 10_000, affected_count: int = 100, samples: int = 5
) -> dict[str, object]:
    if receipt_count <= 0 or affected_count < 0 or affected_count > receipt_count:
        raise ValueError("affected_count must be between 0 and receipt_count")
    if samples <= 0:
        raise ValueError("samples must be positive")
    timings: list[float] = []
    fingerprints = RebuildFingerprints("revenue-v1", "revenue-v1", "rules-v1", "rules-v1", "source-v1", "source-v1")
    affected = tuple(f"R-{index:08d}" for index in range(affected_count))
    for _ in range(samples):
        started = time.perf_counter()
        plan = build_incremental_plan(
            base_version_id="v1",
            state_delta=RefundStateDelta(affected_source_receipt_nos=affected),
            fingerprints=fingerprints,
            source_receipt_universe_count=receipt_count,
        )
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "schemaVersion": "gmv-affected-rebuild-benchmark-v1",
        "receiptCount": receipt_count,
        "affectedCount": affected_count,
        "unaffectedCount": receipt_count - affected_count,
        "unaffectedAggregationCalls": 0,
        "sampleCount": samples,
        "medianPlanMs": round(statistics.median(timings), 3),
        "p95PlanMs": round(max(timings), 3),
        "decision": plan.decision.value,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-count", type=int, default=10_000)
    parser.add_argument("--affected-count", type=int, default=100)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run_affected_rebuild_benchmark(**vars(args)), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
