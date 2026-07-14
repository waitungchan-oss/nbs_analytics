import json

import pytest

from backend.services.target_governance_service import (
    TargetConfigValidationError,
    load_target_config,
    save_target_config,
    validate_target_config,
)


def _payload(**overrides):
    value = {
        "version": "2026-07",
        "scope": "不含掛賬核銷與TT退款轉團款",
        "population": "全部正式分社＋正式四人專職銷售組",
        "approvalStatus": "draft",
        "updatedBy": "manager",
        "changeReason": "2026-07 月度目標設定",
        "approvedBy": None,
        "thresholds": {
            "forecastGapPct": 0.05,
            "qualityWarningScore": 75,
            "qualityCriticalScore": 60,
        },
        "targets": [
            {
                "id": "2026-07-combined",
                "label": "2026-07 合計目標",
                "month": "2026-07",
                "scope": "combined",
                "targetRevenue": 10000000,
            }
        ],
    }
    value.update(overrides)
    return value


def test_validate_target_config_accepts_formal_scope_and_combined_monthly_target():
    result = validate_target_config(_payload())

    assert result["targets"][0]["scope"] == "combined"
    assert result["revision"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "自訂口徑"),
        ("population", "全部資料"),
        ("targets", [{"id": "x", "label": "x", "month": "2026-07", "scope": "branch", "targetRevenue": 1}]),
        ("targets", [{"id": "x", "label": "x", "month": "2026-07", "scope": "combined", "targetRevenue": 0}]),
    ],
)
def test_validate_target_config_rejects_unsafe_values(field, value):
    payload = _payload(**{field: value})

    with pytest.raises(TargetConfigValidationError):
        validate_target_config(payload)


def test_approved_config_requires_approver():
    with pytest.raises(TargetConfigValidationError, match="approvedBy"):
        validate_target_config(_payload(approvalStatus="approved"))


def test_draft_config_is_valid_but_not_active_for_decision_evaluation():
    from backend.services.decision_service import build_decision_overview

    result = build_decision_overview(
        facts={"status": "ready", "monthlyTotals": [{"month": "2026-07", "combinedRevenue": 900}]},
        forecast={"status": "ready", "monthEnd": {"month": "2026-07", "consensus": 950}},
        quality={"overallScore": 100},
        health={"status": "ok", "latestAcceptance": {}},
        target_config=_payload(),
    )

    assert result["targets"] == []
    assert result["alerts"][0]["code"] == "target_not_configured"


def test_save_target_config_increments_revision_and_appends_history(tmp_path):
    config_path = tmp_path / "decision_targets.json"
    history_path = tmp_path / "target_history.jsonl"

    first = save_target_config(_payload(), config_path=config_path, history_path=history_path, now="2026-07-14T10:00:00+08:00")
    second = save_target_config(
        _payload(version="2026-07.1", changeReason="修訂目標"),
        config_path=config_path,
        history_path=history_path,
        now="2026-07-14T11:00:00+08:00",
    )

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert load_target_config(config_path)["revision"] == 2
    rows = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    assert [row["revision"] for row in rows] == [1, 2]
    assert all(row["changeReason"] for row in rows)
