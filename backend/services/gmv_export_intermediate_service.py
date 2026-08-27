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


@dataclass(frozen=True, slots=True)
class GmvReportFacts:
    dimension: str
    scope_id: str
    sheets: Mapping[str, pd.DataFrame]
    row_counts: Mapping[str, int]
    schema_fingerprint: str
    data_fingerprint: str


@dataclass(frozen=True, slots=True)
class GmvReportFactSet:
    dimension: str
    facts_by_scope: Mapping[str, GmvReportFacts]
    preparation_fingerprint: str
    aggregation_count: int


def _stable_frame_fingerprint(frame: pd.DataFrame, *, order_insensitive: bool = True) -> str:
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

    rows = [tuple(normalize(value) for value in row) for row in work.itertuples(index=False, name=None)]
    if order_insensitive:
        rows.sort()
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


def build_gmv_report_facts(
    *,
    adjusted_tour: pd.DataFrame,
    adjusted_others: pd.DataFrame,
    scope_id: str,
    rules: tuple[dict, list[str], list[str], list[str], list[str]],
    include_branch_salesperson_sheet: bool,
    dimension: str = "總退款",
) -> GmvReportFacts:
    """Build legacy-compatible report tables without creating XLSX bytes."""
    import pipeline

    if dimension not in {"總退款", "已退款"}:
        raise ValueError(f"unsupported GMV refund dimension: {dimension}")
    for frame in (adjusted_tour, adjusted_others):
        if frame.attrs.get("gmv_refund_dimension") != dimension:
            raise ValueError(
                f"GMV report facts require dimension-tagged adjusted frames: {dimension}"
            )

    if scope_id == "all":
        _, _, facts = pipeline.build_dashboard_data(
            adjusted_tour, adjusted_others, *rules[:4], make_workbook=False,
            include_branch_salesperson_sheet=include_branch_salesperson_sheet,
            return_facts=True,
        )
    elif scope_id == "no_writeoff":
        _, _, facts = pipeline.build_dashboard_data_excluding_receipt_types(
            adjusted_tour, adjusted_others, *rules[:4], ["掛賬核銷"],
            make_workbook=False,
            include_branch_salesperson_sheet=include_branch_salesperson_sheet,
            return_facts=True,
        )
    elif scope_id == "official":
        _, _, facts = pipeline.build_dashboard_data_excluding_receipt_types(
            adjusted_tour, adjusted_others, *rules[:4], ["掛賬核銷"],
            excluded_payment_methods=["TT 退款轉團款"],
            make_workbook=False,
            include_branch_salesperson_sheet=include_branch_salesperson_sheet,
            return_facts=True,
        )
    else:
        raise ValueError(f"unsupported GMV export scope: {scope_id}")
    if not isinstance(facts, dict):
        raise ValueError("pipeline did not return report facts")
    schema_payload = {name: [str(column) for column in frame.columns] for name, frame in facts.items()}
    data_payload = {
        name: _stable_frame_fingerprint(frame, order_insensitive=False)
        for name, frame in facts.items()
    }
    return GmvReportFacts(
        dimension=str(dimension),
        scope_id=scope_id,
        sheets={name: frame.copy(deep=True) for name, frame in facts.items()},
        row_counts={name: int(len(frame)) for name, frame in facts.items()},
        schema_fingerprint=hashlib.sha256(
            json.dumps(schema_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        data_fingerprint=hashlib.sha256(
            json.dumps(data_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def _scope_frame(frame: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """Apply a preparation mask safely to an adjusted frame."""
    if frame.empty:
        return frame.copy()
    if len(mask) == len(frame) and mask.index.equals(frame.index):
        return frame.loc[mask].copy()
    if len(mask) == len(frame):
        return frame.loc[mask.to_numpy(dtype=bool)].copy()
    aligned = mask.reindex(frame.index, fill_value=False)
    return frame.loc[aligned].copy()


def build_gmv_report_fact_set(
    *, preparation: GmvExportBasePreparation,
    adjusted_tour: pd.DataFrame,
    adjusted_others: pd.DataFrame,
    dimension: str,
    rules: tuple[dict, list[str], list[str], list[str], list[str]],
    include_branch_salesperson_sheet: bool,
) -> GmvReportFactSet:
    """Build all scope facts from one normalized preparation boundary."""
    if dimension not in {"總退款", "已退款"}:
        raise ValueError(f"unsupported GMV refund dimension: {dimension}")
    facts_by_scope: dict[str, GmvReportFacts] = {}
    for scope_id in ("all", "no_writeoff", "official"):
        tour_mask, others_mask = preparation.scope_masks[scope_id]
        scoped_tour = _scope_frame(adjusted_tour, tour_mask)
        scoped_others = _scope_frame(adjusted_others, others_mask)
        scoped_tour.attrs["gmv_refund_dimension"] = dimension
        scoped_others.attrs["gmv_refund_dimension"] = dimension
        facts_by_scope[scope_id] = build_gmv_report_facts(
            adjusted_tour=scoped_tour,
            adjusted_others=scoped_others,
            scope_id=scope_id,
            rules=rules,
            include_branch_salesperson_sheet=(include_branch_salesperson_sheet and scope_id == "official"),
            dimension=dimension,
        )
    return GmvReportFactSet(
        dimension=dimension,
        facts_by_scope=facts_by_scope,
        preparation_fingerprint=hashlib.sha256(
            json.dumps(dict(preparation.source_fingerprints), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        aggregation_count=1,
    )
