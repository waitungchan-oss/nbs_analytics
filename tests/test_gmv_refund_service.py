import sqlite3

import pandas as pd
import pytest

from backend.services.gmv_refund_models import RefundCurrentState
from backend.services.gmv_refund_repository import GmvRefundRepository, migrate_gmv_schema
from backend.services.gmv_refund_service import (
    InjectedGmvFailure,
    RevenueFrames,
    deactivate_gmv_scope,
    load_gmv_scope_status,
    rebuild_gmv_scope,
    rollback_gmv_scope,
    confirm_refund_batch,
    preview_refund_batch,
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
