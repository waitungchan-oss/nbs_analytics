"""Shared, read-only inputs for the Data Export Center fast path."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import pandas as pd

from config import COL_BRANCH, COL_MONEY, COL_ORDER_ID, COL_TRANS_TIME
from pipeline import normalize_runtime_columns


EXPORT_INTERMEDIATE_SCHEMA = "export-intermediate-v1"
OFFICIAL_EXCLUDED_RECEIPT_TYPES = frozenset({"掛賬核銷"})
OFFICIAL_EXCLUDED_PAYMENT_METHODS = frozenset({"TT 退款轉團款"})


class ExportScope(str, Enum):
    ALL = "all"
    NO_WRITEOFF = "no_writeoff"
    OFFICIAL = "official"


@dataclass(frozen=True, slots=True)
class DashboardReportInputs:
    scope_id: str
    tour: pd.DataFrame
    others: pd.DataFrame
    shared_aggregates: Mapping[str, pd.DataFrame]


@dataclass(frozen=True, slots=True)
class DashboardReportFacts:
    scope_id: str
    tour: pd.DataFrame
    others: pd.DataFrame
    aggregates: Mapping[str, pd.DataFrame]
    schema_fingerprint: str
    data_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExportIntermediateModel:
    generation_token: str
    rules_fingerprint: str
    schema_version: str
    normalized_tour: pd.DataFrame
    normalized_others: pd.DataFrame
    classified_tour: pd.DataFrame
    classified_others: pd.DataFrame
    shared_aggregates: Mapping[str, pd.DataFrame]
    source_fingerprints: Mapping[str, str]


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    work = frame.copy(deep=True)
    if work.empty:
        payload = {"columns": [str(column) for column in work.columns], "rows": []}
    else:
        columns = sorted((str(column) for column in work.columns))
        work.columns = [str(column) for column in work.columns]
        work = work.reindex(columns=columns).fillna("").astype("string")
        work = work.sort_values(columns, kind="mergesort").reset_index(drop=True)
        payload = {"columns": columns, "rows": work.to_numpy(dtype=object).tolist()}
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _classification(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    result = frame.copy(deep=True)
    result["_export_source"] = source
    result["_export_order_key"] = result.get(COL_ORDER_ID, pd.Series("", index=result.index)).astype(str).str.strip()
    return result


def _aggregate_by_date_branch(
    tour: pd.DataFrame,
    others: pd.DataFrame,
    value_column: str,
) -> pd.DataFrame:
    amount_columns = [COL_ORDER_ID, COL_BRANCH, "統一日期", COL_TRANS_TIME, value_column, "_export_source"]
    frames = []
    for frame in (tour, others):
        selected = frame.reindex(columns=[column for column in amount_columns if column in frame.columns]).copy()
        if not selected.empty:
            frames.append(selected)
    if not frames:
        return pd.DataFrame(columns=[COL_BRANCH, "統一日期", "_export_source", value_column])
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined[value_column] = pd.to_numeric(combined.get(value_column, 0), errors="coerce").fillna(0)
    aggregate = (
        combined.groupby([COL_BRANCH, "統一日期", "_export_source"], dropna=False)[value_column]
        .sum()
        .reset_index()
        .sort_values([COL_BRANCH, "統一日期", "_export_source"])
        .reset_index(drop=True)
    )
    return aggregate


def _build_shared_aggregates(tour: pd.DataFrame, others: pd.DataFrame) -> Mapping[str, pd.DataFrame]:
    return {
        "amount_by_date_branch": _aggregate_by_date_branch(tour, others, COL_MONEY),
        "quantity_by_date_branch": _aggregate_by_date_branch(tour, others, "數量"),
    }


def build_export_intermediate(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    generation_token: str,
    rules_fingerprint: str,
    schema_version: str,
) -> ExportIntermediateModel:
    normalized_tour = normalize_runtime_columns(raw_tour)
    normalized_others = normalize_runtime_columns(raw_others)
    classified_tour = _classification(normalized_tour, "tour")
    classified_others = _classification(normalized_others, "others")
    return ExportIntermediateModel(
        generation_token=str(generation_token),
        rules_fingerprint=str(rules_fingerprint),
        schema_version=str(schema_version),
        normalized_tour=normalized_tour,
        normalized_others=normalized_others,
        classified_tour=classified_tour,
        classified_others=classified_others,
        shared_aggregates=_build_shared_aggregates(classified_tour, classified_others),
        source_fingerprints={
            "tour": _frame_fingerprint(normalized_tour),
            "others": _frame_fingerprint(normalized_others),
        },
    )


def _scope_mask(frame: pd.DataFrame, scope: ExportScope) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    if scope in (ExportScope.NO_WRITEOFF, ExportScope.OFFICIAL) and "收款類型" in frame.columns:
        mask &= ~frame["收款類型"].astype(str).str.strip().isin(OFFICIAL_EXCLUDED_RECEIPT_TYPES)
    if scope is ExportScope.OFFICIAL and "收款方式" in frame.columns:
        mask &= ~frame["收款方式"].astype(str).str.strip().isin(OFFICIAL_EXCLUDED_PAYMENT_METHODS)
    return mask


def build_scope_report_inputs(intermediate: ExportIntermediateModel, scope: ExportScope) -> DashboardReportInputs:
    if not isinstance(scope, ExportScope):
        scope = ExportScope(str(scope))
    tour = intermediate.classified_tour.loc[_scope_mask(intermediate.classified_tour, scope)].copy(deep=True)
    others = intermediate.classified_others.loc[_scope_mask(intermediate.classified_others, scope)].copy(deep=True)
    return DashboardReportInputs(
        scope_id=scope.value,
        tour=tour,
        others=others,
        shared_aggregates=intermediate.shared_aggregates,
    )


def build_scope_report_facts(
    intermediate: ExportIntermediateModel,
    scope: ExportScope,
) -> DashboardReportFacts:
    if not isinstance(scope, ExportScope):
        scope = ExportScope(str(scope))
    inputs = build_scope_report_inputs(intermediate, scope)
    aggregates = _build_shared_aggregates(inputs.tour, inputs.others)
    schema_payload = {
        "scope": scope.value,
        "tour": [str(column) for column in inputs.tour.columns],
        "others": [str(column) for column in inputs.others.columns],
        "aggregates": {key: [str(column) for column in value.columns] for key, value in aggregates.items()},
    }
    data_payload = {
        "tour": _frame_fingerprint(inputs.tour),
        "others": _frame_fingerprint(inputs.others),
        "aggregates": {key: _frame_fingerprint(value) for key, value in aggregates.items()},
    }
    return DashboardReportFacts(
        scope_id=scope.value,
        tour=inputs.tour,
        others=inputs.others,
        aggregates=aggregates,
        schema_fingerprint=hashlib.sha256(repr(schema_payload).encode("utf-8")).hexdigest(),
        data_fingerprint=hashlib.sha256(repr(data_payload).encode("utf-8")).hexdigest(),
    )


__all__ = [
    "DashboardReportFacts",
    "DashboardReportInputs",
    "EXPORT_INTERMEDIATE_SCHEMA",
    "ExportIntermediateModel",
    "ExportScope",
    "build_export_intermediate",
    "build_scope_report_inputs",
    "build_scope_report_facts",
]
