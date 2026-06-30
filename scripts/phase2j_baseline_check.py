from __future__ import annotations

from pathlib import Path

import database
from backend.services.dashboard_service import build_dashboard_summary
from backend.services.stability_service import PHASE2B_BASELINE_FILTERS


def check_phase2_baseline(db_path: Path) -> dict:
    original_db_file = database.DB_FILE
    try:
        database.DB_FILE = str(db_path)
        summary = build_dashboard_summary(dict(PHASE2B_BASELINE_FILTERS))
    finally:
        database.DB_FILE = original_db_file
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

