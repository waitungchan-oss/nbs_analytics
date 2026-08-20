import hashlib
import sqlite3

import pytest

from backend.services.gmv_refund_repository import (
    EXPECTED_GMV_OBJECTS,
    GmvRefundRepository,
    migrate_gmv_schema,
)


def _seed_revenue_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tour_data (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.execute("CREATE TABLE others_data (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.execute("INSERT INTO tour_data VALUES (1, 100)")
        conn.execute("INSERT INTO others_data VALUES (1, 200)")
    return path


def _revenue_digest(path):
    with sqlite3.connect(path) as conn:
        rows = []
        for table in ("tour_data", "others_data"):
            rows.extend(conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall())
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _sqlite_objects(path):
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }


def test_migration_is_idempotent_and_preserves_revenue_tables(tmp_path):
    db_path = _seed_revenue_database(tmp_path / "nbs.db")
    before = _revenue_digest(db_path)

    first = migrate_gmv_schema(db_path)
    second = migrate_gmv_schema(db_path)

    assert first.created is True
    assert second.created is False
    assert EXPECTED_GMV_OBJECTS <= _sqlite_objects(db_path)
    assert _revenue_digest(db_path) == before


def test_repository_read_contract_does_not_migrate_on_construction(tmp_path):
    db_path = _seed_revenue_database(tmp_path / "nbs.db")

    repository = GmvRefundRepository(db_path)

    assert repository.validate_schema().ready is False
    assert EXPECTED_GMV_OBJECTS.isdisjoint(_sqlite_objects(db_path))


def test_empty_views_are_safe_after_migration(tmp_path):
    db_path = migrate_gmv_schema(_seed_revenue_database(tmp_path / "nbs.db")).db_path
    repository = GmvRefundRepository(db_path)

    assert repository.load_active_scope() is None
    assert repository.load_metric_snapshot("missing") .empty
    assert repository.load_adjustment_snapshot("missing").empty


def test_immutable_ledger_rows_reject_update_and_delete(tmp_path):
    db_path = migrate_gmv_schema(_seed_revenue_database(tmp_path / "nbs.db")).db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO gmv_refund_batches VALUES "
            "('B-1', 'refund.xlsx', 'f', 'n', 1, 1, 'CONFIRMED', 'p', '{}', 'g', 'r', '2026-08-20T00:00:00Z', 'tester')"
        )
        conn.execute(
            "INSERT INTO gmv_refund_observations VALUES "
            "('O-1', 'B-1', 1, 'row', 'R-1', 'S-1', NULL, '已退款', 100, 'HKD', NULL, NULL, '2026-08-20T00:00:00Z')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE gmv_refund_observations SET refund_amount_minor = 200")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM gmv_refund_observations")


def test_only_one_active_scope_version_is_allowed(tmp_path):
    db_path = migrate_gmv_schema(_seed_revenue_database(tmp_path / "nbs.db")).db_path
    with sqlite3.connect(db_path) as conn:
        values = ("V-1", None, None, "g", "refund", "rules", "calc-1", "ACTIVE", "2026-08-20T00:00:00Z", "tester")
        conn.execute("INSERT INTO gmv_scope_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO gmv_scope_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("V-2", None, None, "g", "refund", "rules", "calc-2", "ACTIVE", "2026-08-20T00:00:00Z", "tester"),
            )
