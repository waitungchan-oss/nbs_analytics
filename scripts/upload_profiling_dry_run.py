from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.upload_profiling_service import run_virtual_upload_profiling_dry_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a virtual upload profiling dry-run against a temp DB copy.")
    parser.add_argument("--rows", type=int, default=25, help="Number of virtual receipt rows to generate.")
    parser.add_argument(
        "--include-drift-diagnosis",
        action="store_true",
        help="Run the full row-level drift diagnosis. This can be slow on the live DB copy.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_virtual_upload_profiling_dry_run(
        row_count=args.rows,
        skip_drift_diagnosis=not args.include_drift_diagnosis,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Dry run: {report['dryRun']}")
        print(f"Live DB unchanged: {report['liveDbUnchanged']}")
        print(f"Preflight: {report['preflightStatus']}")
        print(f"Drift diagnosis: {report['driftDiagnosisMode']}")
        print(f"Stability: {report['stabilityStatus']} ({report.get('formattedActualTotal')})")
        print(f"Rollback: {report['rollbackStatus']}")
        print(f"Temp DB: {report['tempDbPath']}")
        print("\nStage timings:")
        for item in report["stageTimings"]:
            print(f"- {item['階段']}: {item['秒數']}s")
    return 0 if report["liveDbUnchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
