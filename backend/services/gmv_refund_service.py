"""Pure-read Preflight application service for formal GMV refunds."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Mapping

import pandas as pd

from .gmv_refund_models import (
    RefundCurrentState,
    RefundObservation,
    classify_refund_state_delta,
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
    warning_summaries: tuple[dict[str, object], ...]
    preflight_fingerprint: str
    observations: tuple[RefundObservation, ...]
    proposed_states: tuple[RefundCurrentState, ...]


@dataclass(frozen=True, slots=True)
class GmvActiveReadModel:
    status: str
    version_id: str | None
    scope: dict[str, object] | None
    metrics: pd.DataFrame
    total_adjusted: dict[str, object] | None
    paid_adjusted: dict[str, object] | None
    can_export: bool


@dataclass(frozen=True, slots=True)
class GmvFormalArtifacts:
    total_adjusted: dict[str, object]
    paid_adjusted: dict[str, object]
    total_summary_rows: list[dict[str, object]]
    paid_summary_rows: list[dict[str, object]]
    cache_manifest: object


@dataclass(frozen=True, slots=True)
class GmvFastCandidate:
    artifacts: dict[str, bytes]
    total_adjusted: dict[str, object]
    paid_adjusted: dict[str, object]
    total_summary_rows: list[dict[str, object]]
    paid_summary_rows: list[dict[str, object]]
    shadow_status: str
    reference_status: str
    performance: dict[str, object] | None = None


GMV_EXPORT_SCHEMA_VERSION = "gmv-formal-export-v2"
GMV_PIPELINE_FINGERPRINT = "pipeline-gmv-fast-v1"
GMV_SERIALIZER_VERSION = "gmv-openpyxl-serializer-v1"


def _canonical_frame_sha256(frame: pd.DataFrame) -> str:
    if frame.empty:
        return canonical_payload_sha256({"columns": sorted(map(str, frame.columns)), "rows": []})
    columns = sorted(map(str, frame.columns))
    work = frame.reindex(columns=columns).copy()

    def normalize(value: object) -> str:
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return str(value).strip()

    rows = sorted(tuple(normalize(value) for value in row) for row in work.itertuples(index=False, name=None))
    return canonical_payload_sha256({"columns": columns, "rows": rows})


def revenue_state_token(frames: RevenueFrames, rule_version: str) -> str:
    """Fingerprint only revenue inputs, never unrelated tables in the SQLite file."""
    digest = canonical_payload_sha256(
        {
            "schema": "gmv-revenue-state-v1",
            "ruleVersion": str(rule_version),
            "rawTour": _canonical_frame_sha256(frames.raw_tour),
            "rawOthers": _canonical_frame_sha256(frames.raw_others),
            "formalTour": _canonical_frame_sha256(frames.formal_tour),
            "formalOthers": _canonical_frame_sha256(frames.formal_others),
        }
    )
    return f"gmv-revenue-state-v1:{digest}"


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
    warning_codes: tuple[str, ...] = (),
    warning_summaries: tuple[dict[str, object], ...] = (),
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
            "warningCodes": sorted(set(warning_codes)),
            "warningSummaries": list(warning_summaries),
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
        warning_codes=tuple(sorted(set(warning_codes))),
        warning_summaries=tuple(dict(item) for item in warning_summaries),
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
        if status == "已退款":
            snapshot_rows = adjusted["adjusted_detail"].to_dict(orient="records")
            expected_applied_minor = money_to_minor(adjusted["applied_refund_total"])
            applied_minors = [
                money_to_minor(row.get("退款扣減金額", 0)) for row in snapshot_rows
            ]
            residual = expected_applied_minor - sum(applied_minors)
            if residual and snapshot_rows:
                for index in range(len(snapshot_rows) - 1, -1, -1):
                    before_minor = money_to_minor(
                            snapshot_rows[index].get("退款前收款原幣金額", 0)
                    )
                    candidate = applied_minors[index] + residual
                    if 0 <= candidate <= before_minor:
                        applied_minors[index] = candidate
                        residual = 0
                        break
            if residual:
                raise ValueError("GMV adjustment minor-unit allocation is not conserved")
            for row, applied_minor in zip(snapshot_rows, applied_minors):
                source_table = str(row.get("資料表", ""))
                source_receipt = str(row.get("來源單據號", ""))
                if not source_receipt:
                    continue
                before = money_to_minor(row.get("退款前收款原幣金額", 0))
                after = before - applied_minor
                from app_workflows import _gmv_revenue_row_fingerprint
                fingerprint = _gmv_revenue_row_fingerprint(source_table, pd.Series(row))
                conn.execute(
                    "INSERT INTO gmv_adjustment_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (version_id, source_table, fingerprint, source_receipt, "UNKNOWN", None, before, applied_minor, after, 1000000, None, None, None),
                )
                adjustment_hashes.append(fingerprint)
            persisted_applied_minor = conn.execute(
                "SELECT COALESCE(SUM(applied_refund_amount_minor), 0) "
                "FROM gmv_adjustment_snapshot WHERE version_id = ?",
                (version_id,),
            ).fetchone()[0]
            if persisted_applied_minor != expected_applied_minor:
                raise ValueError("GMV adjustment snapshot does not reconcile to paid-refund metric")
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
        duplicate = repository.load_confirmed_batch_identity(
            preview.file_sha256, preview.revenue_generation_token
        )
        if duplicate is not None:
            return GmvActivationReceipt(
                batch_id=str(duplicate["batch_id"]),
                version_id=str(duplicate["version_id"]),
                event_id=str(duplicate["event_id"]),
                previous_version_id=duplicate["previous_version_id"],
                revenue_generation_token=str(duplicate["revenue_generation_token"]),
                refund_state_sha256=str(duplicate["refund_state_sha256"]),
            )
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
        state_delta = classify_refund_state_delta(current, tuple(proposed.values()))
        if state_delta.identity_conflict_refund_order_nos:
            raise ValueError(
                "refund identity conflict: "
                + ",".join(state_delta.identity_conflict_refund_order_nos)
            )
        with repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            confirmation_payload = {
                "acknowledgements": sorted(acknowledgements),
                "warnings": list(preview.warning_codes),
                "warningSummaries": list(preview.warning_summaries),
            }
            conn.execute(
                "INSERT INTO gmv_refund_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, f"refund-{preview.file_sha256[:12]}.xlsx", preview.file_sha256, preview.normalized_sha256,
                 len(preview.observations), len(preview.observations), "CONFIRMED", preview.preflight_fingerprint,
                 json.dumps(confirmation_payload, ensure_ascii=False, sort_keys=True), preview.revenue_generation_token,
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


def _refund_frame_from_reconciliation(
    repository: GmvRefundRepository,
    version_id: str,
    refund_dimension: str,
) -> pd.DataFrame:
    snapshot = repository.load_reconciliation_snapshot(version_id, refund_dimension)
    if snapshot.empty:
        return pd.DataFrame(
            columns=["退款單號", "來源單據號", "退款原幣金額", "退款狀態"]
        )
    status = "已退款" if refund_dimension == "REFUNDED" else "總退款"
    return pd.DataFrame(
        {
            "退款單號": [f"{refund_dimension}:{value}" for value in snapshot["source_receipt_no"]],
            "來源單據號": snapshot["source_receipt_no"].astype(str),
            "退款原幣金額": snapshot["refund_detail_amount_minor"].astype("int64") / 100,
            "退款狀態": status,
        }
    )


def build_active_gmv_read_model(
    repository: GmvRefundRepository,
    revenue_frames: RevenueFrames,
    *,
    rule_version: str,
    cache_manifest=None,
    cache_dir=None,
) -> GmvActiveReadModel:
    """Reopen the immutable active GMV version without requiring its source upload."""
    active = repository.load_active_scope()
    if active is None:
        return GmvActiveReadModel(
            "NOT_INITIALIZED", None, None, pd.DataFrame(), None, None, False
        )
    version_id = str(active["version_id"])
    current_token = revenue_state_token(revenue_frames, rule_version)
    if cache_manifest is not None:
        return load_active_gmv_read_model(
            repository=repository,
            cache_manifest=cache_manifest,
            current_revenue_token=current_token,
            cache_dir=cache_dir,
        )
    if active["revenue_generation_token"] != current_token:
        return GmvActiveReadModel(
            "STALE_REVENUE_GENERATION",
            version_id,
            active,
            repository.load_metric_snapshot(version_id),
            None,
            None,
            False,
        )

    from app_workflows import _apply_gmv_refund_adjustments

    total_rows = _refund_frame_from_reconciliation(
        repository, version_id, "TOTAL_REFUND"
    )
    paid_rows = _refund_frame_from_reconciliation(
        repository, version_id, "REFUNDED"
    )
    total_adjusted = _apply_gmv_refund_adjustments(
        revenue_frames.formal_tour, revenue_frames.formal_others, total_rows
    )
    paid_adjusted = _apply_gmv_refund_adjustments(
        revenue_frames.formal_tour,
        revenue_frames.formal_others,
        paid_rows,
        refund_status="已退款",
    )
    return GmvActiveReadModel(
        "CURRENT",
        version_id,
        active,
        repository.load_metric_snapshot(version_id),
        total_adjusted,
        paid_adjusted,
        True,
    )


def build_gmv_formal_artifacts(
    *, repository: GmvRefundRepository, version_id: str,
    revenue_frames: RevenueFrames, rule_version: str, cache_dir=None,
    builder_mode: str = "legacy", equivalence_status: str = "NOT_RUN",
    publish_active: bool = True, fallback_reason: str | None = None,
) -> GmvFormalArtifacts:
    """Calculate both formal dimensions once and persist their derived cache."""
    from app_workflows import (
        _apply_gmv_refund_adjustments, _compute_gmv_exclusion_workbooks,
        _current_rules,
        _gmv_summary_rows, build_formal_gmv_workbooks,
    )
    from backend.services.gmv_export_cache_service import build_gmv_export_cache

    active = repository.load_active_scope()
    if active is None or str(active["version_id"]) != version_id:
        raise ValueError("GMV version is not the active version")
    refunds = _refund_frame_from_reconciliation(repository, version_id, "TOTAL_REFUND")
    paid_refunds = _refund_frame_from_reconciliation(repository, version_id, "REFUNDED")
    total_adjusted = _apply_gmv_refund_adjustments(
        revenue_frames.formal_tour, revenue_frames.formal_others, refunds
    )
    paid_adjusted = _apply_gmv_refund_adjustments(
        revenue_frames.formal_tour, revenue_frames.formal_others, paid_refunds, refund_status="已退款"
    )
    total_summary_rows = _gmv_summary_rows(revenue_frames.formal_tour, revenue_frames.formal_others, total_adjusted)
    paid_summary_rows = _gmv_summary_rows(revenue_frames.formal_tour, revenue_frames.formal_others, paid_adjusted)
    export_rules = _current_rules()
    workbooks = build_formal_gmv_workbooks(
        total_adjusted=total_adjusted,
        paid_adjusted=paid_adjusted,
        total_summary_rows=total_summary_rows,
        paid_summary_rows=paid_summary_rows,
        provenance={"version_id": version_id, "revenue_generation_token": active["revenue_generation_token"]},
    )
    # The two dimensions are independent and workbook generation is the costly
    # part of cache creation. Build them concurrently without changing the
    # artifact contract or the order in which they are persisted below.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gmv-export") as executor:
        total_future = executor.submit(
            _compute_gmv_exclusion_workbooks,
            total_adjusted["tour"],
            total_adjusted["others"],
            rules=export_rules,
        )
        paid_future = executor.submit(
            _compute_gmv_exclusion_workbooks,
            paid_adjusted["tour"],
            paid_adjusted["others"],
            rules=export_rules,
        )
        total_exports = total_future.result()
        paid_exports = paid_future.result()
    total_exports = {key: value for key, value in total_exports.items() if isinstance(value, (bytes, bytearray))}
    paid_exports = {key: value for key, value in paid_exports.items() if isinstance(value, (bytes, bytearray))}
    total_exports["audit"] = workbooks["total"]
    paid_exports["audit"] = workbooks["paid"]
    manifest = build_gmv_export_cache(
        version_id=version_id,
        revenue_generation_token=str(active["revenue_generation_token"]),
        rule_version=rule_version,
        total_workbooks={f"{key}.xlsx": value for key, value in total_exports.items()},
        paid_workbooks={f"{key}.xlsx": value for key, value in paid_exports.items()},
        total_detail=total_adjusted["adjusted_detail"],
        paid_detail=paid_adjusted["adjusted_detail"],
        summaries=total_summary_rows + paid_summary_rows,
        cache_dir=cache_dir or ".nbs_runtime_cache",
        builder_mode=builder_mode,
        equivalence_status=equivalence_status,
        publish_active=publish_active,
        ready_error=fallback_reason,
    )
    return GmvFormalArtifacts(total_adjusted, paid_adjusted, total_summary_rows, paid_summary_rows, manifest)


def _gmv_artifact_kinds() -> dict[str, str]:
    from backend.services.gmv_trusted_reference_service import TRUSTED_REFERENCE_ARTIFACT_KEYS

    return {
        key: "json" if key == "summaries" else "csv" if key.endswith(".detail") else "xlsx"
        for key in TRUSTED_REFERENCE_ARTIFACT_KEYS
    }


def _read_gmv_artifacts(manifest, cache_dir) -> dict[str, bytes]:
    from backend.services.gmv_export_cache_service import read_gmv_export_artifact

    return {
        key: read_gmv_export_artifact(manifest, cache_dir or ".nbs_runtime_cache", key)
        for key in manifest.artifacts
    }


def _build_trusted_reference_manifest(*, seed_manifest, seed_artifacts, active, rule_version: str):
    from backend.services.gmv_export_equivalence_service import build_gmv_artifact_semantic_records
    from backend.services.gmv_trusted_reference_service import (
        TRUSTED_ARTIFACT_CONTRACT_VERSION, TRUSTED_REFERENCE_ID_PREFIX,
        TRUSTED_REFERENCE_SCHEMA_VERSION, TrustedReferenceArtifact,
        TrustedReferenceManifest, TrustedReferenceSource, build_gmv_content_fingerprint,
    )

    source = TrustedReferenceSource(
        revenue_generation_token=str(active["revenue_generation_token"]),
        refund_state_sha256=str(active["refund_state_sha256"]),
        rule_version=str(rule_version),
        export_schema_version=GMV_EXPORT_SCHEMA_VERSION,
        pipeline_fingerprint=GMV_PIPELINE_FINGERPRINT,
        serializer_version=GMV_SERIALIZER_VERSION,
    )
    fingerprint = build_gmv_content_fingerprint(
        revenue_generation_token=source.revenue_generation_token,
        refund_state_sha256=source.refund_state_sha256,
        rule_version=source.rule_version,
        export_schema_version=source.export_schema_version,
        pipeline_fingerprint=source.pipeline_fingerprint,
        serializer_version=source.serializer_version,
    )
    records = build_gmv_artifact_semantic_records(seed_artifacts, _gmv_artifact_kinds())
    return TrustedReferenceManifest(
        schema_version=TRUSTED_REFERENCE_SCHEMA_VERSION,
        reference_id=f"{TRUSTED_REFERENCE_ID_PREFIX}{fingerprint}",
        content_fingerprint=fingerprint,
        status="TRUSTED",
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        seed_mode="LEGACY_SEED",
        source=source,
        artifact_contract_version=TRUSTED_ARTIFACT_CONTRACT_VERSION,
        artifacts={key: TrustedReferenceArtifact.from_dict(record) for key, record in records.items()},
        seed_provenance={
            "cacheKey": str(seed_manifest.cache_key),
            "generationPath": f"{seed_manifest.version_id}/{seed_manifest.generation_path}",
            "manifestSha256": hashlib.sha256(
                (json.dumps(seed_manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            ).hexdigest(),
        },
    )


def _cached_formal_artifacts(*, manifest, cache_dir) -> GmvFormalArtifacts:
    summaries = json.loads(_read_gmv_artifacts(manifest, cache_dir)["summaries"].decode("utf-8"))
    total_rows = [row for row in summaries if row.get("退款維度") == "總退款"]
    paid_rows = [row for row in summaries if row.get("退款維度") == "已退款"]
    return GmvFormalArtifacts(
        _cached_adjusted(_read_gmv_artifacts(manifest, cache_dir)["total.detail"], total_rows, "總退款"),
        _cached_adjusted(_read_gmv_artifacts(manifest, cache_dir)["paid.detail"], paid_rows, "已退款"),
        total_rows,
        paid_rows,
        manifest,
    )


def _gmv_scope_masks_for_adjusted_frames(
    tour: pd.DataFrame, others: pd.DataFrame,
) -> dict[str, tuple[pd.Series, pd.Series]]:
    """Derive receipt-scope masks against the post-refund frame indexes."""
    from backend.services.gmv_export_intermediate_service import _scope_masks

    tour_masks = _scope_masks(tour)
    others_masks = _scope_masks(others)
    return {
        scope_id: (tour_masks[scope_id], others_masks[scope_id])
        for scope_id in ("all", "no_writeoff", "official")
    }


def _gmv_preparation_checksum_status(preparation) -> str:
    """Verify preparation fingerprints against the frames they describe."""
    try:
        from backend.services.gmv_export_intermediate_service import _stable_frame_fingerprint

        expected = {
            "tour": _stable_frame_fingerprint(preparation.tour),
            "others": _stable_frame_fingerprint(preparation.others),
        }
        actual = dict(preparation.source_fingerprints)
        return "PASS" if actual == expected and all(len(value) == 64 for value in actual.values()) else "FAIL"
    except (AttributeError, TypeError, ValueError, KeyError):
        return "FAIL"


def _gmv_baseline_status(*, repository: GmvRefundRepository, generation_token: str, cache_dir) -> str:
    """Evaluate blocking monthly baselines through cached Dashboard Facts only."""
    try:
        from app_workflows import _current_rules
        from backend.services.dashboard_analytics_service import build_analytics_from_facts
        from backend.services.dashboard_facts_service import build_dashboard_facts
        from backend.services.monthly_baseline_service import evaluate_monthly_baselines
        from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL

        branch_mapping, target_branches, cruise_depts, sales_reps, _ = _current_rules()
        facts = build_dashboard_facts(
            db_path=repository.db_path,
            generation_token=str(generation_token),
            branch_mapping=branch_mapping,
            target_branches_s3=target_branches,
            cruise_depts=cruise_depts,
            sales_rep_list=sales_reps,
            cache_dir=cache_dir,
        )
        evaluation = evaluate_monthly_baselines(
            analytics_builder=lambda filters: {
                "revenueScope": REVENUE_SCOPE_LABEL,
                **build_analytics_from_facts(
                    facts["branchFacts"], facts["specialistFacts"], filters,
                ),
            },
        )
        return "PASS" if (
            evaluation.get("scope") == REVENUE_SCOPE_LABEL
            and evaluation.get("blockingStatus") == "matched"
        ) else "FAIL"
    except (AttributeError, KeyError, OSError, TypeError, ValueError, RuntimeError):
        return "FAIL"


def _annotate_gmv_fallback(result: GmvFormalArtifacts, reason: str) -> GmvFormalArtifacts:
    if not isinstance(result, GmvFormalArtifacts):
        return result
    manifest = replace(
        result.cache_manifest,
        builder_mode="legacy_fallback",
        validation_mode="legacy_fallback",
        error=reason[:240],
    )
    return replace(result, cache_manifest=manifest)


def build_gmv_formal_artifacts_fast_or_legacy(
    *, repository: GmvRefundRepository, version_id: str,
    revenue_frames: RevenueFrames, rule_version: str, cache_dir=None,
    worker_count: int = 3, validation_mode: str = "trusted_warm",
) -> GmvFormalArtifacts:
    """Use trusted warm validation and a private legacy seed on cold miss."""
    cache_root = cache_dir or ".nbs_runtime_cache"
    previous_manifest = None
    seed_manifest = None
    try:
        from backend.services.gmv_export_cache_service import load_gmv_export_cache, build_gmv_export_cache
        from backend.services.gmv_export_equivalence_service import (
            build_gmv_artifact_semantic_records, compare_gmv_artifact_semantics,
        )
        from backend.services.gmv_trusted_reference_service import (
            build_gmv_content_fingerprint, load_trusted_reference, write_trusted_reference,
        )

        active = repository.load_active_scope()
        if active is None or str(active["version_id"]) != str(version_id):
            raise ValueError("GMV version is not the active version")
        source = {
            "revenueGenerationToken": str(active["revenue_generation_token"]),
            "refundStateSha256": str(active["refund_state_sha256"]),
            "ruleVersion": str(rule_version),
            "exportSchemaVersion": GMV_EXPORT_SCHEMA_VERSION,
            "pipelineFingerprint": GMV_PIPELINE_FINGERPRINT,
            "serializerVersion": GMV_SERIALIZER_VERSION,
        }
        content_fingerprint = build_gmv_content_fingerprint(
            revenue_generation_token=source["revenueGenerationToken"],
            refund_state_sha256=source["refundStateSha256"],
            rule_version=source["ruleVersion"],
            export_schema_version=source["exportSchemaVersion"],
            pipeline_fingerprint=source["pipelineFingerprint"],
            serializer_version=source["serializerVersion"],
        )
        previous_manifest = load_gmv_export_cache(
            version_id=version_id,
            revenue_generation_token=source["revenueGenerationToken"],
            rule_version=rule_version,
            cache_dir=cache_root,
        )
        reference = load_trusted_reference(
            cache_dir=cache_root, content_fingerprint=content_fingerprint, expected_source=source,
        )
        reference_was_missing = reference is None
        if reference is None:
            seed_result = build_gmv_formal_artifacts(
                repository=repository, version_id=version_id, revenue_frames=revenue_frames,
                rule_version=rule_version, cache_dir=cache_root,
                builder_mode="legacy_seed", equivalence_status="NOT_RUN", publish_active=False,
            )
            seed_manifest = seed_result.cache_manifest
            if seed_manifest.status != "ready":
                raise RuntimeError(seed_manifest.error or "legacy seed cache failed")
            seed_artifacts = _read_gmv_artifacts(seed_manifest, cache_root)
            reference = _build_trusted_reference_manifest(
                seed_manifest=seed_manifest, seed_artifacts=seed_artifacts,
                active=active, rule_version=rule_version,
            )
            write_trusted_reference(cache_dir=cache_root, manifest=reference)

        baseline_status = _gmv_baseline_status(
            repository=repository,
            generation_token=source["revenueGenerationToken"],
            cache_dir=cache_root,
        )

        candidate = _run_fast_export_gate(
            repository=repository, version_id=version_id, revenue_frames=revenue_frames,
            rule_version=rule_version, cache_dir=cache_root, worker_count=worker_count,
            reference_manifest=reference, baseline_status=baseline_status,
        )
        if candidate is None:
            raise RuntimeError("fast export candidate is empty")
        if reference_was_missing:
            candidate = replace(candidate, reference_status="SEED")
        candidate_manifest = build_gmv_export_cache(
            version_id=version_id,
            revenue_generation_token=str(active["revenue_generation_token"]),
            rule_version=rule_version,
            total_workbooks={
                key.split(".workbook.", 1)[1]: value
                for key, value in candidate.artifacts.items()
                if key.startswith("total.workbook.")
            },
            paid_workbooks={
                key.split(".workbook.", 1)[1]: value
                for key, value in candidate.artifacts.items()
                if key.startswith("paid.workbook.")
            },
            total_detail=candidate.total_adjusted["adjusted_detail"],
            paid_detail=candidate.paid_adjusted["adjusted_detail"],
            summaries=candidate.total_summary_rows + candidate.paid_summary_rows,
            cache_dir=cache_root,
            builder_mode="fast",
            equivalence_status=candidate.shadow_status,
            content_fingerprint=reference.content_fingerprint,
            reference_id=reference.reference_id,
            validation_mode=validation_mode,
            shadow_status=candidate.shadow_status,
            reference_status=candidate.reference_status,
            reference_manifest_sha256=hashlib.sha256(
                (json.dumps(reference.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            ).hexdigest(),
            performance=candidate.performance or {},
            fallback={"used": False, "reason": None},
            refund_state_sha256=str(active.get("refund_state_sha256") or "") or None,
        )
        if candidate_manifest.status != "ready":
            raise RuntimeError(candidate_manifest.error or "fast cache publication failed")
        return GmvFormalArtifacts(
            candidate.total_adjusted, candidate.paid_adjusted,
            candidate.total_summary_rows, candidate.paid_summary_rows, candidate_manifest,
        )
    except Exception as exc:
        fallback_reason = f"fast_fallback:{type(exc).__name__}: {str(exc)[:180]}"
        if previous_manifest is not None and previous_manifest.status == "ready":
            return _annotate_gmv_fallback(
                _cached_formal_artifacts(manifest=previous_manifest, cache_dir=cache_root), fallback_reason,
            )
        if seed_manifest is not None and seed_manifest.status == "ready":
            from backend.services.gmv_export_cache_service import publish_gmv_export_cache_manifest
            publish_gmv_export_cache_manifest(cache_dir=cache_root, manifest=seed_manifest)
            return _annotate_gmv_fallback(
                _cached_formal_artifacts(manifest=seed_manifest, cache_dir=cache_root), fallback_reason,
            )
        return build_gmv_formal_artifacts(
            repository=repository, version_id=version_id, revenue_frames=revenue_frames,
            rule_version=rule_version, cache_dir=cache_root,
            builder_mode="legacy_fallback", equivalence_status="FALLBACK",
            fallback_reason=fallback_reason,
        )


def _run_fast_export_gate(
    *, repository: GmvRefundRepository, version_id: str, revenue_frames: RevenueFrames,
    rule_version: str, cache_dir, worker_count: int, reference_manifest, baseline_status: str,
) -> GmvFastCandidate:
    """Execute the real preparation/facts/serializer/equivalence gate.

    The legacy builder remains the reference publisher until all candidate
    workbook semantics match. Any exception intentionally triggers fallback.
    """
    from app_workflows import (
        _apply_gmv_refund_adjustments, _current_rules, _gmv_summary_rows,
        build_formal_gmv_workbooks,
    )
    from backend.services.gmv_export_equivalence_service import (
        build_gmv_artifact_semantic_records, compare_gmv_artifact_semantics,
    )
    from backend.services.gmv_export_intermediate_service import (
        build_gmv_export_base_preparation, build_gmv_report_fact_set,
    )
    from backend.services.gmv_export_serializer_service import (
        SerializerPublicationGate, build_gmv_serializer_jobs, bounded_serializer_timeout_seconds,
        serialize_gmv_workbooks_parallel,
    )

    gate_started = time.perf_counter()
    active = repository.load_active_scope()
    if active is None or str(active["version_id"]) != str(version_id):
        raise ValueError("GMV version is not active for fast export")
    if reference_manifest is None or getattr(reference_manifest, "status", None) not in {"ready", "TRUSTED"}:
        raise ValueError("fast export requires a trusted reference manifest")
    generation_token = str(active["revenue_generation_token"])
    total_rows = _refund_frame_from_reconciliation(repository, version_id, "TOTAL_REFUND")
    paid_rows = _refund_frame_from_reconciliation(repository, version_id, "REFUNDED")
    total_adjusted = _apply_gmv_refund_adjustments(
        revenue_frames.formal_tour, revenue_frames.formal_others, total_rows,
    )
    paid_adjusted = _apply_gmv_refund_adjustments(
        revenue_frames.formal_tour, revenue_frames.formal_others, paid_rows, refund_status="已退款",
    )
    total_adjusted["tour"].attrs["gmv_refund_dimension"] = "總退款"
    total_adjusted["others"].attrs["gmv_refund_dimension"] = "總退款"
    paid_adjusted["tour"].attrs["gmv_refund_dimension"] = "已退款"
    paid_adjusted["others"].attrs["gmv_refund_dimension"] = "已退款"
    total_summary_rows = _gmv_summary_rows(revenue_frames.formal_tour, revenue_frames.formal_others, total_adjusted)
    paid_summary_rows = _gmv_summary_rows(revenue_frames.formal_tour, revenue_frames.formal_others, paid_adjusted)
    audit_workbooks = build_formal_gmv_workbooks(
        total_adjusted=total_adjusted,
        paid_adjusted=paid_adjusted,
        total_summary_rows=total_summary_rows,
        paid_summary_rows=paid_summary_rows,
        provenance={"version_id": version_id, "revenue_generation_token": generation_token},
    )
    rules = _current_rules()
    prep_started = time.perf_counter()
    prep = build_gmv_export_base_preparation(
        version_id=version_id, revenue_generation_token=generation_token,
        rules_fingerprint=rule_version, export_schema_version="gmv-formal-export-v2",
        pipeline_fingerprint="pipeline-gmv-fast-v1", tour=revenue_frames.formal_tour,
        others=revenue_frames.formal_others,
    )
    prep_ms = (time.perf_counter() - prep_started) * 1000
    checksum_status = _gmv_preparation_checksum_status(prep)
    with tempfile.TemporaryDirectory(prefix="gmv-fast-gate-", dir=cache_dir) as raw_dir:
        gate_dir = Path(raw_dir)
        job_specs = (
            ("total", "總退款", total_adjusted),
            ("paid", "已退款", paid_adjusted),
        )
        fact_sets = {}
        facts_started = time.perf_counter()
        for dimension_key, dimension_label, adjusted in job_specs:
            fact_sets[dimension_key] = build_gmv_report_fact_set(
                preparation=prep,
                adjusted_tour=adjusted["tour"],
                adjusted_others=adjusted["others"],
                dimension=dimension_label,
                rules=rules,
                include_branch_salesperson_sheet=True,
            )
        facts_schema_status = "PASS" if all(
            facts.schema_fingerprint and facts.data_fingerprint
            for fact_set in fact_sets.values()
            for facts in fact_set.facts_by_scope.values()
        ) else "FAIL"
        facts_ms = (time.perf_counter() - facts_started) * 1000
        staging_gate = SerializerPublicationGate(
            "PENDING", checksum_status, facts_schema_status, baseline_status,
            "PENDING", staging_only=True,
        )
        jobs = build_gmv_serializer_jobs(
            total_facts=fact_sets["total"], paid_facts=fact_sets["paid"],
            staging_dir=gate_dir, publication_gate=staging_gate,
        )
        serialization_started = time.perf_counter()
        results = serialize_gmv_workbooks_parallel(
            jobs,
            max_workers=max(1, min(worker_count, 3)),
            timeout_seconds=bounded_serializer_timeout_seconds(jobs),
        )
        serialization_ms = (time.perf_counter() - serialization_started) * 1000
        if not all(result.status == "READY" for result in results):
            failures = "; ".join(
                f"{result.artifact_id}:{result.status}:{result.error or 'unknown'}"
                for result in results if result.status != "READY"
            )
            raise RuntimeError(
                f"fast serializer gate did not produce READY artifacts: {failures[:500]}"
            )
        candidate = {
            key: result.path.read_bytes()
            for key, result in zip((job.artifact_id for job in jobs), results)
        }
        candidate.update({
            "total.detail": total_adjusted["adjusted_detail"].to_csv(index=False).encode("utf-8"),
            "paid.detail": paid_adjusted["adjusted_detail"].to_csv(index=False).encode("utf-8"),
            "total.workbook.audit.xlsx": audit_workbooks["total"],
            "paid.workbook.audit.xlsx": audit_workbooks["paid"],
            "summaries": json.dumps(
                total_summary_rows + paid_summary_rows,
                ensure_ascii=False, sort_keys=True, indent=2,
            ).encode("utf-8"),
        })
        # content_fingerprint identifies the upstream source/contract tuple;
        # this exact comparison is the artifact-semantic identity boundary.
        # It must run before the active cache pointer can be published.
        reference_records = reference_manifest.artifacts
        candidate_records = build_gmv_artifact_semantic_records(candidate, _gmv_artifact_kinds())
        equivalence_started = time.perf_counter()
        comparison = compare_gmv_artifact_semantics(
            {
                key: artifact.to_dict() if hasattr(artifact, "to_dict") else artifact
                for key, artifact in reference_records.items()
            },
            candidate_records,
        )
        equivalence_ms = (time.perf_counter() - equivalence_started) * 1000
        equivalence_status = comparison.status
        shadow_status = comparison.status
        if comparison.status != "PASS" or comparison.mismatch_count:
            raise RuntimeError(
                f"fast semantic shadow failed: {comparison.mismatch_count} mismatches; "
                f"examples={comparison.mismatch_examples[:2]}"
            )
        publication_gate = SerializerPublicationGate(
            equivalence_status, checksum_status, facts_schema_status, baseline_status, shadow_status,
        )
        if not publication_gate.ready:
            raise RuntimeError("fast serializer publication gate did not pass shadow validation")
        return GmvFastCandidate(
            artifacts=candidate,
            total_adjusted=total_adjusted,
            paid_adjusted=paid_adjusted,
            total_summary_rows=total_summary_rows,
            paid_summary_rows=paid_summary_rows,
            shadow_status=shadow_status,
            reference_status="HIT" if reference_manifest is not None else "MISS",
            performance={
                "stageTimings": [
                    {"stage": "preparation", "ms": round(prep_ms, 1)},
                    {"stage": "facts", "ms": round(facts_ms, 1)},
                    {"stage": "serialization", "ms": round(serialization_ms, 1)},
                    {"stage": "equivalence", "ms": round(equivalence_ms, 1)},
                    {"stage": "publish", "ms": 0.0},
                ],
                "totalMs": round((time.perf_counter() - gate_started) * 1000, 1),
            },
        )


def load_active_gmv_read_model(
    *, repository: GmvRefundRepository, cache_manifest, current_revenue_token: str,
    cache_dir=None,
) -> GmvActiveReadModel:
    """Read active formal dimensions from verified cache artifacts, never revenue frames."""
    active = repository.load_active_scope()
    if active is None:
        return GmvActiveReadModel("NOT_INITIALIZED", None, None, pd.DataFrame(), None, None, False)
    version_id = str(active["version_id"])
    if str(active["revenue_generation_token"]) != current_revenue_token:
        return GmvActiveReadModel("STALE_REVENUE_GENERATION", version_id, active, repository.load_metric_snapshot(version_id), None, None, False)
    if cache_manifest is None or getattr(cache_manifest, "status", None) != "ready":
        return GmvActiveReadModel("CACHE_NOT_READY", version_id, active, repository.load_metric_snapshot(version_id), None, None, False)
    from backend.services.gmv_export_cache_service import read_gmv_export_artifact
    try:
        total_detail = read_gmv_export_artifact(cache_manifest, cache_dir or ".nbs_runtime_cache", "total.detail")
        paid_detail = read_gmv_export_artifact(cache_manifest, cache_dir or ".nbs_runtime_cache", "paid.detail")
        summaries = json.loads(read_gmv_export_artifact(cache_manifest, cache_dir or ".nbs_runtime_cache", "summaries").decode("utf-8"))
        total_rows = [row for row in summaries if row.get("退款維度") == "總退款"]
        paid_rows = [row for row in summaries if row.get("退款維度") == "已退款"]
        _validate_cached_summary_contract(total_rows, "總退款")
        _validate_cached_summary_contract(paid_rows, "已退款")
        total_adjusted = _cached_adjusted(total_detail, total_rows, "總退款")
        paid_adjusted = _cached_adjusted(paid_detail, paid_rows, "已退款")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return GmvActiveReadModel("CACHE_INVALID", version_id, active, repository.load_metric_snapshot(version_id), None, None, False)
    return GmvActiveReadModel("CURRENT", version_id, active, repository.load_metric_snapshot(version_id), total_adjusted, paid_adjusted, True)


def _validate_cached_summary_contract(summary_rows: list[dict[str, object]], dimension: str) -> None:
    required_metrics = {
        "退款明細金額",
        "實際扣減金額",
        "超額退款金額",
        "排除前 GMV",
        "退款扣減後 GMV",
    }
    available = {str(row.get("指標")) for row in summary_rows if isinstance(row, dict)}
    missing = sorted(required_metrics - available)
    if missing:
        raise ValueError(f"incomplete {dimension} GMV summary contract: {','.join(missing)}")


def _cached_adjusted(detail_bytes: bytes, summary_rows: list[dict[str, object]], status: str) -> dict[str, object]:
    from io import BytesIO
    detail = pd.read_csv(BytesIO(detail_bytes))
    values = {str(row["指標"]): row["數值"] for row in summary_rows}
    return {
        "tour": pd.DataFrame(), "others": pd.DataFrame(), "adjusted_detail": detail,
        "refund_total": float(values.get("退款明細金額", 0)),
        "applied_refund_total": float(values.get("實際扣減金額", 0)),
        "over_refund_total": float(values.get("超額退款金額", 0)),
        "before_gmv": float(values.get("排除前 GMV", 0)),
        "matched_source_ids": set(), "unmatched_source_ids": [], "refund_amounts": pd.Series(dtype=float),
        "refund_status": status,
    }


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
