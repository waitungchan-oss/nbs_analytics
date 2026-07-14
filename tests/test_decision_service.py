from backend.services.decision_service import build_decision_overview


def _sources():
    return {
        "facts": {
            "status": "ready",
            "generationToken": "2:abc",
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "monthlyTotals": [{"month": "2026-07", "combinedRevenue": 900.0}],
        },
        "forecast": {
            "status": "ready",
            "cache": {"version": "forecast-v1"},
            "monthEnd": {"month": "2026-07", "consensus": 950.0},
        },
        "quality": {"status": "ready", "overallScore": 70.0, "overallHealth": "需關注"},
        "health": {
            "status": "ok",
            "latestAcceptance": {"coreStatus": "matched", "rollbackStatus": "not_required"},
        },
    }


def test_decision_overview_evaluates_monthly_target_and_forecast_gap():
    source = _sources()
    result = build_decision_overview(
        **source,
        target_config={
            "version": "1",
            "source": "test",
            "approvalStatus": "approved",
            "thresholds": {"forecastGapPct": 0.05},
            "targets": [{"id": "jul", "label": "2026-07 月度目標", "month": "2026-07", "targetRevenue": 1000}],
        },
    )

    target = result["targets"][0]
    assert result["status"] == "ready"
    assert target["actualRevenue"] == 900.0
    assert target["attainmentPct"] == 90.0
    assert target["gapAmount"] == -100.0
    assert target["forecastedRevenue"] == 950.0
    assert target["status"] == "warning"
    assert any(alert["code"] == "forecast_below_target" for alert in result["alerts"])


def test_decision_overview_reports_missing_targets_without_inventing_attainment():
    result = build_decision_overview(**_sources(), target_config={"version": "1", "targets": []})

    assert result["targetConfig"]["status"] == "not_configured"
    assert result["targets"] == []
    alert = next(alert for alert in result["alerts"] if alert["code"] == "target_not_configured")
    assert alert["severity"] == "info"
    assert not any("attainment" in str(item) for item in result["decisions"])


def test_decision_overview_adds_quality_health_and_baseline_alerts():
    source = _sources()
    source["health"] = {
        "status": "critical",
        "latestAcceptance": {"coreStatus": "drift", "rollbackStatus": "rollback_failed"},
    }
    result = build_decision_overview(**source, target_config={"version": "1", "targets": []})
    codes = {alert["code"] for alert in result["alerts"]}

    assert {"data_quality_low", "system_health_critical", "baseline_or_rollback_risk"} <= codes
    assert all(card["status"] == "open" for card in result["decisions"])
