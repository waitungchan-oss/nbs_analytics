"""Run an isolated production-like GMV rebuild benchmark matrix."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.gmv_production_benchmark_service import (  # noqa: E402
    FORMAL_SCOPE,
    FROZEN_BASELINE,
    build_benchmark_case,
    create_isolated_benchmark_fixture,
    evaluate_benchmark_gates,
    run_isolated_production_rebuild_benchmark,
)


def _ratios(raw: str) -> list[float]:
    values = [float(item.strip()) for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("ratios cannot be empty")
    if len(set(values)) != len(values):
        raise ValueError("ratios must be unique")
    return values


def _isolated_root(raw: str | None) -> Path:
    if raw is None:
        return Path(tempfile.mkdtemp(prefix="gmv-production-benchmark-"))
    path = Path(raw).expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if path == PROJECT_ROOT or ".nbs_runtime_cache" in {part.lower() for part in path.parts}:
        raise ValueError("output root must be isolated from formal runtime")
    if path != temp_root and temp_root not in path.parents:
        raise ValueError("output root must be under an isolated temporary directory")
    return path


def run_cli_benchmark(*, ratios: list[float], samples: int, warm_reads: int, output_root: Path) -> dict[str, object]:
    cases: dict[str, object] = {}
    requested = list(ratios)
    if 0.25 not in requested:
        requested.append(0.25)
    for ratio in requested:
        case = build_benchmark_case(
            affected_ratio=ratio, receipt_count=10_000,
            scenario_flags=("status_transition", "amount_change", "tt_method_transition", "over_refund", "multi_member", "unmatched"),
        )
        case_root = output_root / case.case_id
        try:
            fixture = create_isolated_benchmark_fixture(case, root=case_root / "fixture")
            summary = run_isolated_production_rebuild_benchmark(
                case, fixture=fixture, root=case_root / "runs",
                runs=samples, warm_reads=warm_reads,
            )
            gate = evaluate_benchmark_gates(summary)
            cases[case.case_id] = {
                "case": case.to_dict(),
                "summary": summary.to_dict(),
                "gate": gate.to_dict(),
                "status": gate.status,
            }
        except Exception as exc:
            cases[case.case_id] = {
                "case": case.to_dict(),
                "status": "FAIL",
                "failureReasons": [f"{type(exc).__name__}: {str(exc)[:180]}"],
            }
    statuses = {str(item["status"]) for item in cases.values()}
    overall = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    return {
        "schemaVersion": "gmv-production-rebuild-benchmark-v1",
        "formalScope": FORMAL_SCOPE,
        "frozenBaseline": FROZEN_BASELINE,
        "databaseMutated": False,
        "sampleCount": samples,
        "warmReadCount": warm_reads,
        "cases": dict(sorted(cases.items())),
        "status": overall,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", choices=("synthetic",), default="synthetic")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--warm-reads", type=int, default=3)
    parser.add_argument("--ratios", default="0.001,0.01,0.1")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    try:
        if args.samples < 3 or args.warm_reads < 3:
            raise ValueError("samples and warm-reads must be at least 3")
        report = run_cli_benchmark(
            ratios=_ratios(args.ratios), samples=args.samples,
            warm_reads=args.warm_reads, output_root=_isolated_root(args.output_root),
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
