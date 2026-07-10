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


def build_monthly_baseline_report() -> dict:
    evaluation = evaluate_monthly_baselines()
    governance = build_monthly_baseline_governance(evaluation=evaluation)
    promotions = list_monthly_baseline_promotions(limit=1)
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    report = build_monthly_baseline_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("blockingStatus") == "drift" else 0


if __name__ == "__main__":
    raise SystemExit(main())
