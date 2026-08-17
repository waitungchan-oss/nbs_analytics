from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.dashboard_service import build_dashboard_summary
from backend.services.stability_service import PHASE2B_BASELINE_FILTERS
from backend.services.verification_runtime_paths import load_verification_runtime_profile


def check_phase2_baseline(db_path: Path) -> dict:
    summary = build_dashboard_summary(dict(PHASE2B_BASELINE_FILTERS), db_path=db_path)
    stability = summary["stabilityBaseline"]
    return {
        "status": stability["status"],
        "baselineMonth": stability["baselineMonth"],
        "formattedExpectedTotal": stability["formattedExpectedTotal"],
        "formattedActualTotal": stability["formattedActualTotal"],
        "deltaAmount": stability["deltaAmount"],
        "checks": stability["coreValidation"]["checks"],
        "latestDataDate": summary["dataFreshness"]["maxDate"],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the Phase 2J frozen baseline against a SQLite database.")
    parser.add_argument(
        "--db",
        default="nbs_marketing_data.db",
        help="SQLite database path to inspect. Defaults to nbs_marketing_data.db in the current working directory.",
    )
    parser.add_argument("--verification-profile", help="Profile JSON under the ignored verification runtime.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verification_profile:
        project_root = Path(__file__).resolve().parents[1]
        profile_path = Path(args.verification_profile)
        if not profile_path.is_absolute():
            profile_path = project_root / profile_path
        _, paths = load_verification_runtime_profile(profile_path, project_root=project_root)
        db_path = paths.db_path
    else:
        db_path = Path(args.db)
    result = check_phase2_baseline(db_path)
    payload = {"dbPath": str(db_path), **result}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
