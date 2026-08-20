"""Pure-read Preflight application service for formal GMV refunds."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

import pandas as pd

from .gmv_refund_models import (
    RefundCurrentState,
    RefundObservation,
    canonical_payload_sha256,
    classify_refund_changes,
    money_to_minor,
    refund_state_sha256,
)
from .gmv_refund_repository import GmvRefundRepository
from .upload_lock_service import acquire_upload_lease


class InjectedGmvFailure(RuntimeError):
    pass


class StaleGmvPreview(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GmvActivationReceipt:
    batch_id: str
    version_id: str
    event_id: str
    previous_version_id: str | None
    revenue_generation_token: str
    refund_state_sha256: str


@dataclass(frozen=True, slots=True)
class GmvLifecycleReceipt:
    event_id: str
    event_type: str
    from_version_id: str | None
    to_version_id: str | None


@dataclass(frozen=True, slots=True)
class RevenueFrames:
    raw_tour: pd.DataFrame
    raw_others: pd.DataFrame
    formal_tour: pd.DataFrame
    formal_others: pd.DataFrame


@dataclass(frozen=True, slots=True)
class GmvRefundPreview:
    status: str
    file_sha256: str
    normalized_sha256: str
    current_state_sha256: str
    proposed_state_sha256: str
    revenue_generation_token: str
    rule_version: str
    change_counts: dict[str, int]
    dimensions: dict[str, dict[str, int]]
    formal_revenue_minor: int
    official_net_gmv_minor: int
    blocking_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    preflight_fingerprint: str
    observations: tuple[RefundObservation, ...]
    proposed_states: tuple[RefundCurrentState, ...]


def _row_hash(row: Mapping[str, object]) -> str:
    return canonical_payload_sha256(dict(row))


def _build_observations(refund_rows: pd.DataFrame) -> tuple[list[RefundObservation], list[str]]:
    required = {"退款單號", "來源單據號", "退款原幣金額", "退款狀態"}
    missing = sorted(required - set(refund_rows.columns))
    if missing:
        return [], [f"MISSING_{column}" for column in missing]

    observations: list[RefundObservation] = []
    blocking: list[str] = []
    for index, row in refund_rows.iterrows():
        refund_order_no = str(row["退款單號"] or "").strip()
        source_receipt_no = str(row["來源單據號"] or "").strip()
        refund_status = str(row["退款狀態"] or "").strip()
        if not refund_order_no:
            blocking.append("EMPTY_REFUND_ORDER_NO")
            continue
        if not source_receipt_no:
            blocking.append("EMPTY_SOURCE_RECEIPT_NO")
            continue
        if not refund_status:
            blocking.append("EMPTY_REFUND_STATUS")
            continue
        try:
            amount_minor = money_to_minor(row["退款原幣金額"])
        except ValueError:
            blocking.append("INVALID_REFUND_AMOUNT")
            continue
        observations.append(
            RefundObservation(
                refund_order_no=refund_order_no,
                source_receipt_no=source_receipt_no,
                refund_amount_minor=amount_minor,
                refund_status=refund_status,
                raw_row_sha256=_row_hash(row.to_dict()),
                currency_code=str(row.get("幣種", "HKD") or "HKD").strip(),
                refund_date=str(row.get("退款日期", "") or "").strip() or None,
            )
        )
    return observations, sorted(set(blocking))


def _observations_frame(states: Mapping[str, object]) -> pd.DataFrame:
    rows = [
        {
            "退款單號": state.refund_order_no,
            "來源單據號": state.source_receipt_no,
            "退款原幣金額": state.refund_amount_minor / 100,
            "退款狀態": state.refund_status,
        }
        for state in states.values()
    ]
    return pd.DataFrame(rows, columns=["退款單號", "來源單據號", "退款原幣金額", "退款狀態"])


def _formal_revenue_minor(frames: RevenueFrames) -> int:
    values = []
    for frame in (frames.formal_tour, frames.formal_others):
        if "收款原幣金額" in frame.columns:
            values.extend(frame["收款原幣金額"].tolist())
    return sum(money_to_minor(value) for value in values)


def preview_refund_batch(
    refund_rows: pd.DataFrame,
    *,
    repository: GmvRefundRepository,
    revenue_frames: RevenueFrames,
    revenue_generation_token: str,
    rule_version: str,
    file_sha256: str,
) -> GmvRefundPreview:
    observations, blocking_codes = _build_observations(refund_rows)
    current = repository.load_current_refunds()
    current_hash = refund_state_sha256(current)
    changes = classify_refund_changes(observations, current)
    proposed = dict(current)
    for change in (*changes.new, *changes.status_changed):
        proposed[change.refund_order_no] = change.to_current_state(batch_id="PREVIEW")

    normalized_sha256 = hashlib.sha256(
        "\n".join(item.raw_row_sha256 for item in observations).encode("utf-8")
    ).hexdigest()
    dimensions: dict[str, dict[str, int]] = {}
    proposed_rows = _observations_frame(proposed)
    from app_workflows import _apply_gmv_refund_adjustments

    for dimension, status in (("總退款", None), ("已退款", "已退款")):
        adjusted = _apply_gmv_refund_adjustments(
            revenue_frames.formal_tour,
            revenue_frames.formal_others,
            proposed_rows,
            refund_status=status,
        )
        dimensions[dimension] = {
            "source_order_count": len(adjusted["refund_amounts"]),
            "matched_source_order_count": len(adjusted["matched_source_ids"]),
            "unmatched_source_order_count": len(adjusted["unmatched_source_ids"]),
            "refund_detail_amount_minor": money_to_minor(adjusted["refund_total"]),
            "applied_refund_amount_minor": money_to_minor(adjusted["applied_refund_total"]),
            "over_refund_amount_minor": money_to_minor(adjusted["over_refund_total"]),
        }

    if changes.identity_conflicts:
        blocking_codes.append("REFUND_IDENTITY_CONFLICT")
    blocking_codes = sorted(set(blocking_codes))
    status = "blocked" if blocking_codes else "ready"
    formal_revenue_minor = _formal_revenue_minor(revenue_frames)
    paid_deduction = dimensions["已退款"]["applied_refund_amount_minor"]
    proposed_hash = refund_state_sha256(proposed)
    fingerprint = canonical_payload_sha256(
        {
            "fileSha256": file_sha256,
            "normalizedSha256": normalized_sha256,
            "currentStateSha256": current_hash,
            "proposedStateSha256": proposed_hash,
            "revenueGenerationToken": revenue_generation_token,
            "ruleVersion": rule_version,
        }
    )
    return GmvRefundPreview(
        status=status,
        file_sha256=file_sha256,
        normalized_sha256=normalized_sha256,
        current_state_sha256=current_hash,
        proposed_state_sha256=proposed_hash,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
        change_counts=changes.counts,
        dimensions=dimensions,
        formal_revenue_minor=formal_revenue_minor,
        official_net_gmv_minor=formal_revenue_minor - paid_deduction,
        blocking_codes=tuple(blocking_codes),
        warning_codes=(),
        preflight_fingerprint=fingerprint,
        observations=tuple(observations),
        proposed_states=tuple(sorted(proposed.values(), key=lambda item: item.refund_order_no)),
    )


def _fault(stage: str, fault_after: str | None) -> None:
    if stage == fault_after:
        raise InjectedGmvFailure(stage)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _insert_reconciliation_rows(conn, version_id: str, frames: RevenueFrames, states: Mapping[str, object], observation_ids: Mapping[str, str], revenue_token: str, rule_version: str) -> tuple[str, str]:
    from app_workflows import _apply_gmv_refund_adjustments

    refund_rows = _observations_frame(states)
    revenue_rows = pd.concat(
        [frames.formal_tour.assign(__source_table="旅行團"), frames.formal_others.assign(__source_table="其他業務")],
        ignore_index=True,
        sort=False,
    )
    revenue_rows["__source_id"] = revenue_rows.get("來源單據號", pd.Series(dtype=str)).astype(str).str.strip()
    original_amounts = (
        pd.to_numeric(revenue_rows.get("收款原幣金額", 0), errors="coerce")
        .fillna(0.0)
        .groupby(revenue_rows["__source_id"])
        .sum()
        .to_dict()
    )
    result_hashes: list[str] = []
    adjustment_hashes: list[str] = []
    for dimension, status in (("TOTAL_REFUND", None), ("REFUNDED", "已退款")):
        adjusted = _apply_gmv_refund_adjustments(
            frames.formal_tour, frames.formal_others, refund_rows, refund_status=status
        )
        for source_id in sorted(adjusted["refund_amounts"].index):
            source_amount = float(adjusted["refund_amounts"][source_id])
            original_amount = float(original_amounts.get(source_id, 0.0))
            applied = min(source_amount, original_amount) if original_amount else 0.0
            if source_id in adjusted["unmatched_source_ids"]:
                match_status = "SQLITE_SOURCE_NOT_FOUND"
                reason = "SQLITE_SOURCE_NOT_FOUND"
            else:
                match_status = "FORMAL_MATCHED"
                reason = "FORMAL_MATCHED"
            result_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO gmv_reconciliation_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id, version_id, source_id, dimension, match_status, reason,
                    money_to_minor(source_amount), money_to_minor(original_amount),
                    money_to_minor(applied), money_to_minor(max(source_amount - applied, 0)),
                    int(sum(1 for state in states.values() if state.source_receipt_no == source_id)),
                    revenue_token, rule_version,
                ),
            )
            result_hashes.append(result_id)
            for state in states.values():
                if state.source_receipt_no == source_id and (status is None or state.refund_status == status):
                    conn.execute(
                        "INSERT INTO gmv_reconciliation_members VALUES (?, ?, ?, ?)",
                        (result_id, state.refund_order_no, observation_ids.get(state.refund_order_no), state.refund_amount_minor),
                    )
        # The immutable adjustment snapshot is the official net-GMV (已退款)
        # projection. The total-refund dimension remains available in the
        # dashboard/export path and its reconciliation result/metrics.
        if status != "已退款":
            continue
        for row in adjusted["adjusted_detail"].itertuples(index=False):
            source_table = str(getattr(row, "資料表", ""))
            source_receipt = str(getattr(row, "來源單據號", ""))
            if not source_receipt:
                continue
            before = money_to_minor(getattr(row, "退款前收款原幣金額", 0))
            applied_minor = money_to_minor(getattr(row, "退款扣減金額", 0))
            after = money_to_minor(getattr(row, "退款後收款原幣金額", 0))
            from app_workflows import _gmv_revenue_row_fingerprint
            fingerprint = _gmv_revenue_row_fingerprint(source_table, pd.Series(row._asdict()))
            conn.execute(
                "INSERT OR IGNORE INTO gmv_adjustment_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, source_table, fingerprint, source_receipt, "UNKNOWN", None, before, applied_minor, after, 1000000, None, None, None),
            )
            adjustment_hashes.append(fingerprint)
        for metric_name, amount, count, basis in (
            ("REFUND_DETAIL", adjusted["refund_total"], len(adjusted["refund_amounts"]), "NOT_APPLICABLE"),
            ("APPLIED_REFUND", adjusted["applied_refund_total"], len(adjusted["matched_source_ids"]), "NOT_APPLICABLE"),
            ("ORIGINAL_TRANSACTION_QUANTITY", 0, 0, "ORIGINAL_TRANSACTION"),
        ):
            conn.execute(
                "INSERT INTO gmv_metric_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, "ORIGINAL_ORDER", "ALL", "SCOPE", "ALL", "全部", dimension, metric_name, money_to_minor(amount), int(count), basis),
            )
    return canonical_payload_sha256({"results": result_hashes}), canonical_payload_sha256({"adjustments": adjustment_hashes})


def confirm_refund_batch(
    preview: GmvRefundPreview,
    *,
    actor: str,
    acknowledgements: frozenset[str],
    db_path,
    coordination_db_path,
    revenue_loader,
    revenue_generation_loader,
    fault_after: str | None = None,
) -> GmvActivationReceipt:
    if preview.status == "blocked":
        raise ValueError(f"blocked Preflight: {','.join(preview.blocking_codes)}")
    with acquire_upload_lease(
        entry_point="formal_gmv_confirm",
        source_files=[preview.file_sha256],
        coordination_db_path=coordination_db_path,
    ):
        repository = GmvRefundRepository(db_path)
        current = repository.load_current_refunds()
        if refund_state_sha256(current) != preview.current_state_sha256:
            raise StaleGmvPreview("current refund state changed after Preflight")
        current_revenue_token = revenue_generation_loader()
        if current_revenue_token != preview.revenue_generation_token:
            raise StaleGmvPreview("revenue generation changed after Preflight")
        frames = revenue_loader()
        batch_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        event_id = uuid.uuid4().hex
        timestamp = _now()
        proposed = {
            state.refund_order_no: state for state in preview.proposed_states
        }
        with repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT 1 FROM gmv_refund_batches WHERE file_sha256 = ? AND revenue_generation_token = ?",
                (preview.file_sha256, preview.revenue_generation_token),
            ).fetchone()
            if duplicate:
                raise ValueError("refund batch already confirmed for this revenue generation")
            conn.execute(
                "INSERT INTO gmv_refund_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, f"refund-{preview.file_sha256[:12]}.xlsx", preview.file_sha256, preview.normalized_sha256,
                 len(preview.observations), len(preview.observations), "CONFIRMED", preview.preflight_fingerprint,
                 json.dumps(sorted(acknowledgements), ensure_ascii=False), preview.revenue_generation_token,
                 preview.rule_version, timestamp, actor),
            )
            _fault("after_batch", fault_after)
            observation_ids = {}
            for row_number, observation in enumerate(preview.observations, start=1):
                observation_id = uuid.uuid4().hex
                observation_ids[observation.refund_order_no] = observation_id
                conn.execute(
                    "INSERT INTO gmv_refund_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (observation_id, batch_id, row_number, observation.raw_row_sha256, observation.refund_order_no,
                     observation.source_receipt_no, None, observation.refund_status, observation.refund_amount_minor,
                     observation.currency_code, observation.refund_date, None, timestamp),
                )
            _fault("after_observations", fault_after)
            for state in proposed.values():
                observation_id = observation_ids.get(state.refund_order_no)
                if observation_id is None:
                    continue
                conn.execute(
                    "INSERT INTO gmv_refund_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(refund_order_no) DO UPDATE SET current_observation_id=excluded.current_observation_id, "
                    "source_receipt_no=excluded.source_receipt_no, refund_status=excluded.refund_status, "
                    "refund_amount_minor=excluded.refund_amount_minor, last_seen_batch_id=excluded.last_seen_batch_id, "
                    "state_sha256=excluded.state_sha256, updated_at=excluded.updated_at",
                    (state.refund_order_no, observation_id, state.source_receipt_no, None, state.refund_status,
                     state.refund_amount_minor, state.currency_code, state.refund_date, batch_id, batch_id,
                     state.state_sha256, timestamp),
                )
            _fault("after_current_projection", fault_after)
            previous = conn.execute(
                "SELECT version_id FROM gmv_scope_versions WHERE status = 'ACTIVE'"
            ).fetchone()
            previous_version_id = previous[0] if previous else None
            if previous_version_id:
                conn.execute("UPDATE gmv_scope_versions SET status = 'RETIRED' WHERE version_id = ?", (previous_version_id,))
            proposed_states = {
                state.refund_order_no: state for state in preview.proposed_states
            }
            all_observation_ids = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT refund_order_no, current_observation_id FROM gmv_refund_current"
                ).fetchall()
            }
            conn.execute(
                "INSERT INTO gmv_scope_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, batch_id, previous_version_id, preview.revenue_generation_token,
                 preview.proposed_state_sha256, preview.rule_version, "pending", "ACTIVE", timestamp, actor),
            )
            _fault("after_reconciliation", fault_after)
            reconciliation_hash, adjustment_hash = _insert_reconciliation_rows(
                conn, version_id, frames, proposed_states, all_observation_ids, preview.revenue_generation_token, preview.rule_version
            )
            _fault("after_adjustments", fault_after)
            _fault("after_metrics", fault_after)
            calculation_sha256 = canonical_payload_sha256({
                "revenue": preview.revenue_generation_token,
                "refund": preview.proposed_state_sha256,
                "rule": preview.rule_version,
                "reconciliation": reconciliation_hash,
                "adjustments": adjustment_hash,
            })
            conn.execute(
                "UPDATE gmv_scope_versions SET calculation_sha256 = ? WHERE version_id = ?",
                (calculation_sha256, version_id),
            )
            event_hash = canonical_payload_sha256({"eventId": event_id, "from": previous_version_id, "to": version_id, "type": "ACTIVATE"})
            conn.execute(
                "INSERT INTO gmv_scope_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, "ACTIVATE", previous_version_id, version_id, "confirmed refund batch", actor, timestamp, event_hash),
            )
            _fault("after_activation_event", fault_after)
            conn.commit()
        return GmvActivationReceipt(batch_id, version_id, event_id, previous_version_id, preview.revenue_generation_token, preview.proposed_state_sha256)


def load_gmv_scope_status(repository: GmvRefundRepository, current_revenue_token: str) -> dict[str, object]:
    active = repository.load_active_scope()
    if active is None:
        return {"status": "NOT_INITIALIZED", "version_id": None}
    status = "CURRENT" if active["revenue_generation_token"] == current_revenue_token else "STALE_REVENUE_GENERATION"
    return {"status": status, **active}


def _insert_scope_event(conn, event_id: str, event_type: str, from_version_id: str | None, to_version_id: str | None, reason: str, actor: str, timestamp: str) -> None:
    event_hash = canonical_payload_sha256({"eventId": event_id, "type": event_type, "from": from_version_id, "to": to_version_id, "reason": reason})
    conn.execute(
        "INSERT INTO gmv_scope_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, event_type, from_version_id, to_version_id, reason, actor, timestamp, event_hash),
    )


def deactivate_gmv_scope(*, reason: str, actor: str, db_path, coordination_db_path) -> GmvLifecycleReceipt:
    with acquire_upload_lease(entry_point="formal_gmv_deactivate", source_files=[], coordination_db_path=coordination_db_path):
        repository = GmvRefundRepository(db_path)
        event_id = uuid.uuid4().hex
        timestamp = _now()
        with repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT version_id FROM gmv_scope_versions WHERE status = 'ACTIVE'").fetchone()
            from_version_id = active[0] if active else None
            if from_version_id:
                conn.execute("UPDATE gmv_scope_versions SET status = 'RETIRED' WHERE version_id = ?", (from_version_id,))
            _insert_scope_event(conn, event_id, "DEACTIVATE", from_version_id, None, reason, actor, timestamp)
            conn.commit()
        return GmvLifecycleReceipt(event_id, "DEACTIVATE", from_version_id, None)


def rollback_gmv_scope(target_version_id: str, *, reason: str, actor: str, db_path, coordination_db_path) -> GmvLifecycleReceipt:
    with acquire_upload_lease(entry_point="formal_gmv_rollback", source_files=[], coordination_db_path=coordination_db_path):
        repository = GmvRefundRepository(db_path)
        event_id = uuid.uuid4().hex
        timestamp = _now()
        with repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute("SELECT status FROM gmv_scope_versions WHERE version_id = ?", (target_version_id,)).fetchone()
            if target is None:
                raise ValueError("target GMV version does not exist")
            active = conn.execute("SELECT version_id FROM gmv_scope_versions WHERE status = 'ACTIVE'").fetchone()
            from_version_id = active[0] if active else None
            if from_version_id == target_version_id:
                raise ValueError("target GMV version is already active")
            if from_version_id:
                conn.execute("UPDATE gmv_scope_versions SET status = 'RETIRED' WHERE version_id = ?", (from_version_id,))
            conn.execute("UPDATE gmv_scope_versions SET status = 'ACTIVE' WHERE version_id = ?", (target_version_id,))
            _insert_scope_event(conn, event_id, "ROLLBACK", from_version_id, target_version_id, reason, actor, timestamp)
            conn.commit()
        return GmvLifecycleReceipt(event_id, "ROLLBACK", from_version_id, target_version_id)


def rebuild_gmv_scope(*, reason: str, actor: str, db_path, coordination_db_path, revenue_loader, revenue_generation_loader, rule_version: str) -> GmvActivationReceipt:
    with acquire_upload_lease(entry_point="formal_gmv_rebuild", source_files=[], coordination_db_path=coordination_db_path):
        repository = GmvRefundRepository(db_path)
        states = repository.load_current_refunds()
        revenue_token = revenue_generation_loader()
        frames = revenue_loader()
        refund_hash = refund_state_sha256(states)
        version_id = uuid.uuid4().hex
        event_id = uuid.uuid4().hex
        timestamp = _now()
        with repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT version_id FROM gmv_scope_versions WHERE status = 'ACTIVE'").fetchone()
            previous_version_id = previous[0] if previous else None
            if previous_version_id:
                conn.execute("UPDATE gmv_scope_versions SET status = 'RETIRED' WHERE version_id = ?", (previous_version_id,))
            observation_ids = {row[0]: row[1] for row in conn.execute("SELECT refund_order_no, current_observation_id FROM gmv_refund_current")}
            conn.execute(
                "INSERT INTO gmv_scope_versions VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, previous_version_id, revenue_token, refund_hash, rule_version, "pending", "ACTIVE", timestamp, actor),
            )
            reconciliation_hash, adjustment_hash = _insert_reconciliation_rows(
                conn, version_id, frames, states, observation_ids, revenue_token, rule_version
            )
            calculation_hash = canonical_payload_sha256({"revenue": revenue_token, "refund": refund_hash, "rule": rule_version, "reconciliation": reconciliation_hash, "adjustments": adjustment_hash})
            conn.execute("UPDATE gmv_scope_versions SET calculation_sha256 = ? WHERE version_id = ?", (calculation_hash, version_id))
            _insert_scope_event(conn, event_id, "ACTIVATE", previous_version_id, version_id, reason, actor, timestamp)
            conn.commit()
        return GmvActivationReceipt("", version_id, event_id, previous_version_id, revenue_token, refund_hash)
