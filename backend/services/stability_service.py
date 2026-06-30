from __future__ import annotations

from collections.abc import Callable

from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL

PHASE2B_BASELINE_MONTH = "2026-05"
PHASE2B_EXPECTED_TOTAL = 12_057_968.0
PHASE2B_EXPECTED_MAX_DATE = "2026-06-22"
PHASE2B_EXPECTED_ANALYSIS_ROWS = 26_640
PHASE2B_EXPECTED_EXCLUDED_ROWS = 545
PHASE2B_AMOUNT_TOLERANCE = 1.0
CORE_VALIDATION_KEYS = {"combinedRevenue", "revenueScope"}
FRESHNESS_UPDATE_KEYS = {"maxDate", "analysisRows", "excludedRows"}
PHASE2B_BASELINE_FILTERS = {
    "years": [2026],
    "months": [PHASE2B_BASELINE_MONTH],
    "dateRange": [f"{PHASE2B_BASELINE_MONTH}-01", f"{PHASE2B_BASELINE_MONTH}-31"],
    "branch": "全部分社",
    "salesGroup": "全部銷售組",
}


def _money_text(value: float) -> str:
    return f"HKD {float(value):,.0f}"


def _stability_check(key: str, label: str, expected, actual, matched: bool, delta=None, unit: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "expected": expected,
        "actual": actual,
        "delta": delta,
        "unit": unit,
        "status": "matched" if matched else "drift",
    }


def build_stability_baseline(revenue_totals: dict, data_freshness: dict) -> dict:
    actual_total = float(revenue_totals.get("combinedRevenue", 0.0))
    delta_amount = actual_total - PHASE2B_EXPECTED_TOTAL
    delta_pct = round(delta_amount / PHASE2B_EXPECTED_TOTAL * 100, 4) if PHASE2B_EXPECTED_TOTAL else 0.0
    amount_matched = abs(delta_amount) < PHASE2B_AMOUNT_TOLERANCE
    scope_actual = revenue_totals.get("scope") or data_freshness.get("scope")
    max_date_actual = data_freshness.get("maxDate")
    analysis_rows_actual = int(data_freshness.get("analysisRows", 0))
    excluded_rows_actual = int(data_freshness.get("excludedRows", 0))

    core_checks = [
        _stability_check(
            "combinedRevenue",
            "2026-05 分社 + 專職總營收",
            _money_text(PHASE2B_EXPECTED_TOTAL),
            _money_text(actual_total),
            amount_matched,
            round(delta_amount, 2),
            "HKD",
        ),
        _stability_check(
            "revenueScope",
            "正式口徑",
            REVENUE_SCOPE_LABEL,
            scope_actual,
            scope_actual == REVENUE_SCOPE_LABEL,
        ),
    ]
    freshness_checks = [
        _stability_check(
            "maxDate",
            "最新收款日期",
            PHASE2B_EXPECTED_MAX_DATE,
            max_date_actual,
            max_date_actual == PHASE2B_EXPECTED_MAX_DATE,
        ),
        _stability_check(
            "analysisRows",
            "正式口徑筆數",
            PHASE2B_EXPECTED_ANALYSIS_ROWS,
            analysis_rows_actual,
            analysis_rows_actual == PHASE2B_EXPECTED_ANALYSIS_ROWS,
            analysis_rows_actual - PHASE2B_EXPECTED_ANALYSIS_ROWS,
            "rows",
        ),
        _stability_check(
            "excludedRows",
            "排除明細筆數",
            PHASE2B_EXPECTED_EXCLUDED_ROWS,
            excluded_rows_actual,
            excluded_rows_actual == PHASE2B_EXPECTED_EXCLUDED_ROWS,
            excluded_rows_actual - PHASE2B_EXPECTED_EXCLUDED_ROWS,
            "rows",
        ),
    ]
    for check in freshness_checks:
        if check["status"] == "drift":
            check["status"] = "updated"

    core_matched_count = sum(1 for check in core_checks if check["status"] == "matched")
    core_drift_count = len(core_checks) - core_matched_count
    freshness_stable_count = sum(1 for check in freshness_checks if check["status"] == "matched")
    freshness_updated_count = len(freshness_checks) - freshness_stable_count
    core_summary = {
        "totalChecks": len(core_checks),
        "matchedChecks": core_matched_count,
        "driftChecks": core_drift_count,
    }
    freshness_summary = {
        "totalChecks": len(freshness_checks),
        "stableChecks": freshness_stable_count,
        "updatedChecks": freshness_updated_count,
    }
    checks = core_checks + freshness_checks
    return {
        "name": "Phase 2B Stability Baseline",
        "baselineMonth": PHASE2B_BASELINE_MONTH,
        "status": "matched" if core_drift_count == 0 else "drift",
        "formattedExpectedTotal": _money_text(PHASE2B_EXPECTED_TOTAL),
        "formattedActualTotal": _money_text(actual_total),
        "expectedTotal": float(PHASE2B_EXPECTED_TOTAL),
        "actualTotal": actual_total,
        "deltaAmount": round(delta_amount, 2),
        "deltaPct": delta_pct,
        "summary": core_summary,
        "coreValidation": {
            "status": "matched" if core_drift_count == 0 else "drift",
            "summary": core_summary,
            "checks": core_checks,
        },
        "freshnessUpdate": {
            "status": "updated" if freshness_updated_count else "stable",
            "summary": freshness_summary,
            "checks": freshness_checks,
        },
        "checks": checks,
    }


def build_phase2c_stability_gate(summary_builder: Callable[[dict], dict] | None = None) -> dict:
    if summary_builder is None:
        from backend.services.dashboard_service import build_dashboard_summary

        summary_builder = build_dashboard_summary

    summary = summary_builder(dict(PHASE2B_BASELINE_FILTERS))
    stability = summary["stabilityBaseline"]
    core_validation = stability.get("coreValidation") or {
        "status": stability.get("status", "drift"),
        "summary": stability.get("summary", {}),
        "checks": [
            check for check in stability.get("checks", []) if check.get("key") in CORE_VALIDATION_KEYS
        ],
    }
    freshness_update = stability.get("freshnessUpdate") or {
        "status": "stable",
        "summary": {"totalChecks": 0, "stableChecks": 0, "updatedChecks": 0},
        "checks": [
            check for check in stability.get("checks", []) if check.get("key") in FRESHNESS_UPDATE_KEYS
        ],
    }
    gate_summary = core_validation.get("summary", {})
    total_checks = int(gate_summary.get("totalChecks", 0))
    matched_checks = int(gate_summary.get("matchedChecks", 0))
    drift_checks = int(gate_summary.get("driftChecks", 0))
    drift_items = [check for check in core_validation.get("checks", []) if check.get("status") == "drift"]
    freshness_summary = freshness_update.get("summary", {})
    freshness_update_count = int(freshness_summary.get("updatedChecks", 0))
    freshness_updates = [
        check for check in freshness_update.get("checks", []) if check.get("status") == "updated"
    ]
    status = core_validation.get("status", stability.get("status", "drift"))
    if status == "matched":
        suffix = f"；資料已更新 {freshness_update_count} 項。" if freshness_update_count else "。"
        message = f"重建成功，核心口徑穩定：{matched_checks}/{total_checks} checks matched{suffix}"
    else:
        message = f"重建完成，但核心口徑出現漂移：{drift_checks}/{total_checks} checks drift。"

    return {
        "label": "Phase 2C Upload Rebuild Stability Gate",
        "status": status,
        "message": message,
        "baselineMonth": stability.get("baselineMonth"),
        "formattedExpectedTotal": stability.get("formattedExpectedTotal"),
        "formattedActualTotal": stability.get("formattedActualTotal"),
        "deltaAmount": stability.get("deltaAmount"),
        "deltaPct": stability.get("deltaPct"),
        "totalChecks": total_checks,
        "matchedChecks": matched_checks,
        "driftCheckCount": drift_checks,
        "driftChecks": drift_items,
        "coreValidation": core_validation,
        "freshnessStatus": freshness_update.get("status", "stable"),
        "freshnessUpdateCount": freshness_update_count,
        "freshnessUpdates": freshness_updates,
        "freshnessUpdate": freshness_update,
        "stabilityBaseline": stability,
    }
