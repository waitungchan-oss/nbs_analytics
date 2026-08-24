"""Shared, immutable preparation for GMV export jobs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

import app_workflows


@dataclass(frozen=True, slots=True)
class GmvExportBaseKey:
    version_id: str
    revenue_generation_token: str
    rules_fingerprint: str
    export_schema_version: str
    pipeline_fingerprint: str


@dataclass(frozen=True, slots=True)
class GmvExportBasePreparation:
    key: GmvExportBaseKey
    tour: pd.DataFrame
    others: pd.DataFrame
    scope_masks: Mapping[str, tuple[pd.Series, pd.Series]]
    source_fingerprints: Mapping[str, str]


def _stable_frame_fingerprint(frame: pd.DataFrame) -> str:
    normalized = app_workflows.normalize_runtime_columns(frame.copy())
    columns = sorted(str(column) for column in normalized.columns)
    work = normalized.reindex(columns=columns).copy()

    def normalize(value: object) -> str:
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return str(value).strip()

    rows = sorted(
        tuple(normalize(value) for value in row)
        for row in work.itertuples(index=False, name=None)
    )
    payload = {"columns": columns, "rows": rows}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _scope_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    index = frame.index
    all_mask = pd.Series(True, index=index, dtype=bool)
    no_writeoff = all_mask.copy()
    official = all_mask.copy()
    if "收款類型" in frame.columns:
        no_writeoff &= ~frame["收款類型"].astype(str).str.strip().eq("掛賬核銷")
    if "收款方式" in frame.columns:
        official &= ~frame["收款方式"].astype(str).str.strip().eq("TT 退款轉團款")
    official &= no_writeoff
    return {"all": all_mask, "no_writeoff": no_writeoff, "official": official}


def build_gmv_export_base_preparation(
    *,
    version_id: str,
    revenue_generation_token: str,
    rules_fingerprint: str,
    export_schema_version: str,
    pipeline_fingerprint: str,
    tour: pd.DataFrame,
    others: pd.DataFrame,
) -> GmvExportBasePreparation:
    """Normalize input copies and derive deterministic scope masks.

    This function is intentionally pure with respect to its inputs and
    Streamlit session state. Cache lookup/persistence is added by the later
    controller task; this first layer only defines the reusable base contract.
    """
    normalized_tour = app_workflows.normalize_runtime_columns(tour.copy(deep=True))
    normalized_others = app_workflows.normalize_runtime_columns(others.copy(deep=True))
    tour_masks = _scope_masks(normalized_tour)
    others_masks = _scope_masks(normalized_others)
    for frame, masks in ((normalized_tour, tour_masks), (normalized_others, others_masks)):
        if app_workflows.COL_ORDER_ID in frame.columns:
            frame["__gmv_source_id"] = app_workflows.clean_invoice_number(frame[app_workflows.COL_ORDER_ID])
        else:
            frame["__gmv_source_id"] = pd.Series("", index=frame.index, dtype=str)
    key = GmvExportBaseKey(
        version_id=str(version_id),
        revenue_generation_token=str(revenue_generation_token),
        rules_fingerprint=str(rules_fingerprint),
        export_schema_version=str(export_schema_version),
        pipeline_fingerprint=str(pipeline_fingerprint),
    )
    return GmvExportBasePreparation(
        key=key,
        tour=normalized_tour,
        others=normalized_others,
        scope_masks={
            scope: (tour_masks[scope], others_masks[scope])
            for scope in ("all", "no_writeoff", "official")
        },
        source_fingerprints={
            "tour": _stable_frame_fingerprint(normalized_tour),
            "others": _stable_frame_fingerprint(normalized_others),
        },
    )
