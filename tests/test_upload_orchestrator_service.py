import pandas as pd

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
