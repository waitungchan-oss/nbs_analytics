from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.services.target_governance_service import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_THRESHOLDS,
    load_target_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET_CONFIG_PATH = DEFAULT_CONFIG_PATH
PUBLIC_SNAPSHOT_PROVENANCE_KEYS = (
    "rulesFingerprint",
    "snapshotAttemptCount",
    "coreGenerationConsistent",
)


def load_decision_targets(path: str | Path | None = None) -> dict[str, Any]:
    return load_target_config(path or DEFAULT_TARGET_CONFIG_PATH)


def _severity_from_gap(gap_pct: float, threshold: float) -> str:
    if gap_pct <= -max(threshold * 2, 0.1):
        return "critical"
    if gap_pct <= -threshold:
        return "warning"
    return "info"


def _alert(code: str, severity: str, title: str, summary: str, recommendation: str, evidence: dict) -> dict:
    return {
        "id": code,
        "code": code,
        "severity": severity,
        "title": title,
        "summary": summary,
        "recommendation": recommendation,
        "evidence": evidence,
        "status": "open",
    }


def _target_actual(monthly_totals: list[dict], month: str) -> float | None:
    row = next((item for item in monthly_totals if str(item.get("month")) == month), None)
    if row is None:
        return None
    return float(row.get("combinedRevenue") or 0)


def build_decision_overview(
    *,
    facts: dict,
    forecast: dict,
    quality: dict,
    health: dict,
    target_config: dict | None = None,
    snapshot_provenance: dict | None = None,
) -> dict:
    config = target_config or load_decision_targets()
    public_snapshot_provenance = {
        key: snapshot_provenance[key]
        for key in PUBLIC_SNAPSHOT_PROVENANCE_KEYS
        if snapshot_provenance and key in snapshot_provenance
    }
    thresholds = {**DEFAULT_THRESHOLDS, **(config.get("thresholds") or {})}
    config_status = config.get("status") or ("configured" if config.get("targets") else "not_configured")
    active_targets = config.get("targets") if config.get("approvalStatus") == "approved" else []
    monthly_totals = facts.get("monthlyTotals") or []
    month_end = forecast.get("monthEnd") or {}
    targets: list[dict] = []
    alerts: list[dict] = []

    for raw_target in active_targets or []:
        month = str(raw_target.get("month") or "")
        target_revenue = float(raw_target.get("targetRevenue") or 0)
        actual = _target_actual(monthly_totals, month)
        forecasted = None
        if str(month_end.get("month") or "") == month:
            forecasted = float(month_end.get("consensus") or 0)
        gap = None if actual is None else round(actual - target_revenue, 2)
        attainment = None if actual is None or not target_revenue else round(actual / target_revenue * 100, 2)
        projected_gap = None if forecasted is None else round(forecasted - target_revenue, 2)
        projected_gap_pct = None if forecasted is None or not target_revenue else (forecasted - target_revenue) / target_revenue
        status = "pending" if actual is None else "on_track" if (attainment or 0) >= 100 else "tracking"
        if projected_gap_pct is not None and projected_gap_pct <= -float(thresholds["forecastGapPct"]):
            status = "warning"
            alerts.append(_alert(
                "forecast_below_target",
                _severity_from_gap(projected_gap_pct, float(thresholds["forecastGapPct"])),
                f"{month} 預測可能低於目標",
                f"Month-End Forecast 為 {forecasted:,.0f}，目標為 {target_revenue:,.0f}。",
                "檢查目前落後的分社與產品，並確認本月剩餘期間的補救計畫。",
                {"month": month, "targetRevenue": target_revenue, "forecastedRevenue": forecasted, "projectedGapAmount": projected_gap},
            ))
        targets.append({
            "id": str(raw_target.get("id") or month),
            "label": str(raw_target.get("label") or f"{month} 月度目標"),
            "month": month,
            "scope": str(raw_target.get("scope") or "combined"),
            "targetRevenue": target_revenue,
            "actualRevenue": actual,
            "attainmentPct": attainment,
            "gapAmount": gap,
            "forecastedRevenue": forecasted,
            "projectedGapAmount": projected_gap,
            "status": status,
        })

    if not active_targets:
        alerts.append(_alert(
            "target_not_configured", "info", "尚未設定管理目標",
            "目前沒有已 approved 的月份目標，因此不顯示假造的達成率。",
            "完成目標設定並 approved 後，再啟用 Actual vs Target 預警。",
            {"targetConfigSource": config.get("source"), "approvalStatus": config.get("approvalStatus")},
        ))

    quality_score = float(quality.get("overallScore") or 0)
    if quality_score < float(thresholds["qualityCriticalScore"]):
        alerts.append(_alert("data_quality_low", "critical", "資料品質需要處理", f"Data Quality score 為 {quality_score:.1f}。", "先處理資料品質問題，再使用預測或管理目標作決策。", {"overallScore": quality_score}))
    elif quality_score < float(thresholds["qualityWarningScore"]):
        alerts.append(_alert("data_quality_low", "warning", "資料品質需要留意", f"Data Quality score 為 {quality_score:.1f}。", "檢查缺失日期、未匹配或欄位完整性。", {"overallScore": quality_score}))

    health_status = str(health.get("status") or "unknown")
    if health_status in {"critical", "degraded"}:
        alerts.append(_alert(
            "system_health_critical" if health_status == "critical" else "system_health_degraded",
            "critical" if health_status == "critical" else "warning",
            "系統健康狀態需要處理",
            f"System Health 目前為 {health_status}。",
            "先查看 API Status 與 Operational Health，再採用本頁的管理判斷。",
            {"healthStatus": health_status, "issues": health.get("issues") or []},
        ))

    latest = health.get("latestAcceptance") or {}
    if latest.get("coreStatus") == "drift" or latest.get("rollbackStatus") == "rollback_failed":
        alerts.append(_alert(
            "baseline_or_rollback_risk", "critical", "正式口徑或回滾需要處理",
            "最近一次驗收出現 baseline drift 或 rollback failure。",
            "停止依賴受影響的管理數字，先完成 acceptance / rollback 排查。",
            {"coreStatus": latest.get("coreStatus"), "rollbackStatus": latest.get("rollbackStatus"), "recordId": latest.get("id")},
        ))

    if forecast.get("status") != "ready":
        alerts.append(_alert(
            "forecast_not_ready", "warning", "Forecast 尚未就緒",
            forecast.get("message") or "Forecast cache 尚未提供可用結果。",
            "先確認 Forecast cache 與資料 freshness，再使用預測型預警。",
            {"forecastStatus": forecast.get("status")},
        ))

    decisions = [
        {
            "id": f"decision-{item['id']}",
            "priority": item["severity"],
            "title": item["title"],
            "summary": item["summary"],
            "recommendation": item["recommendation"],
            "evidence": item["evidence"],
            "status": "open",
        }
        for item in alerts
        if item["severity"] != "info"
    ]
    return {
        "status": "ready" if facts.get("status") == "ready" else "degraded",
        "message": "Management decision read model ready.",
        "targetConfig": {
            "status": config_status,
            "version": config.get("version"),
            "source": config.get("source"),
            "thresholds": thresholds,
        },
        "targets": targets,
        "alerts": alerts,
        "decisions": decisions,
        "provenance": {
            **public_snapshot_provenance,
            "factsStatus": facts.get("status"),
            "generationToken": facts.get("generationToken"),
            "revenueScope": facts.get("revenueScope"),
            "factsCacheStatus": facts.get("factsCacheStatus"),
            "readModelCacheStatus": facts.get("readModelCacheStatus"),
            "forecastStatus": forecast.get("status"),
            "forecastCache": forecast.get("cache") or {},
            "dataQualityStatus": quality.get("status"),
            "dataQualityCacheStatus": quality.get("cacheStatus"),
            "systemHealthStatus": health.get("status"),
        },
    }
