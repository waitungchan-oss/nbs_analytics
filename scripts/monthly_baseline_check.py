from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.monthly_baseline_service import (
    build_monthly_baseline_governance,
    evaluate_monthly_baselines,
    list_monthly_baseline_promotions,
)
from backend.services.verification_runtime_paths import load_verification_runtime_profile


def build_monthly_baseline_report(*, db_path=None, verification_mode: bool = False) -> dict:
    evaluation = evaluate_monthly_baselines(db_path=db_path)
    if not verification_mode:
        governance = build_monthly_baseline_governance(evaluation=evaluation)
        promotions = list_monthly_baseline_promotions(limit=1)
    else:
        governance = build_monthly_baseline_governance(evaluation=evaluation, history_records=[])
        promotions = []
    return {
        "status": governance.get("status"),
        "blockingStatus": governance.get("blockingStatus"),
        "registryVersion": governance.get("registryVersion"),
        "scope": governance.get("scope"),
        "population": governance.get("population"),
        "checks": governance.get("checks") or [],
        "stableUploadCycles": governance.get("stableUploadCycles", 0),
        "requiredStableUploadCycles": governance.get("requiredStableUploadCycles", 1),
        "promotionReady": bool(governance.get("promotionReady")),
        "eligibleRecordId": governance.get("eligibleRecordId"),
        "latestPromotion": promotions[0] if promotions else None,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect monthly revenue baseline governance.")
    parser.add_argument("--verification-profile", help="Profile JSON under the ignored verification runtime.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = None
    if args.verification_profile:
        project_root = Path(__file__).resolve().parents[1]
        profile_path = Path(args.verification_profile)
        if not profile_path.is_absolute():
            profile_path = project_root / profile_path
        _, paths = load_verification_runtime_profile(profile_path, project_root=project_root)
        db_path = paths.db_path
    report = build_monthly_baseline_report() if db_path is None else build_monthly_baseline_report(db_path=db_path, verification_mode=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("blockingStatus") == "drift" else 0


if __name__ == "__main__":
    raise SystemExit(main())
