import sqlite3

import pandas as pd
import pytest

from backend.services.gmv_refund_models import RefundCurrentState
from backend.services.gmv_refund_repository import GmvRefundRepository, migrate_gmv_schema
from backend.services.gmv_refund_service import (
    build_active_gmv_read_model,
    InjectedGmvFailure,
    RevenueFrames,
    deactivate_gmv_scope,
    load_gmv_scope_status,
    rebuild_gmv_scope,
    rollback_gmv_scope,
    confirm_refund_batch,
    filter_revenue_frames_for_receipts,
    rebuild_affected_reconciliation_rows,
    preview_refund_batch,
    revenue_state_token,
)
from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease
from app_workflows import _gmv_revenue_row_fingerprint


def _seed_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tour_data (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE others_data (id INTEGER PRIMARY KEY)")
    migrate_gmv_schema(path)
    return path


def _frames():
    tour = pd.DataFrame(
        [
            {"來源單據號": "S-1", "收款原幣金額": 100.0, "收款類型": "旅費"},
            {"來源單據號": "EX-1", "收款原幣金額": 50.0, "收款類型": "掛賬核銷"},
        ]
    )
    formal_tour = tour.loc[tour["收款類型"] != "掛賬核銷"].copy()
    return RevenueFrames(
        raw_tour=tour,
        raw_others=pd.DataFrame(),
        formal_tour=formal_tour,
        formal_others=pd.DataFrame(),
    )


def test_filter_revenue_frames_for_receipts_keeps_only_affected_rows():
    filtered = filter_revenue_frames_for_receipts(_frames(), (" S-1 ",))

    assert filtered.raw_tour["來源單據號"].tolist() == ["S-1"]
    assert filtered.formal_tour["來源單據號"].tolist() == ["S-1"]
    assert filtered.raw_others.empty
    assert filtered.formal_others.empty


def test_filter_revenue_frames_for_receipts_preserves_empty_frame_schema():
    frames = _frames()
    filtered = filter_revenue_frames_for_receipts(frames, ())

    assert filtered.formal_tour.empty
    assert list(filtered.formal_tour.columns) == list(frames.formal_tour.columns)
    assert filtered.formal_others.empty
    assert list(filtered.formal_others.columns) == list(frames.formal_others.columns)


def test_rebuild_affected_reconciliation_rows_passes_bounded_inputs(monkeypatch):
    captured = {}

    def capture(conn, version_id, frames, states, observation_ids, revenue_token, rule_version):
        captured["frames"] = frames
        captured["states"] = states
        return "reconciliation", "adjustment"

    monkeypatch.setattr("backend.services.gmv_refund_service._insert_reconciliation_rows", capture)
    states = {
        "F-1": RefundCurrentState("F-1", "S-1", 100, "已退款", "B-1", "sha-1"),
        "F-2": RefundCurrentState("F-2", "S-2", 200, "已退款", "B-1", "sha-2"),
    }

    result = rebuild_affected_reconciliation_rows(
        None,
        version_id="V-2",
        frames=_frames(),
        states=states,
        observation_ids={"F-1": "O-1", "F-2": "O-2"},
        revenue_token="rev-1",
        rule_version="rules-1",
        affected_source_receipt_nos=("S-1",),
    )

    assert result == ("reconciliation", "adjustment")
    assert captured["frames"].formal_tour["來源單據號"].tolist() == ["S-1"]
    assert tuple(captured["states"]) == ("F-1",)


def _seed_current(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO gmv_refund_batches VALUES "
            "('B-0', 'old.xlsx', 'old-file', 'old-normalized', 1, 1, 'CONFIRMED', 'old-preflight', '{}', 'rev-1', 'rules-1', '2026-08-20T00:00:00Z', 'tester')"
        )
        conn.execute(
            "INSERT INTO gmv_refund_observations VALUES "
            "('O-0', 'B-0', 1, 'old-row', 'R-1', 'S-1', NULL, '退款中', 5000, 'HKD', NULL, '2026-08', '2026-08-20T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO gmv_refund_current VALUES "
            "('R-1', 'O-0', 'S-1', NULL, '退款中', 5000, 'HKD', NULL, 'B-0', 'B-0', 'old-state', '2026-08-20T00:00:00Z')"
        )


def _preview_with_warning(tmp_path, code):
    db_path = _seed_database(tmp_path / "nbs.db")
    repository = GmvRefundRepository(db_path)
    return preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-WARN", "來源單據號": "S-1", "退款原幣金額": "20", "退款狀態": "已退款"}]
        ),
        repository=repository,
        revenue_frames=_frames(),
        revenue_generation_token="rev-warning",
        rule_version="rules-1",
        file_sha256=f"warning-{code}",
        warning_codes=(code,),
        warning_summaries=({"code": code, "count": 1, "amount": 20.0, "examples": ["S-1"]},),
    )


def test_warning_only_preview_can_be_confirmed_without_acknowledgement(tmp_path):
    preview = _preview_with_warning(tmp_path, code="SQLITE_SOURCE_NOT_FOUND")
    assert preview.blocking_codes == ()
    receipt = confirm_refund_batch(
        preview,
        actor="streamlit-auto-merge",
        acknowledgements=frozenset(),
        db_path=tmp_path / "nbs.db",
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=_frames,
        revenue_generation_loader=lambda: preview.revenue_generation_token,
    )
    assert receipt.version_id
    with sqlite3.connect(tmp_path / "nbs.db") as conn:
        payload = conn.execute(
            "SELECT warning_acknowledgement_json FROM gmv_refund_batches WHERE batch_id = ?",
            (receipt.batch_id,),
        ).fetchone()[0]
    assert "SQLITE_SOURCE_NOT_FOUND" in payload
    assert '"warningSummaries"' in payload


def test_confirm_is_idempotent_for_same_file_and_revenue_generation(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    repository = GmvRefundRepository(db_path)
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-IDEMPOTENT", "來源單據號": "S-1", "退款原幣金額": "20", "退款狀態": "已退款"}]
        ),
        repository=repository,
        revenue_frames=_frames(),
        revenue_generation_token="rev-idempotent",
        rule_version="rules-1",
        file_sha256="idempotent-file",
    )

    kwargs = {
        "actor": "streamlit-auto-merge",
        "acknowledgements": frozenset(),
        "db_path": db_path,
        "coordination_db_path": tmp_path / "coordination.db",
        "revenue_loader": _frames,
        "revenue_generation_loader": lambda: preview.revenue_generation_token,
    }
    first = confirm_refund_batch(preview, **kwargs)
    second = confirm_refund_batch(preview, **kwargs)

    assert second == first
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM gmv_refund_batches").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM gmv_scope_versions WHERE status = 'ACTIVE'").fetchone()[0] == 1


def test_preview_uses_current_plus_incoming_status_change_for_both_dimensions(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    _seed_current(db_path)
    repository = GmvRefundRepository(db_path)
    refunds = pd.DataFrame(
        [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
    )

    preview = preview_refund_batch(
        refunds,
        repository=repository,
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="new-file",
    )

    assert preview.change_counts == {
        "NEW": 0,
        "UNCHANGED": 0,
        "STATUS_CHANGED": 1,
        "REFUND_IDENTITY_CONFLICT": 0,
    }
    assert preview.dimensions["總退款"]["refund_detail_amount_minor"] == 5000
    assert preview.dimensions["已退款"]["refund_detail_amount_minor"] == 5000
    assert preview.official_net_gmv_minor == 5000


def test_preview_is_read_only_and_identity_conflict_is_blocking(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    _seed_current(db_path)
    repository = GmvRefundRepository(db_path)
    before = repository.load_current_refunds()
    refunds = pd.DataFrame(
        [{"退款單號": "R-1", "來源單據號": "S-2", "退款原幣金額": "60", "退款狀態": "已退款"}]
    )

    preview = preview_refund_batch(
        refunds,
        repository=repository,
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="conflict-file",
    )

    assert preview.status == "blocked"
    assert "REFUND_IDENTITY_CONFLICT" in preview.blocking_codes
    assert repository.load_current_refunds() == before


def test_revenue_row_fingerprint_is_stable_and_amount_sensitive():
    row = pd.Series(
        {
            "來源單據號": "S-1",
            "收款時間": "2026-08-20",
            "收款原幣金額": 100.0,
            "銷售點": "A",
            "銷售員": "Alice",
        }
    )

    first = _gmv_revenue_row_fingerprint("tour_data", row)
    second = _gmv_revenue_row_fingerprint("tour_data", row.copy())
    changed_row = row.copy()
    changed_row["收款原幣金額"] = 101.0
    changed = _gmv_revenue_row_fingerprint("tour_data", changed_row)

    assert first == second
    assert first != changed


def test_revenue_row_fingerprint_distinguishes_identical_row_occurrences():
    row = pd.Series(
        {
            "來源單據號": "S-1",
            "收款時間": "2026-08-20",
            "收款原幣金額": 50.0,
            "__gmv_row_ordinal": 0,
        }
    )
    duplicate = row.copy()
    duplicate["__gmv_row_ordinal"] = 1

    assert _gmv_revenue_row_fingerprint("旅行團", row) != _gmv_revenue_row_fingerprint(
        "旅行團", duplicate
    )


def test_revenue_row_fingerprint_uses_pre_refund_amount_for_adjusted_rows():
    first = pd.Series(
        {
            "來源單據號": "S-1",
            "收款時間": "2026-08-20",
            "收款原幣金額": 0.0,
            "退款前收款原幣金額": 50.0,
            "__gmv_row_ordinal": 0,
        }
    )
    second = first.copy()
    second["退款前收款原幣金額"] = 80.0

    assert _gmv_revenue_row_fingerprint("旅行團", first) != _gmv_revenue_row_fingerprint(
        "旅行團", second
    )


def test_revenue_state_token_is_order_independent_and_sensitive_to_revenue_and_rules():
    frames = _frames()
    reordered = RevenueFrames(
        raw_tour=frames.raw_tour.iloc[::-1].reset_index(drop=True),
        raw_others=frames.raw_others,
        formal_tour=frames.formal_tour,
        formal_others=frames.formal_others,
    )
    changed_tour = frames.formal_tour.copy()
    changed_tour.loc[changed_tour.index[0], "收款原幣金額"] = 101.0
    changed = RevenueFrames(
        raw_tour=frames.raw_tour,
        raw_others=frames.raw_others,
        formal_tour=changed_tour,
        formal_others=frames.formal_others,
    )

    token = revenue_state_token(frames, "rules-1")

    assert token == revenue_state_token(reordered, "rules-1")
    assert token != revenue_state_token(changed, "rules-1")
    assert token != revenue_state_token(frames, "rules-2")


def test_confirm_preserves_identical_revenue_rows_and_snapshot_money_invariant(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    duplicate_tour = pd.DataFrame(
        [
            {"來源單據號": "S-1", "收款原幣金額": 50.0, "收款時間": "2026-08-20"},
            {"來源單據號": "S-1", "收款原幣金額": 50.0, "收款時間": "2026-08-20"},
        ]
    )
    frames = RevenueFrames(
        raw_tour=duplicate_tour,
        raw_others=pd.DataFrame(),
        formal_tour=duplicate_tour.copy(),
        formal_others=pd.DataFrame(),
    )
    token = revenue_state_token(frames, "rules-1")
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=frames,
        revenue_generation_token=token,
        rule_version="rules-1",
        file_sha256="duplicates-file",
    )

    receipt = confirm_refund_batch(
        preview,
        actor="tester",
        acknowledgements=frozenset(),
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=lambda: frames,
        revenue_generation_loader=lambda: token,
    )

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT COUNT(*), SUM(applied_refund_amount_minor) "
            "FROM gmv_adjustment_snapshot WHERE version_id = ?",
            (receipt.version_id,),
        ).fetchone()
        metric = conn.execute(
            "SELECT metric_amount_minor FROM gmv_metric_snapshot "
            "WHERE version_id = ? AND refund_dimension = 'REFUNDED' "
            "AND metric_name = 'APPLIED_REFUND'",
            (receipt.version_id,),
        ).fetchone()[0]

    assert snapshot == (2, 5000)
    assert metric == snapshot[1]


def test_active_read_model_reopens_both_refund_dimensions_without_upload(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    frames = _frames()
    token = revenue_state_token(frames, "rules-1")
    preview = preview_refund_batch(
        pd.DataFrame(
            [
                {"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"},
                {"退款單號": "R-2", "來源單據號": "S-1", "退款原幣金額": "20", "退款狀態": "退款中"},
            ]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=frames,
        revenue_generation_token=token,
        rule_version="rules-1",
        file_sha256="active-read-file",
    )
    receipt = confirm_refund_batch(
        preview,
        actor="tester",
        acknowledgements=frozenset(),
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=lambda: frames,
        revenue_generation_loader=lambda: token,
    )

    model = build_active_gmv_read_model(
        GmvRefundRepository(db_path), frames, rule_version="rules-1"
    )

    assert model.status == "CURRENT"
    assert model.version_id == receipt.version_id
    assert model.can_export is True
    assert model.total_adjusted["refund_total"] == 70.0
    assert model.paid_adjusted["refund_total"] == 50.0
    assert model.total_adjusted["applied_refund_total"] == 70.0
    assert model.paid_adjusted["applied_refund_total"] == 50.0


def test_active_read_model_fails_closed_when_revenue_changes(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    frames = _frames()
    token = revenue_state_token(frames, "rules-1")
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=frames,
        revenue_generation_token=token,
        rule_version="rules-1",
        file_sha256="stale-read-file",
    )
    confirm_refund_batch(
        preview,
        actor="tester",
        acknowledgements=frozenset(),
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=lambda: frames,
        revenue_generation_loader=lambda: token,
    )
    changed = _frames()
    changed.formal_tour.loc[changed.formal_tour.index[0], "收款原幣金額"] = 101.0

    model = build_active_gmv_read_model(
        GmvRefundRepository(db_path), changed, rule_version="rules-1"
    )

    assert model.status == "STALE_REVENUE_GENERATION"
    assert model.can_export is False
    assert model.total_adjusted is None
    assert model.paid_adjusted is None


def test_preview_requires_refund_order_business_key(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="missing-refund-id",
    )

    assert preview.status == "blocked"
    assert "MISSING_退款單號" in preview.blocking_codes


def test_confirm_updates_current_state_and_activates_one_version(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    _seed_current(db_path)
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="confirm-file",
    )

    receipt = confirm_refund_batch(
        preview,
        actor="tester",
        acknowledgements=frozenset(),
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=_frames,
        revenue_generation_loader=lambda: "rev-1",
    )

    assert receipt.version_id
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM gmv_scope_versions WHERE status = 'ACTIVE'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT refund_status FROM gmv_refund_current WHERE refund_order_no = 'R-1'"
        ).fetchone()[0] == "已退款"
        assert conn.execute(
            "SELECT event_type FROM gmv_scope_events WHERE event_id = ?",
            (receipt.event_id,),
        ).fetchone()[0] == "ACTIVATE"


def test_confirm_fault_rolls_back_current_and_active_pointer(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    _seed_current(db_path)
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="fault-file",
    )
    before = GmvRefundRepository(db_path).load_current_refunds()

    with pytest.raises(InjectedGmvFailure):
        confirm_refund_batch(
            preview,
            actor="tester",
            acknowledgements=frozenset(),
            db_path=db_path,
            coordination_db_path=tmp_path / "coordination.db",
            revenue_loader=_frames,
            revenue_generation_loader=lambda: "rev-1",
            fault_after="after_current_projection",
        )

    assert GmvRefundRepository(db_path).load_current_refunds() == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM gmv_scope_versions").fetchone()[0] == 0


def test_confirm_fails_when_shared_upload_lease_is_busy(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-2", "來源單據號": "S-1", "退款原幣金額": "10", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="busy-file",
    )
    coordination = tmp_path / "coordination.db"
    with acquire_upload_lease(
        entry_point="test-holder",
        source_files=[],
        coordination_db_path=coordination,
    ):
        with pytest.raises(UploadBusyError):
            confirm_refund_batch(
                preview,
                actor="tester",
                acknowledgements=frozenset(),
                db_path=db_path,
                coordination_db_path=coordination,
                revenue_loader=_frames,
                revenue_generation_loader=lambda: "rev-1",
            )


def test_scope_status_marks_active_version_stale_when_revenue_generation_changes(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    _seed_current(db_path)
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="stale-file",
    )
    confirm_refund_batch(
        preview,
        actor="tester",
        acknowledgements=frozenset(),
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=_frames,
        revenue_generation_loader=lambda: "rev-1",
    )

    status = load_gmv_scope_status(GmvRefundRepository(db_path), "rev-2")

    assert status["status"] == "STALE_REVENUE_GENERATION"


def test_deactivate_removes_active_pointer_without_deleting_ledger(tmp_path):
    db_path = _seed_database(tmp_path / "nbs.db")
    _seed_current(db_path)
    preview = preview_refund_batch(
        pd.DataFrame(
            [{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": "50", "退款狀態": "已退款"}]
        ),
        repository=GmvRefundRepository(db_path),
        revenue_frames=_frames(),
        revenue_generation_token="rev-1",
        rule_version="rules-1",
        file_sha256="deactivate-file",
    )
    confirm_refund_batch(
        preview,
        actor="tester",
        acknowledgements=frozenset(),
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
        revenue_loader=_frames,
        revenue_generation_loader=lambda: "rev-1",
    )

    receipt = deactivate_gmv_scope(
        reason="test deactivate",
        actor="tester",
        db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db",
    )

    assert receipt.event_type == "DEACTIVATE"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM gmv_scope_versions WHERE status = 'ACTIVE'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM gmv_refund_observations").fetchone()[0] == 2
