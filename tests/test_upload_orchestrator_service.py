import pandas as pd

from backend.services.receipt_exclusion_models import ReceiptExclusionIdentity, ReceiptExclusionRule
from backend.services.upload_lock_service import UploadOperation


def _operation():
    return UploadOperation("op-1", "test", 123, "2026-07-12T12:00:00+08:00", ("main.xlsx",))


def _accepted_execution(tmp_path, **overrides):
    from backend.services.upload_orchestrator_service import execute_upload_operation

    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
    }
    kwargs = {
        "main_file": object(), "live_db_path": tmp_path / "live.db",
        "preflight_runner": lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
        "upsert_runner": lambda *args, **kwargs: {"backup_path": "backup.db"},
        "load_runner": lambda **kwargs: (prepared["tour"], prepared["others"]),
        "gate_builder": lambda **kwargs: {"status": "matched", "monthlyBaseline": {"allMatched": True}},
        "rollback_handler": lambda *args, **kwargs: {"status": "accepted", "rollbackStatus": "not_required", "postRollbackGate": None},
        "generation_advancer": lambda **kwargs: {"generation": 1, "operationId": "op-1"},
        "history_writer": lambda *args, **kwargs: 1,
        "rules_loader": lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    }
    kwargs.update(overrides)
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
