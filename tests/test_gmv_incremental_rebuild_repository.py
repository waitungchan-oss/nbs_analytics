import sqlite3

from backend.services.gmv_refund_repository import GmvRefundRepository, migrate_gmv_schema


def _database(tmp_path):
    path = tmp_path / "gmv.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tour_data (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE others_data (id INTEGER PRIMARY KEY)")
    migrate_gmv_schema(path)
    return path


def _seed_versions_and_snapshots(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO gmv_refund_batches VALUES "
            "('B-1', 'refund.xlsx', 'file-1', 'normalized-1', 2, 2, 'CONFIRMED', 'preflight-1', '{}', 'rev-1', 'rules-1', '2026-08-27', 'tester')"
        )
        conn.executemany(
            "INSERT INTO gmv_refund_observations VALUES "
            "(?, 'B-1', ?, ?, ?, ?, NULL, '已退款', ?, 'HKD', NULL, NULL, '2026-08-27')",
            [("O-1", 1, "row-1", "F-1", "S-1", 100), ("O-2", 2, "row-2", "F-2", "S-2", 200)],
        )
        for version_id, status, calculation in (("V-BASE", "RETIRED", "calc-base"), ("V-NEW", "RETIRED", "calc-new")):
            conn.execute(
                "INSERT INTO gmv_scope_versions VALUES (?, 'B-1', NULL, 'rev-1', 'refund-1', 'rules-1', ?, ?, '2026-08-27', 'tester')",
                (version_id, calculation, status),
            )
        rows = []
        for result_id, receipt, dimension, observation_id, amount in (
            ("R-1-T", "S-1", "TOTAL_REFUND", "O-1", 100),
            ("R-1-P", "S-1", "REFUNDED", "O-1", 100),
            ("R-2-T", "S-2", "TOTAL_REFUND", "O-2", 200),
            ("R-2-P", "S-2", "REFUNDED", "O-2", 200),
        ):
            rows.append((result_id, "V-BASE", receipt, dimension, "FORMAL_MATCHED", "FORMAL_MATCHED", amount, amount, amount, 0, 1, "rev-1", "rules-1"))
        conn.executemany("INSERT INTO gmv_reconciliation_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany(
            "INSERT INTO gmv_reconciliation_members VALUES (?, ?, ?, ?)",
            [("R-1-T", "F-1", "O-1", 100), ("R-1-P", "F-1", "O-1", 100), ("R-2-T", "F-2", "O-2", 200), ("R-2-P", "F-2", "O-2", 200)],
        )
        conn.executemany(
            "INSERT INTO gmv_adjustment_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [("V-BASE", "tour_data", "FP-1", "S-1", "2026-08", None, 100, 100, 0, 1000000, "B-1", "SP-1", "旅費"),
             ("V-BASE", "tour_data", "FP-2", "S-2", "2026-08", None, 200, 200, 0, 1000000, "B-1", "SP-2", "旅費")],
        )


def test_copy_unaffected_snapshot_rows_creates_self_contained_new_version(tmp_path):
    path = _database(tmp_path)
    _seed_versions_and_snapshots(path)
    repository = GmvRefundRepository(path)

    with repository.connect() as conn:
        counts = repository.copy_unaffected_snapshot_rows(
            conn,
            base_version_id="V-BASE",
            new_version_id="V-NEW",
            affected_source_receipt_nos=("S-1",),
        )
        result_rows = conn.execute(
            "SELECT source_receipt_no, refund_dimension FROM gmv_reconciliation_results "
            "WHERE version_id = 'V-NEW' ORDER BY source_receipt_no, refund_dimension"
        ).fetchall()
        member_versions = conn.execute(
            "SELECT COUNT(*) FROM gmv_reconciliation_members AS m "
            "JOIN gmv_reconciliation_results AS r ON r.result_id = m.result_id "
            "WHERE r.version_id = 'V-NEW'"
        ).fetchone()[0]

    assert counts == {"results": 2, "members": 2, "adjustments": 1}
    assert result_rows == [("S-2", "REFUNDED"), ("S-2", "TOTAL_REFUND")]
    assert member_versions == 2


def test_snapshot_completeness_reports_missing_required_receipt(tmp_path):
    path = _database(tmp_path)
    _seed_versions_and_snapshots(path)
    repository = GmvRefundRepository(path)

    report = repository.load_snapshot_completeness(
        "V-BASE",
        required_source_receipt_nos=("S-1", "S-2", "S-3"),
    )

    assert report["complete"] is False
    assert report["missing_source_receipt_nos"] == ("S-3",)


def test_copy_rejects_nonempty_new_version(tmp_path):
    path = _database(tmp_path)
    _seed_versions_and_snapshots(path)
    repository = GmvRefundRepository(path)

    with repository.connect() as conn:
        repository.copy_unaffected_snapshot_rows(
            conn,
            base_version_id="V-BASE",
            new_version_id="V-NEW",
            affected_source_receipt_nos=("S-1",),
        )
        try:
            repository.copy_unaffected_snapshot_rows(
                conn,
                base_version_id="V-BASE",
                new_version_id="V-NEW",
                affected_source_receipt_nos=("S-1",),
            )
        except ValueError as exc:
            assert str(exc) == "new GMV version must be empty"
        else:
            raise AssertionError("copy must reject a non-empty new version")
