import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import database


def _write_rows(path: Path, values: list[str]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sample (value TEXT)")
        conn.execute("DELETE FROM sample")
        conn.executemany("INSERT INTO sample(value) VALUES (?)", [(value,) for value in values])
        conn.commit()
    finally:
        conn.close()


def _read_rows(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT value FROM sample ORDER BY rowid")]
    finally:
        conn.close()


def test_restore_database_preserves_drifted_copy_and_restores_backup(tmp_path, monkeypatch):
    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))
    _write_rows(live_path, ["verified"])
    backup_path = database.hot_backup_database()
    _write_rows(live_path, ["drifted"])

    result = database.restore_database_from_backup(backup_path)

    assert result["status"] == "restored"
    assert _read_rows(live_path) == ["verified"]
    quarantine_path = Path(result["quarantine_path"])
    assert quarantine_path.exists()
    assert _read_rows(quarantine_path) == ["drifted"]
    assert database.validate_sqlite_database(live_path)["ok"] is True


def test_restore_database_rejects_corrupt_backup_before_replacing_live_db(tmp_path, monkeypatch):
    live_path = tmp_path / "live.db"
    corrupt_path = tmp_path / "corrupt.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))
    _write_rows(live_path, ["keep-me"])
    corrupt_path.write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(ValueError, match="backup integrity check failed"):
        database.restore_database_from_backup(str(corrupt_path))

    assert _read_rows(live_path) == ["keep-me"]


def test_upsert_filters_new_excluded_receipt_rows_without_deleting_existing_non_excluded_source_order(tmp_path, monkeypatch):
    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))
    existing = pd.DataFrame(
        [
            {
                "來源單據號": "225JIA6515114503",
                "收款單號": "SK2605003958",
                "收款類型": "旅費",
                "收款方式": "AC 掛賬",
                "收款原幣金額": 15000.0,
                "統一日期": "2026-05-19",
            }
        ]
    )
    conn = sqlite3.connect(live_path)
    try:
        existing.to_sql("tour_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()
    upload = pd.DataFrame(
        [
            {
                "來源單據號": "225JIA6515114503",
                "收款單號": "SK2605003958",
                "收款類型": "旅費",
                "收款方式": "AC 掛賬",
                "收款原幣金額": 15000.0,
                "統一日期": "2026-05-19",
            },
            {
                "來源單據號": "225JIA6515114503",
                "收款單號": "SK2606004815",
                "收款類型": "旅費",
                "收款方式": "BDR 銀行入數紙",
                "收款原幣金額": 70260.0,
                "統一日期": "2026-06-25",
            },
            {
                "來源單據號": "225JIA6515114503",
                "收款單號": "SK2606004814",
                "收款類型": "掛賬核銷",
                "收款方式": "BDR 銀行入數紙",
                "收款原幣金額": 15000.0,
                "統一日期": "2026-06-25",
            },
        ]
    )

    result = database.upsert_to_db(upload, pd.DataFrame())
    rows, _ = database.load_all_data_from_db()

    assert rows["收款單號"].tolist() == ["SK2605003958", "SK2606004815"]
    assert rows["收款原幣金額"].sum() == 85260.0
    assert "SK2606004814" not in set(rows["收款單號"])
    assert result["tour_data"]["input_rows"] == 3
    assert result["tour_data"]["filtered_excluded_rows"] == 1


def test_upsert_preserves_existing_excluded_receipts_from_full_snapshot_upload(tmp_path, monkeypatch):
    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))
    existing = pd.DataFrame(
        [
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2605000001",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-01",
            },
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2605000002",
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-02",
            },
        ]
    )
    conn = sqlite3.connect(live_path)
    try:
        existing.to_sql("tour_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()
    upload = pd.DataFrame(
        [
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2605000001",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-01",
            },
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2605000002",
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-02",
            },
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2606000003",
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "收款原幣金額": 500.0,
                "統一日期": "2026-06-25",
            },
        ]
    )

    result = database.upsert_to_db(upload, pd.DataFrame())
    rows, _ = database.load_all_data_from_db()

    assert rows["收款單號"].tolist() == ["SK2605000001", "SK2605000002"]
    assert result["tour_data"]["input_rows"] == 3
    assert result["tour_data"]["filtered_excluded_rows"] == 1
    assert result["tour_data"]["write_rows"] == 2


def test_repair_subtable_branch_assignments_keeps_2026_06_e6_reassigned_to_0a_only(tmp_path, monkeypatch):
    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))
    monkeypatch.setattr(
        database,
        "BRANCH_REASSIGNMENT_OVERRIDES",
        [
            {
                "month": "2026-06",
                "from_prefix": "E6",
                "from_branch": "上環服務點",
                "to_branch": "展覽會場專用",
                "to_prefix": "0A",
            }
        ],
        raising=False,
    )
    existing = pd.DataFrame(
        [
            {
                "來源單據號": "E6TEST2026001",
                "收款單號": "SK2606000001",
                "銷售點": "展覽會場專用",
                "副表_銷售點": "上環服務點",
                "收款時間": "2026-06-15",
                "統一日期": "2026-06-15",
                "收款操作員": "",
                "銷售員": "",
            },
            {
                "來源單據號": "E6TEST2026071",
                "收款單號": "SK2607000001",
                "銷售點": "展覽會場專用",
                "副表_銷售點": "上環服務點",
                "收款時間": "2026-07-15",
                "統一日期": "2026-07-15",
                "收款操作員": "",
                "銷售員": "",
            },
        ]
    )
    conn = sqlite3.connect(live_path)
    try:
        existing.to_sql("tour_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

    result = database.repair_subtable_branch_assignments([])
    rows, _ = database.load_all_data_from_db()

    assert result["updated"] == 1
    by_order = rows.set_index("來源單據號")["銷售點"].to_dict()
    assert by_order["E6TEST2026001"] == "展覽會場專用"
    assert by_order["E6TEST2026071"] == "上環服務點"


def test_repair_subtable_branch_assignments_matches_one_exact_source_order_only(tmp_path, monkeypatch):
    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))
    monkeypatch.setattr(
        database,
        "BRANCH_REASSIGNMENT_OVERRIDES",
        [
            {
                "month": "2026-06",
                "source_order_id": "E9MF16613172500",
                "from_branch": "上環服務點",
                "to_branch": "展覽會場專用",
                "to_prefix": "0A",
            }
        ],
        raising=False,
    )
    existing = pd.DataFrame(
        [
            {
                "來源單據號": "E9MF16613172500",
                "收款單號": "SK2606000001",
                "銷售點": "上環服務點",
                "副表_銷售點": "上環服務點",
                "收款時間": "2026-06-13",
                "統一日期": "2026-06-13",
                "收款操作員": "",
                "銷售員": "",
            },
            {
                "來源單據號": "E9OTHER202606",
                "收款單號": "SK2606000002",
                "銷售點": "上環服務點",
                "副表_銷售點": "上環服務點",
                "收款時間": "2026-06-13",
                "統一日期": "2026-06-13",
                "收款操作員": "",
                "銷售員": "",
            },
            {
                "來源單據號": "E9MF16613172500",
                "收款單號": "SK2607000001",
                "銷售點": "上環服務點",
                "副表_銷售點": "上環服務點",
                "收款時間": "2026-07-13",
                "統一日期": "2026-07-13",
                "收款操作員": "",
                "銷售員": "",
            },
            {
                "來源單據號": "E9MF16613172500",
                "收款單號": "SK2606000003",
                "銷售點": "元朗服務點",
                "副表_銷售點": "元朗服務點",
                "收款時間": "2026-06-13",
                "統一日期": "2026-06-13",
                "收款操作員": "",
                "銷售員": "",
            },
        ]
    )
    conn = sqlite3.connect(live_path)
    try:
        existing.to_sql("others_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

    result = database.repair_subtable_branch_assignments([])
    _, rows = database.load_all_data_from_db()

    assert result["updated"] == 1
    by_receipt = rows.set_index("收款單號")["銷售點"].to_dict()
    assert by_receipt == {
        "SK2606000001": "展覽會場專用",
        "SK2606000002": "上環服務點",
        "SK2607000001": "上環服務點",
        "SK2606000003": "元朗服務點",
    }
