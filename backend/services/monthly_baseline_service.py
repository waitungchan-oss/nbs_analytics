from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

import database

from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = PROJECT_ROOT / "data" / "monthly_revenue_baselines.json"
PROMOTION_TABLE = "monthly_baseline_promotion_history"


def _money_text(value: float) -> str:
    return f"HKD {float(value):,.0f}"


def load_monthly_baseline_registry(path: Path | None = None) -> dict:
    registry_path = Path(path or REGISTRY_FILE)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    required = {"version", "scope", "population", "amountTolerance", "requiredStableUploadCycles", "baselines"}
    missing = required - set(registry)
    if missing:
        raise ValueError(f"monthly baseline registry missing keys: {sorted(missing)}")
    months = [str(row.get("month") or "") for row in registry["baselines"]]
    if len(months) != len(set(months)) or not all(months):
        raise ValueError("monthly baseline registry months must be unique and non-empty")
    return registry


def evaluate_monthly_baselines(
    registry: dict | None = None,
    analytics_builder: Callable[[dict], dict] | None = None,
) -> dict:
    registry = deepcopy(registry or load_monthly_baseline_registry())
    if analytics_builder is None:
        from backend.services.dashboard_analytics_service import build_dashboard_analytics

        analytics_builder = build_dashboard_analytics

    months = [str(row["month"]) for row in registry["baselines"]]
    analytics = analytics_builder(
        {
            "years": [2026],
            "months": months,
            "dateRange": [f"{months[0]}-01", f"{months[-1]}-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )
    actual_by_month = {
        str(row.get("month")): row
        for row in analytics.get("monthlyTrend", [])
    }
    tolerance = float(registry.get("amountTolerance", 1.0))
    scope_actual = str(analytics.get("revenueScope") or "")
    scope_matched = scope_actual == str(registry.get("scope") or "") == REVENUE_SCOPE_LABEL
    checks: list[dict] = []
    for baseline in registry["baselines"]:
        month = str(baseline["month"])
        actual_row = actual_by_month.get(month, {})
        expected = float(baseline["expectedTotal"])
        actual = float(actual_row.get("combinedRevenue") or 0.0)
        delta = round(actual - expected, 2)
        matched = abs(actual - expected) < tolerance and scope_matched
        checks.append(
            {
                "key": f"monthlyRevenue:{month}",
                "month": month,
                "mode": str(baseline.get("mode") or "monitoring"),
                "legacyCore": bool(baseline.get("legacyCore", False)),
                "expectedTotal": expected,
                "displayTotal": int(baseline["displayTotal"]),
                "formattedExpectedTotal": _money_text(baseline["displayTotal"]),
                "actualTotal": actual,
                "formattedActualTotal": _money_text(actual),
                "branchRevenue": float(actual_row.get("branchRevenue") or 0.0),
                "specialistRevenue": float(actual_row.get("specialistRevenue") or 0.0),
                "deltaAmount": delta,
                "status": "matched" if matched else "drift",
            }
        )

    monitoring_checks = [row for row in checks if row["mode"] == "monitoring"]
    blocking_checks = [row for row in checks if row["mode"] == "blocking"]
    matched_count = sum(row["status"] == "matched" for row in checks)
    return {
        "registryVersion": str(registry["version"]),
        "scope": str(registry["scope"]),
        "population": str(registry["population"]),
        "amountTolerance": tolerance,
        "requiredStableUploadCycles": int(registry["requiredStableUploadCycles"]),
        "checks": checks,
        "monitoringChecks": monitoring_checks,
        "blockingChecks": blocking_checks,
        "matchedCount": int(matched_count),
        "totalChecks": len(checks),
        "allMatched": matched_count == len(checks),
        "blockingStatus": "matched" if all(row["status"] == "matched" for row in blocking_checks) else "drift",
    }


def apply_monthly_blocking_checks(gate: dict, evaluation: dict) -> dict:
    result = deepcopy(gate)
    result["monthlyBaseline"] = deepcopy(evaluation)
    injected = [
        {
            "key": row["key"],
            "label": f"{row['month']} 分社 + 專職總營收",
            "expected": row["formattedExpectedTotal"],
            "actual": row["formattedActualTotal"],
            "delta": row["deltaAmount"],
            "unit": "HKD",
            "status": row["status"],
        }
        for row in evaluation.get("blockingChecks", [])
        if not row.get("legacyCore")
    ]
    if not injected:
        return result

    core = deepcopy(result.get("coreValidation") or {})
    checks = list(core.get("checks") or []) + injected
    matched_count = sum(row.get("status") == "matched" for row in checks)
    drift_checks = [row for row in checks if row.get("status") == "drift"]
    summary = {
        "totalChecks": len(checks),
        "matchedChecks": matched_count,
        "driftChecks": len(drift_checks),
    }
    status = "matched" if not drift_checks else "drift"
    core.update({"status": status, "summary": summary, "checks": checks})
    result.update(
        {
            "status": status,
            "coreValidation": core,
            "matchedChecks": matched_count,
            "totalChecks": len(checks),
            "driftCheckCount": len(drift_checks),
            "driftChecks": drift_checks,
        }
    )
    if drift_checks:
        result["message"] = f"重建完成，但核心口徑出現漂移：{len(drift_checks)}/{len(checks)} checks drift。"
    return result


def build_governed_stability_gate(
    *,
    gate_builder: Callable[[], dict] | None = None,
    analytics_builder: Callable[[dict], dict] | None = None,
) -> dict:
    if gate_builder is None:
        from backend.services.stability_service import build_phase2c_stability_gate

        gate_builder = build_phase2c_stability_gate
    gate = gate_builder()
    evaluation = evaluate_monthly_baselines(analytics_builder=analytics_builder)
    return apply_monthly_blocking_checks(gate, evaluation)


def build_monthly_baseline_governance(
    evaluation: dict | None = None,
    history_records: list[dict] | None = None,
) -> dict:
    evaluation = deepcopy(evaluation or evaluate_monthly_baselines())
    if history_records is None:
        from backend.services.stability_history_service import list_stability_history

        history_records = list_stability_history(limit=100)

    required_cycles = int(evaluation.get("requiredStableUploadCycles") or 1)
    all_blocking = all(row.get("mode") == "blocking" for row in evaluation.get("checks", []))
    latest_eligible = None
    for record in history_records:
        monthly = record.get("monthlyBaseline") or {}
        if (
            record.get("uploadStatus") == "accepted"
            and record.get("rollbackStatus") == "not_required"
            and monthly.get("registryVersion") == evaluation.get("registryVersion")
        ):
            latest_eligible = record
            break

    record_matched = bool(latest_eligible and (latest_eligible.get("monthlyBaseline") or {}).get("allMatched"))
    stable_cycles = required_cycles if record_matched and evaluation.get("allMatched") else 0
    promotion_ready = not all_blocking and stable_cycles >= required_cycles
    if all_blocking:
        status = "blocking" if evaluation.get("blockingStatus") == "matched" else "drift"
    elif not evaluation.get("allMatched"):
        status = "drift"
    elif promotion_ready:
        status = "promotion_ready"
    else:
        status = "monitoring"

    return {
        **evaluation,
        "status": status,
        "stableUploadCycles": stable_cycles,
        "requiredStableUploadCycles": required_cycles,
        "promotionReady": promotion_ready,
        "eligibleRecordId": int(latest_eligible["id"]) if promotion_ready else None,
        "eligibleCreatedAt": latest_eligible.get("createdAt") if promotion_ready else None,
    }


def _ensure_promotion_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROMOTION_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            registry_version TEXT NOT NULL,
            upload_record_id INTEGER NOT NULL,
            old_modes_json TEXT NOT NULL,
            new_modes_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            backup_path TEXT NOT NULL
        )
        """
    )


def _record_promotion_event(
    *,
    registry_version: str,
    upload_record_id: int,
    old_modes: dict,
    new_modes: dict,
    snapshot: dict,
    backup_path: str,
) -> int:
    conn = database.get_db_connection()
    try:
        _ensure_promotion_table(conn)
        cursor = conn.execute(
            f"""
            INSERT INTO {PROMOTION_TABLE} (
                created_at, registry_version, upload_record_id,
                old_modes_json, new_modes_json, snapshot_json, backup_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                registry_version,
                int(upload_record_id),
                json.dumps(old_modes, ensure_ascii=False),
                json.dumps(new_modes, ensure_ascii=False),
                json.dumps(snapshot, ensure_ascii=False, default=str),
                backup_path,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def list_monthly_baseline_promotions(limit: int = 20) -> list[dict]:
    bounded_limit = max(1, min(int(limit), 100))
    conn = database.get_db_connection()
    try:
        table_exists = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (PROMOTION_TABLE,),
        ).fetchone()[0]
        if not table_exists:
            return []
        conn.row_factory = database.sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {PROMOTION_TABLE} ORDER BY id DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "createdAt": row["created_at"],
            "registryVersion": row["registry_version"],
            "uploadRecordId": int(row["upload_record_id"]),
            "oldModes": json.loads(row["old_modes_json"]),
            "newModes": json.loads(row["new_modes_json"]),
            "snapshot": json.loads(row["snapshot_json"]),
            "backupPath": row["backup_path"],
        }
        for row in rows
    ]


def promote_monthly_baselines(
    *,
    confirmed: bool,
    expected_record_id: int,
    registry_path: Path | None = None,
) -> dict:
    if not confirmed:
        raise ValueError("promotion confirmation is required")

    path = Path(registry_path or REGISTRY_FILE)
    registry = load_monthly_baseline_registry(path)
    evaluation = evaluate_monthly_baselines(registry)
    governance = build_monthly_baseline_governance(evaluation=evaluation)
    if not governance.get("promotionReady") or not evaluation.get("allMatched"):
        raise ValueError("monthly baseline promotion is not ready")
    if int(governance.get("eligibleRecordId") or 0) != int(expected_record_id):
        raise ValueError("stale upload record; refresh governance before promotion")

    old_modes = {str(row["month"]): str(row["mode"]) for row in registry["baselines"]}
    promoted_months = [str(row["month"]) for row in registry["baselines"] if row.get("mode") == "monitoring"]
    updated = deepcopy(registry)
    for row in updated["baselines"]:
        if row.get("mode") == "monitoring":
            row["mode"] = "blocking"
    new_modes = {str(row["month"]): str(row["mode"]) for row in updated["baselines"]}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = path.with_name(f"{path.name}.backup_{timestamp}")
    temp_path = path.with_name(f".{path.name}.tmp_{timestamp}")
    shutil.copy2(path, backup_path)
    try:
        temp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, path)
        event_id = _record_promotion_event(
            registry_version=str(registry["version"]),
            upload_record_id=int(expected_record_id),
            old_modes=old_modes,
            new_modes=new_modes,
            snapshot=evaluation,
            backup_path=str(backup_path),
        )
        verified = load_monthly_baseline_registry(path)
        if any(row.get("mode") != "blocking" for row in verified["baselines"]):
            raise RuntimeError("monthly baseline promotion verification failed")
    except Exception:
        shutil.copy2(backup_path, path)
        if temp_path.exists():
            temp_path.unlink()
        raise

    return {
        "status": "promoted",
        "registryVersion": str(registry["version"]),
        "promotedMonths": promoted_months,
        "uploadRecordId": int(expected_record_id),
        "promotionEventId": event_id,
        "backupPath": str(backup_path),
    }
