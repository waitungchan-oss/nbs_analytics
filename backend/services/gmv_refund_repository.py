"""SQLite repository and explicit schema migration for formal GMV refunds."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .gmv_refund_models import RefundCurrentState


EXPECTED_GMV_OBJECTS = {
    "gmv_refund_batches",
    "gmv_refund_observations",
    "gmv_refund_current",
    "gmv_reconciliation_results",
    "gmv_reconciliation_members",
    "gmv_scope_versions",
    "gmv_adjustment_snapshot",
    "gmv_metric_snapshot",
    "gmv_scope_events",
    "v_gmv_current_scope",
    "v_gmv_current_metrics",
    "v_gmv_current_adjustments",
}


@dataclass(frozen=True, slots=True)
class GmvSchemaValidation:
    db_path: Path
    ready: bool
    missing_objects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GmvMigrationResult:
    db_path: Path
    created: bool


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS gmv_refund_batches (
    batch_id TEXT PRIMARY KEY,
    source_filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    source_row_count INTEGER NOT NULL CHECK (source_row_count >= 0),
    valid_row_count INTEGER NOT NULL CHECK (valid_row_count >= 0),
    preflight_status TEXT NOT NULL CHECK (preflight_status IN ('READY', 'CONFIRMED', 'BLOCKED')),
    preflight_fingerprint TEXT NOT NULL,
    warning_acknowledgement_json TEXT NOT NULL,
    revenue_generation_token TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    UNIQUE (file_sha256, revenue_generation_token)
);

CREATE TABLE IF NOT EXISTS gmv_refund_observations (
    observation_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES gmv_refund_batches(batch_id),
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    source_row_sha256 TEXT NOT NULL,
    refund_order_no TEXT NOT NULL,
    source_receipt_no TEXT NOT NULL,
    refund_order_status TEXT,
    refund_status TEXT NOT NULL,
    refund_amount_minor INTEGER NOT NULL CHECK (refund_amount_minor >= 0),
    currency_code TEXT NOT NULL DEFAULT 'HKD',
    refund_date TEXT,
    original_order_period TEXT,
    observed_at TEXT NOT NULL,
    UNIQUE (batch_id, refund_order_no),
    UNIQUE (batch_id, source_row_sha256)
);

CREATE TABLE IF NOT EXISTS gmv_refund_current (
    refund_order_no TEXT PRIMARY KEY,
    current_observation_id TEXT NOT NULL REFERENCES gmv_refund_observations(observation_id),
    source_receipt_no TEXT NOT NULL,
    refund_order_status TEXT,
    refund_status TEXT NOT NULL,
    refund_amount_minor INTEGER NOT NULL CHECK (refund_amount_minor >= 0),
    currency_code TEXT NOT NULL,
    refund_date TEXT,
    first_seen_batch_id TEXT NOT NULL REFERENCES gmv_refund_batches(batch_id),
    last_seen_batch_id TEXT NOT NULL REFERENCES gmv_refund_batches(batch_id),
    state_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gmv_scope_versions (
    version_id TEXT PRIMARY KEY,
    trigger_batch_id TEXT REFERENCES gmv_refund_batches(batch_id),
    previous_version_id TEXT,
    revenue_generation_token TEXT NOT NULL,
    refund_state_sha256 TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    calculation_sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    activated_at TEXT NOT NULL,
    activated_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gmv_reconciliation_results (
    result_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES gmv_scope_versions(version_id),
    source_receipt_no TEXT NOT NULL,
    refund_dimension TEXT NOT NULL CHECK (refund_dimension IN ('TOTAL_REFUND', 'REFUNDED')),
    match_status TEXT NOT NULL CHECK (match_status IN ('FORMAL_MATCHED', 'REVENUE_SCOPE_EXCLUDED', 'SQLITE_SOURCE_NOT_FOUND')),
    reason_code TEXT NOT NULL,
    refund_detail_amount_minor INTEGER NOT NULL CHECK (refund_detail_amount_minor >= 0),
    original_receipt_amount_minor INTEGER NOT NULL CHECK (original_receipt_amount_minor >= 0),
    applied_refund_amount_minor INTEGER NOT NULL CHECK (applied_refund_amount_minor >= 0),
    over_refund_amount_minor INTEGER NOT NULL CHECK (over_refund_amount_minor >= 0),
    refund_row_count INTEGER NOT NULL CHECK (refund_row_count >= 0),
    revenue_generation_token TEXT NOT NULL,
    rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gmv_reconciliation_members (
    result_id TEXT NOT NULL REFERENCES gmv_reconciliation_results(result_id),
    refund_order_no TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES gmv_refund_observations(observation_id),
    contributed_amount_minor INTEGER NOT NULL CHECK (contributed_amount_minor >= 0),
    PRIMARY KEY (result_id, refund_order_no)
);

CREATE TABLE IF NOT EXISTS gmv_adjustment_snapshot (
    version_id TEXT NOT NULL REFERENCES gmv_scope_versions(version_id),
    source_table TEXT NOT NULL,
    source_row_fingerprint TEXT NOT NULL,
    source_receipt_no TEXT NOT NULL,
    original_order_period TEXT NOT NULL,
    refund_period TEXT,
    refund_before_amount_minor INTEGER NOT NULL CHECK (refund_before_amount_minor >= 0),
    applied_refund_amount_minor INTEGER NOT NULL CHECK (applied_refund_amount_minor >= 0),
    refund_after_amount_minor INTEGER NOT NULL CHECK (refund_after_amount_minor >= 0),
    allocation_ratio_ppm INTEGER NOT NULL CHECK (allocation_ratio_ppm >= 0 AND allocation_ratio_ppm <= 1000000),
    branch_code TEXT,
    salesperson_key TEXT,
    business_type TEXT,
    PRIMARY KEY (version_id, source_table, source_row_fingerprint)
);

CREATE TABLE IF NOT EXISTS gmv_metric_snapshot (
    version_id TEXT NOT NULL REFERENCES gmv_scope_versions(version_id),
    period_basis TEXT NOT NULL CHECK (period_basis IN ('ORIGINAL_ORDER', 'REFUND_EVENT')),
    period_key TEXT NOT NULL,
    dimension_type TEXT NOT NULL,
    dimension_key TEXT NOT NULL,
    dimension_label TEXT NOT NULL,
    refund_dimension TEXT NOT NULL CHECK (refund_dimension IN ('TOTAL_REFUND', 'REFUNDED')),
    metric_name TEXT NOT NULL,
    metric_amount_minor INTEGER NOT NULL CHECK (metric_amount_minor >= 0),
    metric_count INTEGER NOT NULL CHECK (metric_count >= 0),
    quantity_basis TEXT NOT NULL CHECK (quantity_basis IN ('NOT_APPLICABLE', 'ORIGINAL_TRANSACTION')),
    PRIMARY KEY (version_id, period_basis, period_key, dimension_type, dimension_key, refund_dimension, metric_name, quantity_basis)
);

CREATE TABLE IF NOT EXISTS gmv_scope_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('ACTIVATE', 'ROLLBACK', 'DEACTIVATE')),
    from_version_id TEXT,
    to_version_id TEXT,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_sha256 TEXT NOT NULL UNIQUE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gmv_scope_one_active
    ON gmv_scope_versions(status) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_gmv_observations_batch_order
    ON gmv_refund_observations(batch_id, refund_order_no);
CREATE INDEX IF NOT EXISTS idx_gmv_observations_order_observed
    ON gmv_refund_observations(refund_order_no, observed_at);
CREATE INDEX IF NOT EXISTS idx_gmv_current_receipt_status
    ON gmv_refund_current(source_receipt_no, refund_status);
CREATE INDEX IF NOT EXISTS idx_gmv_results_version_dimension_status
    ON gmv_reconciliation_results(version_id, refund_dimension, match_status);
CREATE INDEX IF NOT EXISTS idx_gmv_members_result_order
    ON gmv_reconciliation_members(result_id, refund_order_no);
CREATE INDEX IF NOT EXISTS idx_gmv_adjustments_version_original_period
    ON gmv_adjustment_snapshot(version_id, original_order_period);
CREATE INDEX IF NOT EXISTS idx_gmv_adjustments_version_refund_period
    ON gmv_adjustment_snapshot(version_id, refund_period);
CREATE INDEX IF NOT EXISTS idx_gmv_metrics_version_period_dimension
    ON gmv_metric_snapshot(version_id, period_basis, period_key, dimension_type);
CREATE INDEX IF NOT EXISTS idx_gmv_scope_status
    ON gmv_scope_versions(status);

CREATE VIEW IF NOT EXISTS v_gmv_current_scope AS
SELECT version_id, trigger_batch_id, previous_version_id, revenue_generation_token,
       refund_state_sha256, rule_version, activated_at, activated_by, calculation_sha256
FROM gmv_scope_versions
WHERE status = 'ACTIVE';

CREATE VIEW IF NOT EXISTS v_gmv_current_metrics AS
SELECT m.*
FROM gmv_metric_snapshot AS m
JOIN gmv_scope_versions AS v ON v.version_id = m.version_id
WHERE v.status = 'ACTIVE';

CREATE VIEW IF NOT EXISTS v_gmv_current_adjustments AS
SELECT a.*
FROM gmv_adjustment_snapshot AS a
JOIN gmv_scope_versions AS v ON v.version_id = a.version_id
WHERE v.status = 'ACTIVE';

CREATE TRIGGER IF NOT EXISTS trg_gmv_batches_immutable_update
BEFORE UPDATE ON gmv_refund_batches
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_batches_immutable_delete
BEFORE DELETE ON gmv_refund_batches
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_observations_immutable_update
BEFORE UPDATE ON gmv_refund_observations
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_observations_immutable_delete
BEFORE DELETE ON gmv_refund_observations
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_results_immutable_update
BEFORE UPDATE ON gmv_reconciliation_results
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_results_immutable_delete
BEFORE DELETE ON gmv_reconciliation_results
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_members_immutable_update
BEFORE UPDATE ON gmv_reconciliation_members
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_members_immutable_delete
BEFORE DELETE ON gmv_reconciliation_members
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_adjustments_immutable_update
BEFORE UPDATE ON gmv_adjustment_snapshot
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_adjustments_immutable_delete
BEFORE DELETE ON gmv_adjustment_snapshot
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_metrics_immutable_update
BEFORE UPDATE ON gmv_metric_snapshot
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_metrics_immutable_delete
BEFORE DELETE ON gmv_metric_snapshot
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_events_immutable_update
BEFORE UPDATE ON gmv_scope_events
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_gmv_events_immutable_delete
BEFORE DELETE ON gmv_scope_events
BEGIN SELECT RAISE(ABORT, 'gmv ledger is immutable'); END;
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _object_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }


def migrate_gmv_schema(db_path: str | Path) -> GmvMigrationResult:
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with _connect(resolved) as conn:
        before = EXPECTED_GMV_OBJECTS <= _object_names(conn)
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL + "\nCOMMIT;")
        after = EXPECTED_GMV_OBJECTS <= _object_names(conn)
        if not after:
            missing = sorted(EXPECTED_GMV_OBJECTS - _object_names(conn))
            raise RuntimeError(f"GMV schema incomplete: {', '.join(missing)}")
    return GmvMigrationResult(db_path=resolved, created=not before)


class GmvRefundRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    def validate_schema(self) -> GmvSchemaValidation:
        if not self.db_path.exists():
            return GmvSchemaValidation(self.db_path, False, tuple(sorted(EXPECTED_GMV_OBJECTS)))
        with self.connect() as conn:
            missing = tuple(sorted(EXPECTED_GMV_OBJECTS - _object_names(conn)))
        return GmvSchemaValidation(self.db_path, not missing, missing)

    def load_current_refunds(self) -> dict[str, RefundCurrentState]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT refund_order_no, source_receipt_no, refund_amount_minor, "
                "refund_status, last_seen_batch_id, state_sha256, currency_code, refund_date "
                "FROM gmv_refund_current ORDER BY refund_order_no"
            ).fetchall()
        return {
            row[0]: RefundCurrentState(
                refund_order_no=row[0],
                source_receipt_no=row[1],
                refund_amount_minor=row[2],
                refund_status=row[3],
                source_batch_id=row[4],
                state_sha256=row[5],
                currency_code=row[6],
                refund_date=row[7],
            )
            for row in rows
        }

    def load_active_scope(self) -> dict[str, object] | None:
        with self.connect() as conn:
            try:
                cursor = conn.execute("SELECT * FROM v_gmv_current_scope")
            except sqlite3.OperationalError as exc:
                # Older production copies may contain the GMV tables but not
                # the derived current-scope view. Keep reopening read-only by
                # using the view's equivalent predicate until migration runs.
                if "no such table: v_gmv_current_scope" not in str(exc):
                    raise
                cursor = conn.execute(
                    "SELECT version_id, trigger_batch_id, previous_version_id, "
                    "revenue_generation_token, refund_state_sha256, rule_version, "
                    "activated_at, activated_by, calculation_sha256 "
                    "FROM gmv_scope_versions WHERE status = 'ACTIVE'"
                )
            row = cursor.fetchone()
            columns = [item[0] for item in cursor.description] if cursor.description else []
        return dict(zip(columns, row)) if row else None

    def load_confirmed_batch_identity(
        self, file_sha256: str, revenue_generation_token: str
    ) -> dict[str, object] | None:
        """Return the immutable activation receipt for a previously confirmed upload."""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT b.batch_id, v.version_id, v.previous_version_id, "
                "v.revenue_generation_token, v.refund_state_sha256, e.event_id "
                "FROM gmv_refund_batches AS b "
                "JOIN gmv_scope_versions AS v ON v.trigger_batch_id = b.batch_id "
                "JOIN gmv_scope_events AS e ON e.to_version_id = v.version_id "
                "AND e.event_type = 'ACTIVATE' "
                "WHERE b.file_sha256 = ? AND b.revenue_generation_token = ? "
                "ORDER BY v.activated_at DESC LIMIT 1",
                (file_sha256, revenue_generation_token),
            )
            row = cursor.fetchone()
            columns = [item[0] for item in cursor.description] if cursor.description else []
        return dict(zip(columns, row)) if row else None

    def load_metric_snapshot(self, version_id: str) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM v_gmv_current_metrics WHERE version_id = ?",
                conn,
                params=(version_id,),
            )

    def load_adjustment_snapshot(self, version_id: str) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM v_gmv_current_adjustments WHERE version_id = ?",
                conn,
                params=(version_id,),
            )

    def load_reconciliation_snapshot(
        self, version_id: str, refund_dimension: str
    ) -> pd.DataFrame:
        if refund_dimension not in {"TOTAL_REFUND", "REFUNDED"}:
            raise ValueError("unsupported refund dimension")
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT source_receipt_no, refund_detail_amount_minor, "
                "applied_refund_amount_minor, over_refund_amount_minor, "
                "match_status, reason_code "
                "FROM gmv_reconciliation_results "
                "WHERE version_id = ? AND refund_dimension = ? "
                "ORDER BY source_receipt_no",
                conn,
                params=(version_id, refund_dimension),
            )

    def load_reconciliation_snapshot_for_receipts(
        self, version_id: str, refund_dimension: str,
        source_receipt_nos: list[str] | tuple[str, ...],
    ) -> pd.DataFrame:
        """Load only affected receipts for an incremental rebuild."""
        if refund_dimension not in {"TOTAL_REFUND", "REFUNDED"}:
            raise ValueError("unsupported refund dimension")
        receipts = tuple(dict.fromkeys(str(value).strip() for value in source_receipt_nos if str(value).strip()))
        if not receipts:
            return pd.DataFrame(columns=[
                "source_receipt_no", "refund_detail_amount_minor",
                "applied_refund_amount_minor", "over_refund_amount_minor",
                "match_status", "reason_code",
            ])
        placeholders = ",".join("?" for _ in receipts)
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT source_receipt_no, refund_detail_amount_minor, "
                "applied_refund_amount_minor, over_refund_amount_minor, "
                "match_status, reason_code FROM gmv_reconciliation_results "
                f"WHERE version_id = ? AND refund_dimension = ? AND source_receipt_no IN ({placeholders}) "
                "ORDER BY source_receipt_no",
                conn, params=(version_id, refund_dimension, *receipts),
            )

    def load_scope_history(self, limit: int = 20) -> tuple[dict[str, object], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT version_id, trigger_batch_id, previous_version_id, "
                "revenue_generation_token, refund_state_sha256, rule_version, "
                "calculation_sha256, status, activated_at, activated_by "
                "FROM gmv_scope_versions ORDER BY activated_at DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description]
        return tuple(dict(zip(columns, row)) for row in rows)
