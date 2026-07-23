from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import database
from backend.services.receipt_exclusion_matcher import normalize_identity_text
from backend.services.receipt_exclusion_models import (
    ReceiptExclusionIdentity,
    ReceiptExclusionRule,
    canonical_json_hash,
)


REGISTRY_TABLE = "receipt_exclusion_registry"
QUARANTINE_TABLE = "receipt_exclusion_quarantine"
EVENTS_TABLE = "receipt_exclusion_events"
MAX_EVENT_JSON_CHARS = 20_000
MAX_QUARANTINE_JSON_CHARS = 50_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dump(value: Any, *, max_chars: int) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if len(encoded) > max_chars:
        raise ValueError("receipt exclusion payload exceeds the allowed size")
    return encoded


def _json_load(value: str) -> dict:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("receipt exclusion payload must be a JSON object")
    return loaded


def _normalize_kind(value: object) -> str:
    return str(value or "").replace("\u3000", " ").replace("\xa0", " ").strip()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {REGISTRY_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no_norm TEXT NOT NULL,
            source_order_no_norm TEXT NOT NULL,
            exclusion_kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
            reason TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            proposal_fingerprint TEXT NOT NULL,
            created_operation_id TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_operation_id TEXT,
            revoked_by TEXT,
            revoked_at TEXT,
            UNIQUE(receipt_no_norm, source_order_no_norm, exclusion_kind)
        );
        CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id INTEGER NOT NULL,
            operation_id TEXT NOT NULL,
            source_file_name TEXT NOT NULL,
            source_file_sha256 TEXT NOT NULL,
            raw_payload_json TEXT NOT NULL,
            raw_row_hash TEXT NOT NULL,
            prepared_payload_json TEXT NOT NULL,
            prepared_row_hash TEXT NOT NULL,
            observed_amount REAL NOT NULL,
            observed_at TEXT NOT NULL,
            FOREIGN KEY(registry_id) REFERENCES {REGISTRY_TABLE}(id)
        );
        CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id INTEGER,
            operation_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'activated', 'activation_rejected', 'auto_applied',
                'collision_blocked', 'revocation_preview_passed',
                'revocation_preview_failed', 'revoked'
            )),
            proposal_fingerprint TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(registry_id) REFERENCES {REGISTRY_TABLE}(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_receipt_exclusion_auto_event
        ON {EVENTS_TABLE}(operation_id, registry_id, event_type, proposal_fingerprint);
        """
    )


def _identity_from_candidate(candidate: dict) -> ReceiptExclusionIdentity:
    identity = ReceiptExclusionIdentity(
        receipt_no=normalize_identity_text(candidate.get("receiptNo")),
        source_order_no=normalize_identity_text(candidate.get("sourceOrderNo")),
        exclusion_kind=_normalize_kind(candidate.get("exclusionKind")),
    )
    if not all((identity.receipt_no, identity.source_order_no, identity.exclusion_kind)):
        raise ValueError("receipt exclusion candidate identity is incomplete")
    return identity


def _candidate_evidence(candidate: dict) -> dict:
    raw_payload = candidate.get("rawPayload")
    prepared_payload = candidate.get("preparedPayload")
    if not isinstance(raw_payload, dict) or not isinstance(prepared_payload, dict):
        raise ValueError("receipt exclusion candidate evidence is incomplete")
    raw_json = _json_dump(raw_payload, max_chars=MAX_QUARANTINE_JSON_CHARS)
    prepared_json = _json_dump(prepared_payload, max_chars=MAX_QUARANTINE_JSON_CHARS)
    source_name = Path(str(candidate.get("sourceFileName") or "")).name
    if not source_name or source_name == ".":
        raise ValueError("receipt exclusion source file name is required")
    return {
        "rawPayload": raw_payload,
        "rawJson": raw_json,
        "rawRowHash": str(candidate.get("rawRowHash") or ""),
        "preparedPayload": prepared_payload,
        "preparedJson": prepared_json,
        "preparedRowHash": str(candidate.get("preparedRowHash") or ""),
        "sourceFileName": source_name,
        "sourceFileSha256": str(candidate.get("sourceFileSha256") or ""),
        "observedAmount": float(candidate.get("observedAmount") or 0),
    }


def _insert_quarantine(
    conn: sqlite3.Connection,
    *,
    registry_id: int,
    operation_id: str,
    evidence: dict,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {QUARANTINE_TABLE} (
            registry_id, operation_id, source_file_name, source_file_sha256,
            raw_payload_json, raw_row_hash, prepared_payload_json, prepared_row_hash,
            observed_amount, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            registry_id,
            operation_id,
            evidence["sourceFileName"],
            evidence["sourceFileSha256"],
            evidence["rawJson"],
            evidence["rawRowHash"],
            evidence["preparedJson"],
            evidence["preparedRowHash"],
            evidence["observedAmount"],
            _now(),
        ),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    registry_id: int | None,
    operation_id: str,
    event_type: str,
    proposal_fingerprint: str,
    payload: dict,
) -> int:
    cursor = conn.execute(
        f"""
        INSERT INTO {EVENTS_TABLE} (
            registry_id, operation_id, event_type, proposal_fingerprint, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            registry_id,
            operation_id,
            event_type,
            proposal_fingerprint,
            _json_dump(payload, max_chars=MAX_EVENT_JSON_CHARS),
            _now(),
        ),
    )
    return int(cursor.lastrowid)


def _registry_rows(conn: sqlite3.Connection, *, status: str | None = "active") -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    where = ""
    args: tuple[object, ...] = ()
    if status is not None:
        where = " WHERE status = ?"
        args = (status,)
    return list(conn.execute(
        f"""
        SELECT id, receipt_no_norm, source_order_no_norm, exclusion_kind, status, reason,
               evidence_hash, proposal_fingerprint, created_operation_id, created_by, created_at,
               revoked_operation_id, revoked_by, revoked_at
        FROM {REGISTRY_TABLE}{where}
        ORDER BY receipt_no_norm, source_order_no_norm, exclusion_kind, id
        """,
        args,
    ))


def load_active_registry_snapshot(*, db_path) -> dict:
    path = database.resolve_db_path(db_path)
    if not path.exists():
        return {"revision": canonical_json_hash([]), "rules": ()}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        present = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (REGISTRY_TABLE,),
        ).fetchone()[0]
        if not present:
            return {"revision": canonical_json_hash([]), "rules": ()}
        rows = _registry_rows(conn)
        revision_rows = [
            {
                "id": row["id"],
                "receiptNo": row["receipt_no_norm"],
                "sourceOrderNo": row["source_order_no_norm"],
                "exclusionKind": row["exclusion_kind"],
            }
            for row in rows
        ]
        rules = tuple(
            ReceiptExclusionRule(
                id=int(row["id"]),
                identity=ReceiptExclusionIdentity(
                    receipt_no=row["receipt_no_norm"],
                    source_order_no=row["source_order_no_norm"],
                    exclusion_kind=row["exclusion_kind"],
                ),
                status=row["status"],
            )
            for row in rows
        )
        return {"revision": canonical_json_hash(revision_rows), "rules": rules}
    finally:
        conn.close()


def activate_receipt_exclusions(
    candidates: Iterable[dict],
    *,
    operation_id: str,
    created_by: str,
    proposal_fingerprint: str,
    db_path,
) -> dict:
    candidates = list(candidates)
    if not candidates:
        raise ValueError("at least one receipt exclusion candidate is required")
    conn = database.get_db_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # Additive schema bootstrap is infrastructure; rule, evidence and event rows share the transaction below.
        _ensure_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        rule_ids: list[int] = []
        activated_ids: list[int] = []
        for candidate in candidates:
            identity = _identity_from_candidate(candidate)
            evidence = _candidate_evidence(candidate)
            existing = conn.execute(
                f"""
                SELECT id, status FROM {REGISTRY_TABLE}
                WHERE receipt_no_norm = ? AND source_order_no_norm = ? AND exclusion_kind = ?
                """,
                (identity.receipt_no, identity.source_order_no, identity.exclusion_kind),
            ).fetchone()
            if existing is not None:
                rule_id, status = int(existing[0]), str(existing[1])
                if status != "active":
                    raise ValueError("receipt exclusion rule is revoked and cannot be reactivated")
                rule_ids.append(rule_id)
                continue
            evidence_hash = canonical_json_hash({
                "candidateId": str(candidate.get("candidateId") or identity.candidate_id),
                "rawRowHash": evidence["rawRowHash"],
                "preparedRowHash": evidence["preparedRowHash"],
                "sourceFileSha256": evidence["sourceFileSha256"],
            })
            cursor = conn.execute(
                f"""
                INSERT INTO {REGISTRY_TABLE} (
                    receipt_no_norm, source_order_no_norm, exclusion_kind, status, reason,
                    evidence_hash, proposal_fingerprint, created_operation_id, created_by, created_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.receipt_no,
                    identity.source_order_no,
                    identity.exclusion_kind,
                    str(candidate.get("reason") or "confirmed exact excluded receipt"),
                    evidence_hash,
                    proposal_fingerprint,
                    operation_id,
                    created_by,
                    _now(),
                ),
            )
            rule_id = int(cursor.lastrowid)
            _insert_quarantine(
                conn,
                registry_id=rule_id,
                operation_id=operation_id,
                evidence=evidence,
            )
            _insert_event(
                conn,
                registry_id=rule_id,
                operation_id=operation_id,
                event_type="activated",
                proposal_fingerprint=proposal_fingerprint,
                payload={"candidateId": str(candidate.get("candidateId") or identity.candidate_id)},
            )
            rule_ids.append(rule_id)
            activated_ids.append(rule_id)
        conn.commit()
        return {
            "status": "activated" if activated_ids else "already_active",
            "ruleIds": rule_ids,
            "revision": load_active_registry_snapshot(db_path=db_path)["revision"],
        }
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_auto_applied_events(events: Iterable[dict], *, operation_id: str, db_path) -> list[int]:
    conn = database.get_db_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # Schema bootstrap is separate from the all-or-nothing audit event transaction.
        _ensure_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        event_ids: list[int] = []
        for event in events:
            registry_id = int(event["registryId"])
            fingerprint = str(event["proposalFingerprint"])
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("auto-applied event payload must be an object")
            cursor = conn.execute(
                f"""
                INSERT OR IGNORE INTO {EVENTS_TABLE} (
                    registry_id, operation_id, event_type, proposal_fingerprint, payload_json, created_at
                ) VALUES (?, ?, 'auto_applied', ?, ?, ?)
                """,
                (registry_id, operation_id, fingerprint, _json_dump(payload, max_chars=MAX_EVENT_JSON_CHARS), _now()),
            )
            if cursor.rowcount:
                event_ids.append(int(cursor.lastrowid))
            else:
                existing = conn.execute(
                    f"""
                    SELECT id FROM {EVENTS_TABLE}
                    WHERE operation_id = ? AND registry_id = ? AND event_type = 'auto_applied'
                      AND proposal_fingerprint = ?
                    """,
                    (operation_id, registry_id, fingerprint),
                ).fetchone()
                event_ids.append(int(existing[0]))
        conn.commit()
        return event_ids
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_receipt_exclusions(*, status: str | None = None, limit: int = 100, db_path) -> list[dict]:
    if status not in {None, "active", "revoked"}:
        raise ValueError("receipt exclusion status is invalid")
    bounded_limit = max(1, min(int(limit), 100))
    path = database.resolve_db_path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        present = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (REGISTRY_TABLE,),
        ).fetchone()[0]
        if not present:
            return []
        rows = _registry_rows(conn, status=status)
        return [
            {
                "id": int(row["id"]),
                "receiptNo": row["receipt_no_norm"],
                "sourceOrderNo": row["source_order_no_norm"],
                "exclusionKind": row["exclusion_kind"],
                "status": row["status"],
                "reason": row["reason"],
                "evidenceHash": row["evidence_hash"],
                "proposalFingerprint": row["proposal_fingerprint"],
                "createdOperationId": row["created_operation_id"],
                "createdBy": row["created_by"],
                "createdAt": row["created_at"],
                "revokedOperationId": row["revoked_operation_id"],
                "revokedBy": row["revoked_by"],
                "revokedAt": row["revoked_at"],
            }
            for row in rows[:bounded_limit]
        ]
    finally:
        conn.close()


def load_quarantine_evidence(rule_id: int, *, db_path) -> dict:
    path = database.resolve_db_path(db_path)
    if not path.exists():
        raise KeyError(f"receipt exclusion rule does not exist: {rule_id}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"""
            SELECT q.registry_id, q.operation_id, q.source_file_name, q.source_file_sha256,
                   q.raw_payload_json, q.raw_row_hash, q.prepared_payload_json, q.prepared_row_hash,
                   q.observed_amount, q.observed_at, r.evidence_hash
            FROM {QUARANTINE_TABLE} q
            JOIN {REGISTRY_TABLE} r ON r.id = q.registry_id
            WHERE q.registry_id = ?
            ORDER BY q.id ASC LIMIT 1
            """,
            (rule_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"receipt exclusion quarantine does not exist: {rule_id}")
        return {
            "registryId": int(row["registry_id"]),
            "operationId": row["operation_id"],
            "sourceFileName": row["source_file_name"],
            "sourceFileSha256": row["source_file_sha256"],
            "rawPayload": _json_load(row["raw_payload_json"]),
            "rawRowHash": row["raw_row_hash"],
            "preparedPayload": _json_load(row["prepared_payload_json"]),
            "preparedRowHash": row["prepared_row_hash"],
            "observedAmount": float(row["observed_amount"]),
            "observedAt": row["observed_at"],
            "evidenceHash": row["evidence_hash"],
        }
    finally:
        conn.close()


def commit_receipt_exclusion_revocation(
    rule_id: int,
    *,
    operation_id: str,
    revoked_by: str,
    preview_fingerprint: str,
    db_path,
) -> dict:
    conn = database.get_db_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # Schema bootstrap is separate from the all-or-nothing revocation transaction.
        _ensure_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT status FROM {REGISTRY_TABLE} WHERE id = ?", (rule_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"receipt exclusion rule does not exist: {rule_id}")
        if row[0] != "active":
            raise ValueError("receipt exclusion rule is not active")
        revoked_at = _now()
        conn.execute(
            f"""
            UPDATE {REGISTRY_TABLE}
            SET status = 'revoked', revoked_operation_id = ?, revoked_by = ?, revoked_at = ?
            WHERE id = ?
            """,
            (operation_id, revoked_by, revoked_at, rule_id),
        )
        event_id = _insert_event(
            conn,
            registry_id=rule_id,
            operation_id=operation_id,
            event_type="revoked",
            proposal_fingerprint=preview_fingerprint,
            payload={"ruleId": rule_id, "revokedBy": revoked_by},
        )
        conn.commit()
        return {"status": "revoked", "ruleId": rule_id, "eventId": event_id}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
