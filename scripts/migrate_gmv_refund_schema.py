"""Explicit, backup-first migration for the formal GMV SQLite ledger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.gmv_refund_repository import GmvRefundRepository, migrate_gmv_schema
from database import hot_backup_database, validate_sqlite_database


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = args.db_path.expanduser().resolve()
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2
    integrity = validate_sqlite_database(db_path)
    if not integrity["ok"]:
        print(f"database integrity failed: {integrity['integrity']}", file=sys.stderr)
        return 2

    repository = GmvRefundRepository(db_path)
    before = repository.validate_schema()
    if args.dry_run:
        print(
            f"DRY_RUN db_path={db_path} ready={before.ready} "
            f"missing={','.join(before.missing_objects) or 'none'}"
        )
        return 0

    backup_path = hot_backup_database(db_path)
    if not backup_path:
        print("backup failed: database is empty", file=sys.stderr)
        return 2
    try:
        result = migrate_gmv_schema(db_path)
        after = GmvRefundRepository(db_path).validate_schema()
        integrity_after = validate_sqlite_database(db_path)
        if not after.ready or not integrity_after["ok"]:
            raise RuntimeError(
                f"post-migration validation failed: missing={after.missing_objects}, "
                f"integrity={integrity_after['integrity']}"
            )
    except Exception as exc:
        print(f"migration failed; backup={backup_path}; error={exc}", file=sys.stderr)
        return 1

    print(f"APPLIED db_path={result.db_path} backup={backup_path} created={result.created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
