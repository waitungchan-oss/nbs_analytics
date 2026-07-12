import sqlite3

import pandas as pd

import database


def _seed(path, order_id):
    conn = sqlite3.connect(path)
    try:
        pd.DataFrame(
            [{
                "來源單據號": order_id,
                "收款單號": order_id,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "收款原幣金額": 100.0,
                "統一日期": "2026-07-01",
            }]
        ).to_sql("tour_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()


def test_explicit_database_load_does_not_change_default_target(tmp_path, monkeypatch):
    default_path = tmp_path / "default.db"
    alternate_path = tmp_path / "alternate.db"
    _seed(default_path, "DEFAULT001")
    _seed(alternate_path, "ALT001")
    monkeypatch.setattr(database, "DB_FILE", str(default_path))

    alternate_tour, _ = database.load_all_data_from_db(db_path=alternate_path)
    default_tour, _ = database.load_all_data_from_db()

    assert alternate_tour["來源單據號"].tolist() == ["ALT001"]
    assert default_tour["來源單據號"].tolist() == ["DEFAULT001"]
    assert database.DB_FILE == str(default_path)


def test_snapshot_sqlite_database_is_integrity_checked(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    _seed(source, "SNAP001")

    database.snapshot_sqlite_database(source, destination)

    assert database.validate_sqlite_database(destination)["ok"] is True
    snapshot_tour, _ = database.load_all_data_from_db(db_path=destination)
    assert snapshot_tour["來源單據號"].tolist() == ["SNAP001"]
