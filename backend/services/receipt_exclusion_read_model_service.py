from __future__ import annotations

import sqlite3

import database
from backend.services.receipt_exclusion_registry_service import (
    EVENTS_TABLE,
    REGISTRY_TABLE,
    list_receipt_exclusions,
    load_active_registry_snapshot,
)


def _event_counts(*, db_path, rule_ids: list[int]) -> dict[int, int]:
    if not rule_ids:
        return {}
    path = database.resolve_db_path(db_path)
    if not path.exists():
        return {}
    placeholders = ",".join("?" for _ in rule_ids)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if REGISTRY_TABLE not in tables or EVENTS_TABLE not in tables:
            return {}
        return {
            int(row[0]): int(row[1])
            for row in conn.execute(
                f"SELECT registry_id, count(*) FROM {EVENTS_TABLE} WHERE registry_id IN ({placeholders}) GROUP BY registry_id",
                rule_ids,
            )
        }
    finally:
        conn.close()


def _status_counts(*, db_path) -> dict[str, int]:
    path = database.resolve_db_path(db_path)
    if not path.exists():
        return {"active": 0, "revoked": 0}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if REGISTRY_TABLE not in tables:
            return {"active": 0, "revoked": 0}
        counted = {str(row[0]): int(row[1]) for row in conn.execute(
            f"SELECT status, count(*) FROM {REGISTRY_TABLE} GROUP BY status"
        )}
        return {"active": counted.get("active", 0), "revoked": counted.get("revoked", 0)}
    finally:
        conn.close()


def build_receipt_exclusion_read_model(*, db_path, limit: int = 100) -> dict:
    bounded = max(1, min(int(limit), 100))
    active = list_receipt_exclusions(status="active", limit=bounded, db_path=db_path)
    revoked = list_receipt_exclusions(status="revoked", limit=bounded, db_path=db_path)
    counts = _event_counts(db_path=db_path, rule_ids=[row["id"] for row in active + revoked])
    for row in active + revoked:
        row["eventCount"] = counts.get(row["id"], 0)
    return {
        "schemaVersion": "receipt-exclusion-read-model-v1",
        "registryRevision": load_active_registry_snapshot(db_path=db_path)["revision"],
        "active": active,
        "revoked": revoked,
        "counts": _status_counts(db_path=db_path),
    }
