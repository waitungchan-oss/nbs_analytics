import json
from copy import deepcopy
from decimal import Decimal

import pytest

import database


EXPECTED_TOTALS = {
    "2026-01": 10_711_053.50,
    "2026-02": 9_765_694.54,
    "2026-03": 14_628_841.00,
    "2026-04": 10_506_207.78,
    "2026-05": 12_057_967.92,
    "2026-06": 9_083_241.29,
}


def _analytics_builder(overrides=None):
    overrides = overrides or {}

    def build(_filters):
        return {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "monthlyTrend": [
                {
                    "month": month,
                    "branchRevenue": 0.0,
                    "specialistRevenue": float(overrides.get(month, total)),
                    "combinedRevenue": float(overrides.get(month, total)),
                }
                for month, total in EXPECTED_TOTALS.items()
            ],
        }

    return build


def _base_gate():
    checks = [
        {"key": "combinedRevenue", "status": "matched"},
        {"key": "revenueScope", "status": "matched"},
    ]
    return {
        "status": "matched",
        "message": "matched",
        "matchedChecks": 2,
        "totalChecks": 2,
        "driftCheckCount": 0,
        "driftChecks": [],
        "coreValidation": {
            "status": "matched",
            "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0},
            "checks": checks,
        },
    }


def _monitoring_registry():
    from backend.services.monthly_baseline_service import load_monthly_baseline_registry

    registry = deepcopy(load_monthly_baseline_registry())
    for row in registry["baselines"]:
        row["mode"] = "blocking" if row.get("legacyCore") else "monitoring"
    return registry


def test_governed_gate_binds_legacy_and_monthly_checks_to_same_db(monkeypatch, tmp_path):
    from backend.services import monthly_baseline_service

    seen = {"gate": [], "analytics": []}
    target = tmp_path / "target.db"

    monkeypatch.setattr(
        "backend.services.stability_service.build_phase2c_stability_gate",
        lambda *, db_path=None: seen["gate"].append(db_path) or {"status": "matched"},
    )
    monkeypatch.setattr(
        "backend.services.dashboard_analytics_service.build_dashboard_analytics",
        lambda filters, *, db_path=None: seen["analytics"].append(db_path) or {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "monthlyTrend": [],
        },
    )

    monthly_baseline_service.build_governed_stability_gate(db_path=target)

    assert seen == {"gate": [target], "analytics": [target]}


def test_registry_preserves_exact_monthly_values_and_six_month_total():
    from backend.services.monthly_baseline_service import load_monthly_baseline_registry

    registry = load_monthly_baseline_registry()
    totals = {
        row["month"]: Decimal(str(row["expectedTotal"]))
        for row in registry["baselines"]
    }

    assert registry["version"] == "monthly-revenue-v1"
    assert registry["scope"] == "不含掛賬核銷與TT退款轉團款"
    assert totals == {month: Decimal(str(total)) for month, total in EXPECTED_TOTALS.items()}
    assert sum(totals.values()) == Decimal("66753006.03")
    assert [row["mode"] for row in registry["baselines"]] == ["blocking"] * 6


def test_evaluate_monthly_baselines_matches_all_six_months():
    from backend.services.monthly_baseline_service import evaluate_monthly_baselines

    result = evaluate_monthly_baselines(
        _monitoring_registry(),
        analytics_builder=_analytics_builder(),
    )

    assert result["registryVersion"] == "monthly-revenue-v1"
    assert result["matchedCount"] == 6
    assert result["totalChecks"] == 6
    assert result["allMatched"] is True
    assert result["blockingStatus"] == "matched"
    assert len(result["monitoringChecks"]) == 5
    assert len(result["blockingChecks"]) == 1
    assert result["checks"][2]["formattedExpectedTotal"] == "HKD 14,628,841"


def test_monitoring_drift_is_reported_without_changing_core_gate():
    from backend.services.monthly_baseline_service import (
        apply_monthly_blocking_checks,
        evaluate_monthly_baselines,
    )

    evaluation = evaluate_monthly_baselines(
        _monitoring_registry(),
        analytics_builder=_analytics_builder({"2026-01": EXPECTED_TOTALS["2026-01"] - 100})
    )
    result = apply_monthly_blocking_checks(_base_gate(), evaluation)

    assert evaluation["allMatched"] is False
    assert evaluation["monitoringChecks"][0]["status"] == "drift"
    assert result["status"] == "matched"
    assert result["totalChecks"] == 2
    assert result["monthlyBaseline"]["allMatched"] is False


def test_promoted_blocking_drift_changes_core_gate_status():
    from backend.services.monthly_baseline_service import (
        apply_monthly_blocking_checks,
        evaluate_monthly_baselines,
        load_monthly_baseline_registry,
    )

    registry = _monitoring_registry()
    registry["baselines"][0]["mode"] = "blocking"
    evaluation = evaluate_monthly_baselines(
        registry,
        analytics_builder=_analytics_builder({"2026-01": EXPECTED_TOTALS["2026-01"] - 100}),
    )
    result = apply_monthly_blocking_checks(_base_gate(), evaluation)

    assert result["status"] == "drift"
    assert result["totalChecks"] == 3
    assert result["matchedChecks"] == 2
    assert result["driftCheckCount"] == 1
    assert result["driftChecks"][0]["key"] == "monthlyRevenue:2026-01"


def test_legacy_may_blocking_check_is_not_duplicated_in_core_gate():
    from backend.services.monthly_baseline_service import (
        apply_monthly_blocking_checks,
        evaluate_monthly_baselines,
    )

    evaluation = evaluate_monthly_baselines(
        _monitoring_registry(),
        analytics_builder=_analytics_builder(),
    )
    result = apply_monthly_blocking_checks(_base_gate(), evaluation)

    assert result["status"] == "matched"
    assert result["totalChecks"] == 2
    assert [row["key"] for row in result["coreValidation"]["checks"]] == [
        "combinedRevenue",
        "revenueScope",
    ]


def test_governance_ignores_old_history_without_monthly_payload():
    from backend.services.monthly_baseline_service import (
        build_monthly_baseline_governance,
        evaluate_monthly_baselines,
    )

    evaluation = evaluate_monthly_baselines(
        _monitoring_registry(),
        analytics_builder=_analytics_builder(),
    )
    result = build_monthly_baseline_governance(
        evaluation=evaluation,
        history_records=[
            {
                "id": 13,
                "createdAt": "2026-07-10T09:41:29+08:00",
                "uploadStatus": "accepted",
                "rollbackStatus": "not_required",
                "monthlyBaseline": {},
            }
        ],
    )

    assert result["status"] == "monitoring"
    assert result["stableUploadCycles"] == 0
    assert result["requiredStableUploadCycles"] == 1
    assert result["promotionReady"] is False
    assert result["eligibleRecordId"] is None


def test_governance_becomes_promotion_ready_after_one_accepted_matched_upload():
    from backend.services.monthly_baseline_service import (
        build_monthly_baseline_governance,
        evaluate_monthly_baselines,
    )

    evaluation = evaluate_monthly_baselines(
        _monitoring_registry(),
        analytics_builder=_analytics_builder(),
    )
    result = build_monthly_baseline_governance(
        evaluation=evaluation,
        history_records=[
            {
                "id": 14,
                "createdAt": "2026-07-11T10:00:00+08:00",
                "uploadStatus": "accepted",
                "rollbackStatus": "not_required",
                "monthlyBaseline": evaluation,
            }
        ],
    )

    assert result["status"] == "promotion_ready"
    assert result["stableUploadCycles"] == 1
    assert result["promotionReady"] is True
    assert result["eligibleRecordId"] == 14
    assert result["eligibleCreatedAt"] == "2026-07-11T10:00:00+08:00"


def test_governance_resets_cycle_when_latest_accepted_upload_has_monitoring_drift():
    from backend.services.monthly_baseline_service import (
        build_monthly_baseline_governance,
        evaluate_monthly_baselines,
    )

    evaluation = evaluate_monthly_baselines(
        _monitoring_registry(),
        analytics_builder=_analytics_builder({"2026-01": EXPECTED_TOTALS["2026-01"] - 100})
    )
    result = build_monthly_baseline_governance(
        evaluation=evaluation,
        history_records=[
            {
                "id": 15,
                "createdAt": "2026-07-12T10:00:00+08:00",
                "uploadStatus": "accepted",
                "rollbackStatus": "not_required",
                "monthlyBaseline": evaluation,
            }
        ],
    )

    assert result["status"] == "drift"
    assert result["stableUploadCycles"] == 0
    assert result["promotionReady"] is False
    assert result["eligibleRecordId"] is None


def test_promotion_requires_explicit_confirmation():
    from backend.services.monthly_baseline_service import promote_monthly_baselines

    with pytest.raises(ValueError, match="confirmation is required"):
        promote_monthly_baselines(confirmed=False, expected_record_id=14)


def test_promotion_rejects_stale_upload_record(tmp_path, monkeypatch):
    from backend.services import monthly_baseline_service as service

    registry_path = tmp_path / "baselines.json"
    registry = _monitoring_registry()
    registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    evaluation = service.evaluate_monthly_baselines(registry, analytics_builder=_analytics_builder())
    monkeypatch.setattr(
        service,
        "build_monthly_baseline_governance",
        lambda evaluation=None, history_records=None: {
            **evaluation,
            "promotionReady": True,
            "eligibleRecordId": 14,
        },
    )
    monkeypatch.setattr(service, "evaluate_monthly_baselines", lambda registry=None: evaluation)

    with pytest.raises(ValueError, match="stale upload record"):
        service.promote_monthly_baselines(
            confirmed=True,
            expected_record_id=13,
            registry_path=registry_path,
        )


def test_promotion_rejects_current_monthly_drift(tmp_path, monkeypatch):
    from backend.services import monthly_baseline_service as service

    registry_path = tmp_path / "baselines.json"
    registry = _monitoring_registry()
    registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    evaluation = service.evaluate_monthly_baselines(
        registry,
        analytics_builder=_analytics_builder({"2026-01": EXPECTED_TOTALS["2026-01"] - 100})
    )
    monkeypatch.setattr(service, "evaluate_monthly_baselines", lambda registry=None: evaluation)
    monkeypatch.setattr(
        service,
        "build_monthly_baseline_governance",
        lambda evaluation=None, history_records=None: {
            **evaluation,
            "promotionReady": False,
            "eligibleRecordId": None,
        },
    )

    with pytest.raises(ValueError, match="not ready"):
        service.promote_monthly_baselines(
            confirmed=True,
            expected_record_id=14,
            registry_path=registry_path,
        )


def test_promotion_updates_only_monitoring_months_and_records_audit(tmp_path, monkeypatch):
    from backend.services import monthly_baseline_service as service

    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "history.db"))
    registry_path = tmp_path / "baselines.json"
    original = _monitoring_registry()
    registry_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluation = service.evaluate_monthly_baselines(original, analytics_builder=_analytics_builder())
    monkeypatch.setattr(service, "evaluate_monthly_baselines", lambda registry=None: evaluation)
    monkeypatch.setattr(
        service,
        "build_monthly_baseline_governance",
        lambda evaluation=None, history_records=None: {
            **evaluation,
            "promotionReady": True,
            "eligibleRecordId": 14,
        },
    )

    result = service.promote_monthly_baselines(
        confirmed=True,
        expected_record_id=14,
        registry_path=registry_path,
        db_path=tmp_path / "history.db",
        generation_path=tmp_path / "data_generation.json",
    )

    updated = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [row["mode"] for row in updated["baselines"]] == ["blocking"] * 6
    assert updated["baselines"][4]["legacyCore"] is True
    assert result["status"] == "promoted"
    assert result["promotedMonths"] == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-06"]
    assert result["backupPath"]
    assert service.Path(result["backupPath"]).exists()
    events = service.list_monthly_baseline_promotions()
    assert events[0]["uploadRecordId"] == 14
    assert events[0]["registryVersion"] == "monthly-revenue-v1"
    assert events[0]["oldModes"]["2026-05"] == "blocking"
    assert events[0]["newModes"] == {month: "blocking" for month in EXPECTED_TOTALS}


def test_promotion_refreshes_generation_signature_without_advancing_generation(tmp_path, monkeypatch):
    from backend.services import monthly_baseline_service as service
    from backend.services.cache_generation_service import advance_cache_generation, load_cache_generation

    db_path = tmp_path / "history.db"
    conn = database.get_db_connection(db_path)
    try:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.commit()
    finally:
        conn.close()
    generation_path = tmp_path / "data_generation.json"
    before = advance_cache_generation(
        db_path=db_path,
        operation_id="upload-op-14",
        status="accepted",
        path=generation_path,
    )
    registry_path = tmp_path / "baselines.json"
    original = _monitoring_registry()
    registry_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluation = service.evaluate_monthly_baselines(original, analytics_builder=_analytics_builder())
    monkeypatch.setattr(service, "evaluate_monthly_baselines", lambda registry=None: evaluation)
    monkeypatch.setattr(
        service,
        "build_monthly_baseline_governance",
        lambda evaluation=None, history_records=None: {
            **evaluation,
            "promotionReady": True,
            "eligibleRecordId": 14,
        },
    )

    service.promote_monthly_baselines(
        confirmed=True,
        expected_record_id=14,
        registry_path=registry_path,
        db_path=db_path,
        generation_path=generation_path,
    )

    after = load_cache_generation(generation_path, db_path=db_path)
    assert after["generation"] == before["generation"]
    assert after["operationId"] == "upload-op-14"
    assert after["signatureMatched"] is True


def test_promotion_restores_registry_when_audit_write_fails(tmp_path, monkeypatch):
    from backend.services import monthly_baseline_service as service

    registry_path = tmp_path / "baselines.json"
    original = _monitoring_registry()
    original_text = json.dumps(original, ensure_ascii=False, indent=2)
    registry_path.write_text(original_text, encoding="utf-8")
    evaluation = service.evaluate_monthly_baselines(original, analytics_builder=_analytics_builder())
    monkeypatch.setattr(service, "evaluate_monthly_baselines", lambda registry=None: evaluation)
    monkeypatch.setattr(
        service,
        "build_monthly_baseline_governance",
        lambda evaluation=None, history_records=None: {
            **evaluation,
            "promotionReady": True,
            "eligibleRecordId": 14,
        },
    )
    monkeypatch.setattr(service, "_record_promotion_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))

    with pytest.raises(RuntimeError, match="audit failed"):
        service.promote_monthly_baselines(
            confirmed=True,
            expected_record_id=14,
            registry_path=registry_path,
        )

    assert json.loads(registry_path.read_text(encoding="utf-8")) == original


def test_list_promotions_does_not_create_audit_table_when_absent(tmp_path, monkeypatch):
    from backend.services import monthly_baseline_service as service

    db_path = tmp_path / "history.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_path))
    conn = database.sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.commit()
    finally:
        conn.close()

    assert service.list_monthly_baseline_promotions() == []
    conn = database.sqlite3.connect(db_path)
    try:
        table_exists = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (service.PROMOTION_TABLE,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert table_exists == 0
