"""SQLite 連線、熱備份與 Upsert 防重覆寫入。"""

from __future__ import annotations

import shutil
import sqlite3
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import (
    BRANCH_REASSIGNMENT_OVERRIDES,
    COL_BRANCH,
    COL_DATE,
    COL_MONEY,
    COL_ORDER_ID,
    COL_RECEIPT_OPERATOR,
    COL_SALESPERSON,
    DB_FILE,
    TARGET_DEPT_FOR_REP,
)

COL_SUBTABLE_BRANCH = "副表_銷售點"
UPLOAD_EXCLUDED_RECEIPT_TYPES = {"掛賬核銷"}
UPLOAD_EXCLUDED_PAYMENT_METHODS = {"TT 退款轉團款"}


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path if db_path is not None else DB_FILE)


def get_db_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    return sqlite3.connect(resolve_db_path(db_path))


def _sqlite_backup_copy(source_path: Path, destination_path: Path) -> None:
    source_conn = sqlite3.connect(source_path)
    destination_conn = sqlite3.connect(destination_path)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def snapshot_sqlite_database(
    source_path: str | Path,
    destination_path: str | Path,
) -> None:
    source = resolve_db_path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _sqlite_backup_copy(source, destination)
    check = validate_sqlite_database(destination)
    if not check["ok"]:
        raise RuntimeError(f"snapshot integrity check failed: {check['integrity']}")


def hot_backup_database(db_path: str | Path | None = None) -> str | None:
    source = resolve_db_path(db_path)
    if not source.exists() or source.stat().st_size == 0:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = source.with_name(f"{source.name}.backup_{stamp}")
    snapshot_sqlite_database(source, backup_path)
    return str(backup_path)


def validate_sqlite_database(path: str | Path) -> dict:
    db_path = Path(path)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {"ok": False, "path": str(db_path), "integrity": "missing or empty database"}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "path": str(db_path), "integrity": str(exc)}
    return {"ok": integrity.lower() == "ok", "path": str(db_path), "integrity": integrity}


def restore_database_from_backup(
    backup_path: str | Path,
    *,
    live_db_path: str | Path | None = None,
) -> dict:
    live_path = resolve_db_path(live_db_path)
    source_path = Path(backup_path)
    source_check = validate_sqlite_database(source_path)
    if not source_check["ok"]:
        raise ValueError(f"backup integrity check failed: {source_check['integrity']}")
    if not live_path.exists():
        raise FileNotFoundError(f"live database not found: {live_path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    quarantine_path = live_path.with_name(f"{live_path.name}.quarantine_{stamp}")
    restore_temp = live_path.with_name(f".{live_path.name}.restore_{stamp}.tmp")
    _sqlite_backup_copy(live_path, quarantine_path)
    try:
        shutil.copy2(source_path, restore_temp)
        restored_check = validate_sqlite_database(restore_temp)
        if not restored_check["ok"]:
            raise ValueError(f"restored database integrity check failed: {restored_check['integrity']}")
        os.replace(restore_temp, live_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{live_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
    finally:
        if restore_temp.exists():
            restore_temp.unlink()

    final_check = validate_sqlite_database(live_path)
    if not final_check["ok"]:
        raise RuntimeError(f"live database integrity check failed after restore: {final_check['integrity']}")
    return {
        "status": "restored",
        "backup_path": str(source_path),
        "quarantine_path": str(quarantine_path),
        "integrity": final_check["integrity"],
    }


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()[0]
        == 1
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    if not _table_exists(conn, table_name):
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")]


def _sqlite_type(series: pd.Series) -> str:
    if pd.api.types.is_integer_dtype(series) or pd.api.types.is_float_dtype(series):
        return "REAL"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"
    return "TEXT"


def _append_compatible(conn: sqlite3.Connection, table_name: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    safe_df = df.copy()
    safe_df.columns = [str(col) for col in safe_df.columns]

    if not _table_exists(conn, table_name):
        safe_df.to_sql(table_name, conn, if_exists="append", index=False)
        return

    existing_cols = _table_columns(conn, table_name)
    for col in safe_df.columns:
        if col not in existing_cols:
            conn.execute(
                f"ALTER TABLE {_quote_identifier(table_name)} "
                f"ADD COLUMN {_quote_identifier(col)} {_sqlite_type(safe_df[col])}"
            )
            existing_cols.append(col)

    for col in existing_cols:
        if col not in safe_df.columns:
            safe_df[col] = None
    safe_df[existing_cols].to_sql(table_name, conn, if_exists="append", index=False)


def _table_stats(conn: sqlite3.Connection, table_name: str) -> dict:
    if not _table_exists(conn, table_name):
        return {"rows": 0, "max_date": None, "amount": 0.0}
    columns = set(_table_columns(conn, table_name))
    row_count = int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}").fetchone()[0])
    max_date = None
    amount = 0.0
    if COL_DATE in columns:
        max_date = conn.execute(f"SELECT MAX({_quote_identifier(COL_DATE)}) FROM {_quote_identifier(table_name)}").fetchone()[0]
    if COL_MONEY in columns:
        amount_value = conn.execute(f"SELECT SUM({_quote_identifier(COL_MONEY)}) FROM {_quote_identifier(table_name)}").fetchone()[0]
        amount = float(amount_value or 0)
    return {"rows": row_count, "max_date": max_date, "amount": amount}


def _unique_order_ids(df: pd.DataFrame) -> set[str]:
    if df.empty or COL_ORDER_ID not in df.columns:
        return set()
    ids = df[COL_ORDER_ID].dropna().astype(str).str.strip()
    return {value for value in ids if value}


def _existing_order_id_count(conn: sqlite3.Connection, table_name: str, order_ids: set[str]) -> int:
    if not order_ids or not _table_exists(conn, table_name) or COL_ORDER_ID not in _table_columns(conn, table_name):
        return 0
    temp_df = pd.DataFrame({COL_ORDER_ID: sorted(order_ids)})
    temp_df.to_sql("temp_ids", conn, if_exists="replace", index=False)
    return int(
        conn.execute(
            f"SELECT COUNT(DISTINCT {_quote_identifier(COL_ORDER_ID)}) "
            f"FROM {_quote_identifier(table_name)} "
            f"WHERE {_quote_identifier(COL_ORDER_ID)} IN (SELECT {_quote_identifier(COL_ORDER_ID)} FROM temp_ids)"
        ).fetchone()[0]
    )


def _delete_existing_ids(conn: sqlite3.Connection, table_name: str, df: pd.DataFrame) -> dict:
    order_ids = _unique_order_ids(df)
    result = {"input_rows": int(len(df)), "input_order_ids": len(order_ids), "overlap_order_ids": 0, "deleted_rows": 0}
    if df.empty or not order_ids:
        return result
    if not _table_exists(conn, table_name) or COL_ORDER_ID not in _table_columns(conn, table_name):
        return result
    result["overlap_order_ids"] = _existing_order_id_count(conn, table_name, order_ids)
    pd.DataFrame({COL_ORDER_ID: sorted(order_ids)}).to_sql("temp_ids", conn, if_exists="replace", index=False)
    cursor = conn.execute(
        f"DELETE FROM {_quote_identifier(table_name)} "
        f"WHERE {_quote_identifier(COL_ORDER_ID)} IN (SELECT {_quote_identifier(COL_ORDER_ID)} FROM temp_ids)"
    )
    result["deleted_rows"] = int(cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0)
    return result


def _upload_revenue_scope_excluded_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index)
    mask = pd.Series(False, index=df.index)
    if "收款類型" in df.columns:
        mask |= df["收款類型"].astype(str).str.strip().isin(UPLOAD_EXCLUDED_RECEIPT_TYPES)
    if "收款方式" in df.columns:
        mask |= df["收款方式"].astype(str).str.strip().isin(UPLOAD_EXCLUDED_PAYMENT_METHODS)
    return mask


def _existing_receipt_numbers(conn: sqlite3.Connection, table_name: str) -> set[str]:
    receipt_col = "收款單號"
    if not _table_exists(conn, table_name) or receipt_col not in _table_columns(conn, table_name):
        return set()
    rows = conn.execute(
        f"SELECT DISTINCT {_quote_identifier(receipt_col)} FROM {_quote_identifier(table_name)} "
        f"WHERE {_quote_identifier(receipt_col)} IS NOT NULL"
    ).fetchall()
    return {str(row[0]).strip() for row in rows if str(row[0]).strip()}


def _filter_upload_revenue_scope_rows(df: pd.DataFrame, existing_receipt_numbers: set[str]) -> tuple[pd.DataFrame, dict]:
    mask = _upload_revenue_scope_excluded_mask(df)
    if "收款單號" in df.columns and existing_receipt_numbers:
        receipts = df["收款單號"].astype(str).str.strip()
        mask &= ~receipts.isin(existing_receipt_numbers)
    filtered = df.loc[~mask].copy()
    return filtered, {
        "input_rows": int(len(df)),
        "filtered_excluded_rows": int(mask.sum()),
        "write_rows": int(len(filtered)),
    }


def upsert_to_db(
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    *,
    db_path: str | Path | None = None,
) -> dict:
    """將清洗後的新數據安全追加至資料庫，依據來源單據號防重覆。"""
    target = resolve_db_path(db_path)
    backup_path = hot_backup_database(target)
    conn = get_db_connection(target)
    try:
        df_tour, tour_filter = _filter_upload_revenue_scope_rows(df_tour, _existing_receipt_numbers(conn, "tour_data"))
        df_others, others_filter = _filter_upload_revenue_scope_rows(df_others, _existing_receipt_numbers(conn, "others_data"))
        before_tour = _table_stats(conn, "tour_data")
        before_others = _table_stats(conn, "others_data")
        tour_delete = _delete_existing_ids(conn, "tour_data", df_tour)
        _append_compatible(conn, "tour_data", df_tour)
        others_delete = _delete_existing_ids(conn, "others_data", df_others)
        _append_compatible(conn, "others_data", df_others)
        conn.commit()
        after_tour = _table_stats(conn, "tour_data")
        after_others = _table_stats(conn, "others_data")
        return {
            "backup_path": backup_path,
            "tour_data": {
                "before": before_tour,
                "after": after_tour,
                **tour_delete,
                "input_rows": tour_filter["input_rows"],
                "filtered_excluded_rows": tour_filter["filtered_excluded_rows"],
                "write_rows": tour_filter["write_rows"],
                "inserted_rows": int(len(df_tour)),
            },
            "others_data": {
                "before": before_others,
                "after": after_others,
                **others_delete,
                "input_rows": others_filter["input_rows"],
                "filtered_excluded_rows": others_filter["filtered_excluded_rows"],
                "write_rows": others_filter["write_rows"],
                "inserted_rows": int(len(df_others)),
            },
        }
    finally:
        conn.close()


def load_all_data_from_db(
    *,
    db_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = get_db_connection(db_path)
    try:
        df_tour, df_others = pd.DataFrame(), pd.DataFrame()
        if _table_exists(conn, "tour_data"):
            df_tour = pd.read_sql("SELECT * FROM tour_data", conn)
        if _table_exists(conn, "others_data"):
            df_others = pd.read_sql("SELECT * FROM others_data", conn)
        return df_tour, df_others
    finally:
        conn.close()


def repair_operator_sales_rep_assignments(sales_rep_list: list[str]) -> dict:
    """依收款操作員修復既有資料的專職銷售代表歸屬。"""
    from pipeline import match_sales_rep_by_operator

    required_cols = {COL_RECEIPT_OPERATOR, COL_BRANCH, COL_SALESPERSON}
    conn = get_db_connection()
    updates: list[tuple[str, int, str]] = []
    try:
        for table_name in ("tour_data", "others_data"):
            if not _table_exists(conn, table_name):
                continue
            if not required_cols.issubset(set(_table_columns(conn, table_name))):
                continue
            df = pd.read_sql(f"SELECT rowid AS __rowid__, * FROM {_quote_identifier(table_name)}", conn)
            for _, row in df.iterrows():
                matched_rep = match_sales_rep_by_operator(row.get(COL_RECEIPT_OPERATOR), sales_rep_list)
                if not matched_rep:
                    continue
                branch = str(row.get(COL_BRANCH, "")).strip()
                salesperson = str(row.get(COL_SALESPERSON, "")).strip()
                if branch == TARGET_DEPT_FOR_REP and salesperson == matched_rep:
                    continue
                updates.append((table_name, int(row["__rowid__"]), matched_rep))
    finally:
        conn.close()

    if not updates:
        return {"updated": 0, "backup": None}

    backup_path = hot_backup_database()
    conn = get_db_connection()
    try:
        for table_name, rowid, matched_rep in updates:
            conn.execute(
                f"UPDATE {_quote_identifier(table_name)} "
                f"SET {_quote_identifier(COL_BRANCH)} = ?, {_quote_identifier(COL_SALESPERSON)} = ? "
                "WHERE rowid = ?",
                (TARGET_DEPT_FOR_REP, matched_rep, rowid),
            )
        conn.commit()
    finally:
        conn.close()
    return {"updated": len(updates), "backup": backup_path}


def _clean_branch_value(value) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _row_periods(row, table_cols: set[str]) -> tuple[str, str]:
    for column in ("統一日期", COL_DATE, "收款時間"):
        if column in table_cols:
            parsed = pd.to_datetime(row.get(column), errors="coerce")
            if pd.notna(parsed):
                return parsed.strftime("%Y-%m"), parsed.strftime("%Y")
    return "", ""


def _branch_reassignment_target(row, table_cols: set[str]) -> str | None:
    current_branch = _clean_branch_value(row.get(COL_BRANCH))
    sub_branch = _clean_branch_value(row.get(COL_SUBTABLE_BRANCH))
    source_id = _clean_branch_value(row.get(COL_ORDER_ID)).upper()
    month, year = _row_periods(row, table_cols)
    for override in BRANCH_REASSIGNMENT_OVERRIDES or []:
        to_branch = _clean_branch_value(override.get("to_branch"))
        if not to_branch:
            continue
        override_month = _clean_branch_value(override.get("month"))
        if override_month and month != override_month:
            continue
        override_year = _clean_branch_value(override.get("year"))
        if override_year and year != override_year:
            continue
        from_branch = _clean_branch_value(override.get("from_branch"))
        if from_branch and current_branch != from_branch and sub_branch != from_branch:
            continue
        from_prefix = _clean_branch_value(override.get("from_prefix")).upper()
        if from_prefix and not source_id.startswith(from_prefix):
            continue
        override_source_order_id = _clean_branch_value(override.get("source_order_id")).upper()
        if override_source_order_id and source_id != override_source_order_id:
            continue
        return to_branch
    return None


def repair_subtable_branch_assignments(sales_rep_list: list[str]) -> dict:
    """依副表銷售點修復既有資料歸屬，並保留收款操作員命中專職的最高優先級。"""
    from pipeline import match_sales_rep_by_operator

    conn = get_db_connection()
    updates: list[tuple[str, int, str, str | None]] = []
    try:
        for table_name in ("tour_data", "others_data"):
            if not _table_exists(conn, table_name):
                continue
            table_cols = set(_table_columns(conn, table_name))
            if not {COL_BRANCH, COL_SUBTABLE_BRANCH}.issubset(table_cols):
                continue

            df = pd.read_sql(f"SELECT rowid AS __rowid__, * FROM {_quote_identifier(table_name)}", conn)
            for _, row in df.iterrows():
                sub_branch = str(row.get(COL_SUBTABLE_BRANCH, "") or "").replace("\u3000", " ").strip()
                if not sub_branch:
                    continue

                matched_rep = None
                if COL_RECEIPT_OPERATOR in table_cols:
                    matched_rep = match_sales_rep_by_operator(row.get(COL_RECEIPT_OPERATOR), sales_rep_list)

                reassigned_branch = _branch_reassignment_target(row, table_cols)
                desired_branch = TARGET_DEPT_FOR_REP if matched_rep else (reassigned_branch or sub_branch)
                desired_sales = matched_rep if matched_rep and COL_SALESPERSON in table_cols else None

                current_branch = str(row.get(COL_BRANCH, "") or "").replace("\u3000", " ").strip()
                current_sales = str(row.get(COL_SALESPERSON, "") or "").replace("\u3000", " ").strip()
                branch_ok = current_branch == desired_branch
                sales_ok = desired_sales is None or current_sales == desired_sales
                if branch_ok and sales_ok:
                    continue

                updates.append((table_name, int(row["__rowid__"]), desired_branch, desired_sales))
    finally:
        conn.close()

    if not updates:
        return {"updated": 0, "backup": None}

    backup_path = hot_backup_database()
    conn = get_db_connection()
    try:
        for table_name, rowid, desired_branch, desired_sales in updates:
            if desired_sales is None:
                conn.execute(
                    f"UPDATE {_quote_identifier(table_name)} "
                    f"SET {_quote_identifier(COL_BRANCH)} = ? "
                    "WHERE rowid = ?",
                    (desired_branch, rowid),
                )
            else:
                conn.execute(
                    f"UPDATE {_quote_identifier(table_name)} "
                    f"SET {_quote_identifier(COL_BRANCH)} = ?, {_quote_identifier(COL_SALESPERSON)} = ? "
                    "WHERE rowid = ?",
                    (desired_branch, desired_sales, rowid),
                )
        conn.commit()
    finally:
        conn.close()
    return {"updated": len(updates), "backup": backup_path}


def clear_database() -> None:
    conn = get_db_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS tour_data")
        conn.execute("DROP TABLE IF EXISTS others_data")
        conn.execute("DROP TABLE IF EXISTS temp_ids")
        conn.commit()
    finally:
        conn.close()
