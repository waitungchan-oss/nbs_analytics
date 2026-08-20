"""Read-only revenue data-preparation benchmark for the NBS dashboard hot path."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def summarize_samples(samples_seconds: list[float]) -> dict[str, float]:
    if not samples_seconds:
        raise ValueError("at least one benchmark sample is required")
    samples_ms = sorted(round(value * 1000.0, 3) for value in samples_seconds)
    p95_index = max(0, math.ceil(len(samples_ms) * 0.95) - 1)
    return {
        "medianMs": round(statistics.median(samples_ms), 3),
        "p95Ms": samples_ms[p95_index],
        "minMs": samples_ms[0],
        "maxMs": samples_ms[-1],
        "sampleCount": float(len(samples_ms)),
    }


def compare_benchmarks(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    absolute_limit_ms: float = 300.0,
    relative_limit: float = 0.05,
) -> dict[str, Any]:
    baseline_median = float(baseline["medianMs"])
    candidate_median = float(candidate["medianMs"])
    regression_ms = round(candidate_median - baseline_median, 3)
    regression_ratio = round(regression_ms / baseline_median, 6) if baseline_median else float("inf")
    return {
        "passed": regression_ms <= absolute_limit_ms and regression_ratio <= relative_limit,
        "baselineMedianMs": baseline_median,
        "candidateMedianMs": candidate_median,
        "regressionMs": regression_ms,
        "regressionRatio": regression_ratio,
        "absoluteLimitMs": absolute_limit_ms,
        "relativeLimit": relative_limit,
    }


def _db_metadata() -> dict[str, Any]:
    try:
        from database import resolve_db_path

        db_path = Path(resolve_db_path()).resolve()
        stat = db_path.stat()
        identity = hashlib.sha256(
            f"{db_path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()
        import sqlite3

        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            counts = {}
            for table in ("tour_data", "others_data"):
                try:
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.Error:
                    counts[table] = None
        return {"pathIdentitySha256": identity, "rowCounts": counts}
    except Exception as exc:  # pragma: no cover - only used for diagnostic output
        return {"pathIdentitySha256": None, "rowCounts": {}, "errorType": type(exc).__name__}


def _run_dashboard_data_prep() -> tuple[float, tuple[int, int]]:
    """Measure revenue data preparation without Streamlit UI or GMV repository."""
    from app_workflows import _build_revenue_scope_frames
    from database import load_all_data_from_db

    started = time.perf_counter()
    db_tour, db_others = load_all_data_from_db()
    formal_tour, formal_others, _ = _build_revenue_scope_frames(db_tour, db_others)
    elapsed = time.perf_counter() - started
    return elapsed, (len(formal_tour), len(formal_others))


def run_benchmark(*, app_path: Path, iterations: int) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("iterations must be positive")
    _run_dashboard_data_prep()
    samples: list[float] = []
    formal_row_counts: list[tuple[int, int]] = []
    for _ in range(iterations):
        elapsed, rows = _run_dashboard_data_prep()
        samples.append(elapsed)
        formal_row_counts.append(rows)
    report: dict[str, Any] = {
        "mode": "measurement",
        "entrypoint": str(app_path.resolve()),
        "measurement": "dashboard_data_preparation_without_workbook_or_gmv_repository",
        "python": sys.version.split()[0],
        "gitHead": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "appExceptionCount": 0,
        "samplesMs": [round(value * 1000.0, 3) for value in samples],
        "formalRowCounts": sorted(set(formal_row_counts)),
        **summarize_samples(samples),
        **_db_metadata(),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--app-path", type=Path, default=Path("app.py"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_benchmark(app_path=args.app_path, iterations=args.iterations)
    report["mode"] = args.mode
    if args.mode == "candidate":
        if args.baseline is None:
            raise SystemExit("--baseline is required for candidate mode")
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["comparison"] = compare_benchmarks(baseline=baseline, candidate=report)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.mode == "candidate" and not report["comparison"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
