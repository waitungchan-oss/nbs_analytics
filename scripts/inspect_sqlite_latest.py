"""Inspect latest SQLite dates and recent daily totals for NBS Analytics."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import COL_DATE, COL_MONEY, DB_FILE  # noqa: E402


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_summary(conn: sqlite3.Connection, table_name: str) -> dict[str, Any]:
    if not _table_exists(conn, table_name):
        return {"exists": False, "rows": 0, "max_date": None, "amount": 0.0}
    rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table_name)}").fetchone()[0])
    max_date = conn.execute(f"SELECT MAX({_quote(COL_DATE)}) FROM {_quote(table_name)}").fetchone()[0]
    amount = conn.execute(f"SELECT SUM({_quote(COL_MONEY)}) FROM {_quote(table_name)}").fetchone()[0]
    return {"exists": True, "rows": rows, "max_date": max_date, "amount": round(float(amount or 0), 2)}


def _recent_daily(conn: sqlite3.Connection, table_name: str, days: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, table_name):
        return []
    query = f"""
        SELECT substr({_quote(COL_DATE)}, 1, 10) AS date,
               COUNT(*) AS rows,
               ROUND(SUM({_quote(COL_MONEY)}), 2) AS amount
        FROM {_quote(table_name)}
        WHERE {_quote(COL_DATE)} IS NOT NULL
        GROUP BY substr({_quote(COL_DATE)}, 1, 10)
        ORDER BY date DESC
        LIMIT ?
    """
    return [
        {"table": table_name, "date": row[0], "rows": int(row[1]), "amount": float(row[2] or 0)}
        for row in conn.execute(query, (days,)).fetchall()
    ]


def _inspect_db(path: Path, days: int) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    conn = _connect(path)
    try:
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": path.stat().st_size,
            "tables": {
                "tour_data": _table_summary(conn, "tour_data"),
                "others_data": _table_summary(conn, "others_data"),
            },
            "recent_daily": _recent_daily(conn, "tour_data", days) + _recent_daily(conn, "others_data", days),
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect latest NBS SQLite data and backups.")
    parser.add_argument("--days", type=int, default=10, help="Number of recent daily rows per table to show.")
    parser.add_argument("--backups", type=int, default=5, help="Number of latest DB backups to compare.")
    args = parser.parse_args()

    db_path = Path(DB_FILE)
    backups = sorted(db_path.parent.glob(f"{db_path.name}.backup_*"), key=lambda item: item.stat().st_mtime, reverse=True)
    result = {
        "current": _inspect_db(db_path, args.days),
        "latest_backups": [_inspect_db(path, args.days) for path in backups[: max(0, args.backups)]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["current"].get("exists") else 1


if __name__ == "__main__":
    raise SystemExit(main())
