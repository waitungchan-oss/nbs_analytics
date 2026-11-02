import pandas as pd
import pytest

from backend.services.receipt_exclusion_models import ReceiptExclusionIdentity, ReceiptExclusionRule
from backend.services.upload_lock_service import UploadOperation


def _operation():
    return UploadOperation("op-1", "test", 123, "2026-07-12T12:00:00+08:00", ("main.xlsx",))


def _accepted_execution(tmp_path, **overrides):
    from backend.services import beta_seller_scope_service, upload_orchestrator_service
    from backend.services.upload_orchestrator_service import execute_upload_operation

    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
        "beta_seller_scope": {
            "tour": pd.DataFrame(),
            "others": pd.DataFrame(),
            "coverage": {"complete": True, "status": "complete"},
        },
    }
    kwargs = {
        "main_file": object(), "live_db_path": tmp_path / "live.db",
        "preflight_runner": lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
        "upsert_runner": lambda *args, **kwargs: {"backup_path": "backup.db"},
        "load_runner": lambda **kwargs: (prepared["tour"], prepared["others"]),
        "gate_builder": lambda **kwargs: {"status": "matched", "monthlyBaseline": {"allMatched": True}},
        "rollback_handler": lambda *args, **kwargs: {"status": "accepted", "rollbackStatus": "not_required", "postRollbackGate": None},
        "generation_advancer": lambda **kwargs: {"generation": 1, "operationId": "op-1", "signatureMatched": True},
        "generation_signature_refresher": lambda **kwargs: {"generation": 1, "operationId": "op-1", "signatureMatched": True},
        "history_writer": lambda *args, **kwargs: 1,
        "rules_loader": lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    }
    kwargs.update(overrides)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("NBS_ANALYTICS_CACHE_DIR", str(tmp_path / "cache"))
        if upload_orchestrator_service.persist_beta_seller_scope_frames is beta_seller_scope_service.persist_beta_seller_scope_frames:
            monkeypatch.setattr(
                upload_orchestrator_service,
                "persist_beta_seller_scope_frames",
                lambda tour, others, coverage, **kwargs: {
                    "status": "saved", "tourRows": len(tour), "othersRows": len(others), "coverage": coverage,
                },
            )
        return execute_upload_operation(_operation(), **kwargs)


def test_blocked_preflight_does_not_write_history_or_database(tmp_path):
    from backend.services.upload_orchestrator_service import execute_upload_operation

    calls = []
    execution = execute_upload_operation(
        _operation(), main_file=object(), live_db_path=tmp_path / "live.db",
        preflight_runner=lambda *args, **kwargs: {"status": "drift", "message": "warning", "prepared": {}},
        upsert_runner=lambda *args, **kwargs: calls.append("upsert"),
        history_writer=lambda *args, **kwargs: calls.append("history"),
        rules_loader=lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    )

    assert execution.response["status"] == "blocked"
    assert execution.response["preflightReport"]["message"] == "warning"
    assert execution.response["writeCommitted"] is False
    assert calls == []


def test_empty_prepared_batch_is_blocked_without_write(tmp_path):
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": {"tour": pd.DataFrame(), "others": pd.DataFrame()}},
        upsert_runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("write")),
    )
    assert execution.response["status"] == "blocked"
    assert execution.response["writeCommitted"] is False


def test_accepted_upload_records_history_and_generation(tmp_path):
    contexts = []
    execution = _accepted_execution(tmp_path, history_writer=lambda gate, context, **kwargs: contexts.append(context) or 7)
    assert execution.response["status"] == "success"
    assert execution.response["historyRecordId"] == 7
    assert execution.response["cacheState"] == "invalidated"
    assert contexts[0]["operation_id"] == "op-1"
    assert contexts[0]["latest_data_date"] == "2026-07-01"


def test_accepted_upload_keeps_legacy_beta_cache_bytes_unchanged(tmp_path, monkeypatch):
    from backend.services import beta_only_exception_service
    from backend.services import upload_orchestrator_service
    from rules import BETA_ONLY_EXCEPTION_SALES_POINTS

    monkeypatch.setenv("NBS_ANALYTICS_CACHE_DIR", str(tmp_path))
    legacy_cache_path = tmp_path / "beta_only_exception.pkl"
    beta_only_exception_service.persist_beta_only_exception_frames(
        pd.DataFrame([{
            "來源單據號": "1950506001", "收款單號": "LEGACY-1",
            "銷售點": BETA_ONLY_EXCEPTION_SALES_POINTS[0], "收款原幣金額": 100,
        }]),
        pd.DataFrame(),
        cache_path=legacy_cache_path,
    )
    legacy_cache_before = legacy_cache_path.read_bytes()

    beta_tour = pd.DataFrame([{"收款單號": "R-1", "銷售員": "002785 朱淑宜"}])
    beta_others = pd.DataFrame([{"收款單號": "R-2", "銷售員": "011185 洪淑芬"}])
    coverage = {
        "complete": True,
        "status": "complete",
        "requiredDateIntervals": [{"start": "2025-01-01", "end": "2026-07-01"}],
    }
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
        "beta_exception_tour": pd.DataFrame([{
            "來源單據號": "1950506002", "收款單號": "LEGACY-2",
            "銷售點": BETA_ONLY_EXCEPTION_SALES_POINTS[0], "收款原幣金額": 200,
        }]),
        "beta_exception_others": pd.DataFrame(),
        "beta_seller_scope": {"tour": beta_tour, "others": beta_others, "coverage": coverage},
    }
    captured = []
    monkeypatch.setattr(
        upload_orchestrator_service,
        "persist_beta_seller_scope_frames",
        lambda tour, others, passed_coverage, **kwargs: captured.append((tour, others, passed_coverage, kwargs)) or {
            "status": "saved", "tourRows": 1, "othersRows": 1,
            "coverage": passed_coverage,
        },
    )
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
    )

    assert len(captured) == 1
    assert captured[0][0].equals(beta_tour)
    assert captured[0][1].equals(beta_others)
    assert captured[0][2] is coverage
    assert captured[0][3]["data_generation"] == 1
    assert legacy_cache_path.read_bytes() == legacy_cache_before
    legacy_tour, legacy_others = beta_only_exception_service.load_beta_only_exception_frames(
        cache_path=legacy_cache_path
    )
    assert list(legacy_tour["收款單號"]) == ["LEGACY-1"]
    assert legacy_others.empty
    assert execution.response["betaExceptionCache"]["status"] == "not_run"
    assert execution.response["betaSellerScopeCache"]["status"] == "saved"
    assert execution.response["betaSellerScopeCache"]["coverage"]["complete"] is True


@pytest.mark.parametrize("final_status", ["rejected_rolled_back", "rollback_failed"])
def test_unaccepted_upload_never_persists_or_reports_beta_seller_scope_ready(
    tmp_path, monkeypatch, final_status
):
    from backend.services import upload_orchestrator_service

    calls = []
    monkeypatch.setattr(
        upload_orchestrator_service,
        "persist_beta_seller_scope_frames",
        lambda *args, **kwargs: calls.append("persist"),
    )
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "beta_seller_scope": {
            "tour": pd.DataFrame([{"收款單號": "R-1", "銷售員": "002785 朱淑宜"}]),
            "others": pd.DataFrame(),
            "coverage": {"complete": True, "status": "complete"},
        },
    }
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
        rollback_handler=lambda *args, **kwargs: {"status": final_status, "rollbackStatus": final_status},
    )

    assert calls == []
    assert execution.response["betaSellerScopeCache"]["status"] == "not_run"
    assert execution.response["betaSellerScopeCache"]["coverage"]["complete"] is False


def test_missing_beta_seller_scope_payload_is_not_ready(tmp_path, monkeypatch):
    from backend.services import upload_orchestrator_service

    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
    }
    calls = []
    monkeypatch.setattr(
        upload_orchestrator_service,
        "persist_beta_seller_scope_frames",
        lambda *args, **kwargs: calls.append("persist"),
    )
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
    )

    assert calls == []
    assert execution.response["status"] == "degraded"
    assert execution.response["writeCommitted"] is True
    assert execution.response["betaSellerScopeCache"]["status"] == "missing_payload"
    assert execution.response["betaSellerScopeCache"]["coverage"]["complete"] is False


def test_incomplete_beta_seller_scope_coverage_degrades_upload_without_rollback(tmp_path, monkeypatch):
    from backend.services import upload_orchestrator_service

    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "beta_seller_scope": {
            "tour": pd.DataFrame([{"收款單號": "R-1", "銷售員": "002785 朱淑宜"}]),
            "others": pd.DataFrame(),
            "coverage": {"complete": False, "status": "partial"},
        },
    }
    monkeypatch.setattr(
        upload_orchestrator_service,
        "persist_beta_seller_scope_frames",
        lambda *args, **kwargs: {
            "status": "saved", "tourRows": 1, "othersRows": 0,
            "coverage": None,
        },
    )
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
    )

    assert execution.response["status"] == "degraded"
    assert execution.response["writeCommitted"] is True
    assert execution.response["betaSellerScopeCache"]["coverage"] is None


def test_beta_seller_scope_persist_error_degrades_upload_and_never_reports_ready(tmp_path, monkeypatch):
    from backend.services import upload_orchestrator_service

    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "beta_seller_scope": {
            "tour": pd.DataFrame([{"收款單號": "R-1", "銷售員": "002785 朱淑宜"}]),
            "others": pd.DataFrame(),
            "coverage": {"complete": True, "status": "complete"},
        },
    }
    monkeypatch.setattr(
        upload_orchestrator_service,
        "persist_beta_seller_scope_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
    )

    assert execution.response["status"] == "degraded"
    assert execution.response["betaSellerScopeCache"]["status"] == "error"
    assert execution.response["betaSellerScopeCache"]["coverage"]["complete"] is False


def test_generation_signature_refreshes_after_history_write(tmp_path):
    calls = []
    execution = _accepted_execution(
        tmp_path,
        history_writer=lambda *args, **kwargs: calls.append("history") or 7,
        generation_signature_refresher=lambda **kwargs: calls.append("refresh") or {"generation": 1, "operationId": "op-1", "signatureMatched": True},
    )

    assert execution.response["status"] == "success"
    assert calls == ["history", "refresh"]
    assert execution.response["dataGeneration"]["signatureMatched"] is True


def test_generation_or_history_failure_is_degraded(tmp_path):
    generation_failed = _accepted_execution(tmp_path, generation_advancer=lambda **kwargs: (_ for _ in ()).throw(OSError("generation failed")))
    history_failed = _accepted_execution(tmp_path, history_writer=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("history failed")))
    assert generation_failed.response["status"] == "degraded"
    assert generation_failed.response["cacheState"] == "refresh_required"
    assert history_failed.response["status"] == "degraded"
    assert "history failed" in history_failed.response["historyError"]


def _blocked_proposal_report():
    return {
        "status": "drift",
        "message": "blocked",
        "receiptExclusion": {"registryRevision": "r1", "matchedRules": [], "autoApplyAudit": []},
        "receiptExclusionProposal": {
            "proposalFingerprint": "proposal-1",
            "candidates": [{
                "candidateId": "candidate-1",
                "receiptNo": "SK2606005393",
                "sourceOrderNo": "31NZY6629115617",
                "exclusionKind": "payment_method:TT 退款轉團款",
            }],
        },
        "prepared": {
            "receipt_exclusion_evidence": {
                "candidate-1": {
                    "rawPayload": {"收款單號": "SK2606005393"},
                    "preparedPayload": {"收款單號": "SK2606005393"},
                }
            }
        },
    }


def _matched_overlay_report():
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "31NZY6629115617", "統一日期": "2026-06-29"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
    }
    return {
        "status": "matched",
        "prepared": prepared,
        "receiptExclusion": {"registryRevision": "r1", "matchedRules": [], "autoApplyAudit": []},
        "receiptExclusionProposal": {},
    }


def _confirmation():
    return {
        "proposalFingerprint": "proposal-1",
        "selectedCandidateIds": ["candidate-1"],
        "confirmedBy": "streamlit-local",
    }


def test_confirmed_upload_reruns_preflight_with_overlay_before_activation(tmp_path):
    preflight_calls, activation_calls = [], []

    def preflight(*args, **kwargs):
        preflight_calls.append(kwargs.get("receipt_exclusion_overlay"))
        return _blocked_proposal_report() if len(preflight_calls) == 1 else _matched_overlay_report()

    execution = _accepted_execution(
        tmp_path,
        preflight_runner=preflight,
        receipt_exclusion_confirmation=_confirmation(),
        registry_activator=lambda *args, **kwargs: activation_calls.append(kwargs) or {
            "status": "activated", "ruleIds": [7], "revision": "r2",
        },
        registry_loader=lambda **kwargs: {"revision": "r2", "rules": ()},
    )

    assert preflight_calls[0] in (None, ())
    assert preflight_calls[1][0].identity.receipt_no == "SK2606005393"
    assert len(activation_calls) == 1
    assert execution.response["writeCommitted"] is True


def test_overlay_drift_does_not_activate_or_upsert(tmp_path):
    writes = []
    reports = [_blocked_proposal_report(), _blocked_proposal_report()]
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: reports.pop(0),
        receipt_exclusion_confirmation=_confirmation(),
        registry_activator=lambda *args, **kwargs: writes.append("activate"),
        upsert_runner=lambda *args, **kwargs: writes.append("upsert"),
    )

    assert execution.response["status"] == "blocked"
    assert writes == []


def test_registry_revision_change_before_upsert_blocks_operation(tmp_path):
    revisions = iter(["r1", "changed"])
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: _matched_overlay_report(),
        registry_loader=lambda **kwargs: {"revision": next(revisions), "rules": ()},
    )

    assert execution.response["status"] == "blocked"
    assert execution.response["message"] == "收款單永久排除規則已更新，請重新預演。"


def test_auto_event_failure_blocks_before_formal_upsert(tmp_path):
    writes = []
    report = _matched_overlay_report()
    report["receiptExclusion"]["autoApplyAudit"] = [{"registryId": 7, "proposalFingerprint": "r1", "payload": {}}]
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: report,
        auto_event_recorder=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("audit failed")),
        upsert_runner=lambda *args, **kwargs: writes.append("upsert"),
    )

    assert execution.response["status"] == "blocked"
    assert writes == []
