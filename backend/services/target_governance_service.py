from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "decision_targets.json"
DEFAULT_HISTORY_PATH = PROJECT_ROOT / ".nbs_runtime" / "decision_targets_history.jsonl"
FORMAL_POPULATION = "全部正式分社＋正式四人專職銷售組"
DEFAULT_THRESHOLDS = {
    "forecastGapPct": 0.05,
    "qualityWarningScore": 75.0,
    "qualityCriticalScore": 60.0,
}
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class TargetConfigValidationError(ValueError):
    pass


def _empty_config(path: Path) -> dict[str, Any]:
    return {
        "status": "not_configured",
        "revision": 0,
        "version": None,
        "scope": REVENUE_SCOPE_LABEL,
        "population": FORMAL_POPULATION,
        "approvalStatus": "draft",
        "updatedBy": None,
        "changeReason": None,
        "approvedBy": None,
        "approvedAt": None,
        "updatedAt": None,
        "source": str(path),
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "targets": [],
    }


def load_target_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return _empty_config(config_path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        normalized = validate_target_config(payload)
    except (OSError, json.JSONDecodeError, TargetConfigValidationError) as exc:
        result = _empty_config(config_path)
        result["status"] = "invalid"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    normalized["status"] = normalized["approvalStatus"] if normalized["targets"] else "not_configured"
    normalized["source"] = str(config_path)
    return normalized


def validate_target_config(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TargetConfigValidationError("target config must be an object")
    if str(payload.get("scope") or "") != REVENUE_SCOPE_LABEL:
        raise TargetConfigValidationError("scope must match the formal revenue scope")
    if str(payload.get("population") or "") != FORMAL_POPULATION:
        raise TargetConfigValidationError("population must match the formal population")
    version = str(payload.get("version") or "").strip()
    updated_by = str(payload.get("updatedBy") or "").strip()
    change_reason = str(payload.get("changeReason") or "").strip()
    if not version:
        raise TargetConfigValidationError("version is required")
    if not updated_by:
        raise TargetConfigValidationError("updatedBy is required")
    if not change_reason:
        raise TargetConfigValidationError("changeReason is required")

    approval_status = str(payload.get("approvalStatus") or "draft")
    if approval_status not in {"draft", "approved"}:
        raise TargetConfigValidationError("approvalStatus must be draft or approved")
    approved_by = str(payload.get("approvedBy") or "").strip() or None
    if approval_status == "approved" and not approved_by:
        raise TargetConfigValidationError("approvedBy is required for approved config")

    raw_thresholds = payload.get("thresholds") or {}
    thresholds = {**DEFAULT_THRESHOLDS, **raw_thresholds}
    try:
        thresholds = {
            "forecastGapPct": float(thresholds["forecastGapPct"]),
            "qualityWarningScore": float(thresholds["qualityWarningScore"]),
            "qualityCriticalScore": float(thresholds["qualityCriticalScore"]),
        }
    except (TypeError, ValueError, KeyError) as exc:
        raise TargetConfigValidationError("thresholds must contain numeric values") from exc
    if not 0 < thresholds["forecastGapPct"] <= 0.5:
        raise TargetConfigValidationError("forecastGapPct must be greater than 0 and no more than 0.5")
    if not 0 <= thresholds["qualityCriticalScore"] < thresholds["qualityWarningScore"] <= 100:
        raise TargetConfigValidationError("quality thresholds must satisfy 0 <= critical < warning <= 100")

    raw_targets = payload.get("targets") or []
    if not isinstance(raw_targets, list):
        raise TargetConfigValidationError("targets must be a list")
    if approval_status == "approved" and not raw_targets:
        raise TargetConfigValidationError("approved config must contain at least one target")
    targets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_months: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, dict):
            raise TargetConfigValidationError("each target must be an object")
        target_id = str(item.get("id") or "").strip()
        month = str(item.get("month") or "").strip()
        label = str(item.get("label") or "").strip()
        scope = str(item.get("scope") or "").strip()
        try:
            amount = float(item.get("targetRevenue"))
        except (TypeError, ValueError) as exc:
            raise TargetConfigValidationError("targetRevenue must be numeric") from exc
        if not target_id or target_id in seen_ids:
            raise TargetConfigValidationError("target ids must be unique and non-empty")
        if not MONTH_PATTERN.match(month) or month in seen_months:
            raise TargetConfigValidationError("target months must be unique and use YYYY-MM")
        if not label:
            raise TargetConfigValidationError("target label is required")
        if scope != "combined":
            raise TargetConfigValidationError("only combined target scope is supported in P2-6")
        if amount <= 0:
            raise TargetConfigValidationError("targetRevenue must be greater than 0")
        seen_ids.add(target_id)
        seen_months.add(month)
        targets.append({
            "id": target_id,
            "label": label,
            "month": month,
            "scope": scope,
            "targetRevenue": amount,
        })

    return {
        "revision": int(payload.get("revision") or 0),
        "version": version,
        "scope": REVENUE_SCOPE_LABEL,
        "population": FORMAL_POPULATION,
        "approvalStatus": approval_status,
        "updatedBy": updated_by,
        "changeReason": change_reason,
        "approvedBy": approved_by,
        "approvedAt": payload.get("approvedAt"),
        "updatedAt": payload.get("updatedAt"),
        "thresholds": thresholds,
        "targets": targets,
    }


def load_target_history(path: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    history_path = Path(path or DEFAULT_HISTORY_PATH)
    if not history_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 100)):]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return list(reversed(rows))


def save_target_config(
    payload: dict[str, Any],
    *,
    config_path: str | Path | None = None,
    history_path: str | Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    target_path = Path(config_path or DEFAULT_CONFIG_PATH)
    audit_path = Path(history_path or DEFAULT_HISTORY_PATH)
    normalized = validate_target_config(payload)
    current = load_target_config(target_path)
    revision = int(current.get("revision") or 0) + 1
    timestamp = now or __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
    normalized["revision"] = revision
    normalized["updatedAt"] = timestamp
    normalized["approvedAt"] = timestamp if normalized["approvalStatus"] == "approved" else None
    normalized["status"] = normalized["approvalStatus"] if normalized["targets"] else "not_configured"
    normalized["source"] = str(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_suffix(f"{target_path.suffix}.tmp")
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target_path)

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    history_row = {
        "event": "target_config_updated",
        "revision": revision,
        "createdAt": timestamp,
        "approvalStatus": normalized["approvalStatus"],
        "updatedBy": normalized["updatedBy"],
        "changeReason": normalized["changeReason"],
        "targetCount": len(normalized["targets"]),
        "configPath": str(target_path),
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(history_row, ensure_ascii=False) + "\n")
    return normalized
