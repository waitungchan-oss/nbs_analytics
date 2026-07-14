from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median
from time import perf_counter

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app


ENDPOINT = "/api/decisions/overview"


def build_profile_report(
    *,
    cold_seconds: float,
    warm_seconds: list[float],
    warm_limit_ms: float,
) -> dict:
    warm_samples_ms = [round(value * 1000, 3) for value in warm_seconds]
    warm_median_ms = round(median(warm_samples_ms), 3)
    return {
        "status": "passed" if warm_median_ms <= float(warm_limit_ms) else "failed",
        "endpoint": ENDPOINT,
        "coldMs": round(cold_seconds * 1000, 3),
        "warmSamplesMs": warm_samples_ms,
        "warmMedianMs": warm_median_ms,
        "warmLimitMs": float(warm_limit_ms),
    }


def exit_code_for_report(report: dict) -> int:
    return 0 if report.get("status") == "passed" else 1


def _timed_request(client: TestClient) -> tuple[float, dict]:
    started = perf_counter()
    response = client.get(ENDPOINT)
    elapsed = perf_counter() - started
    response.raise_for_status()
    return elapsed, response.json()


def profile_decision_api(*, runs: int, warm_limit_ms: float) -> dict:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    with TestClient(create_app()) as client:
        cold_seconds, _ = _timed_request(client)
        warm_results = [_timed_request(client) for _ in range(runs)]
    warm_seconds = [elapsed for elapsed, _ in warm_results]
    report = build_profile_report(
        cold_seconds=cold_seconds,
        warm_seconds=warm_seconds,
        warm_limit_ms=warm_limit_ms,
    )
    report["runs"] = runs
    report["provenance"] = warm_results[-1][1].get("provenance") or {}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile the NBS Decision API warm response time.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warm-limit-ms", type=float, default=300.0)
    args = parser.parse_args()
    report = profile_decision_api(runs=args.runs, warm_limit_ms=args.warm_limit_ms)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code_for_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
