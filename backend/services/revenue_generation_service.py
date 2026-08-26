"""Canonical semantic signature for the formal revenue read models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from database import load_all_data_from_db
from pipeline import normalize_runtime_columns

from .gmv_refund_models import canonical_payload_sha256
from .revenue_scope_service import REVENUE_SCOPE_LABEL, build_revenue_scope_frames


CORE_REVENUE_SIGNATURE_SCHEMA = "nbs-core-revenue-signature-v1"
CORE_REVENUE_TOKEN_PREFIX = "nbs-core-revenue-v1"
REVENUE_SCOPE_CONTRACT_VERSION = "revenue-scope-v1"
CORE_REVENUE_SOURCE_TABLES = ("tour_data", "others_data")


def canonical_frame_sha256(frame: pd.DataFrame) -> str:
    """Hash a normalized frame without depending on row or column order.

    This is intentionally equivalent to the original GMV frame fingerprint:
    nulls become empty strings, timestamps use ISO format, values are trimmed
    strings, and rows are sorted after columns are sorted.
    """
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

    rows = sorted(
        tuple(normalize(value) for value in row)
        for row in work.itertuples(index=False, name=None)
    )
    return canonical_payload_sha256({"columns": columns, "rows": rows})


@dataclass(frozen=True, slots=True)
class CoreRevenueSignature:
    schema_version: str
    scope_label: str
    scope_contract_version: str
    source_tables: tuple[str, ...]
    row_counts: dict[str, int]
    raw_tour_sha256: str
    raw_others_sha256: str
    formal_tour_sha256: str
    formal_others_sha256: str
    sha256: str
    token: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "scopeLabel": self.scope_label,
            "scopeContractVersion": self.scope_contract_version,
            "sourceTables": list(self.source_tables),
            "rowCounts": dict(self.row_counts),
            "rawTour": self.raw_tour_sha256,
            "rawOthers": self.raw_others_sha256,
            "formalTour": self.formal_tour_sha256,
            "formalOthers": self.formal_others_sha256,
            "sha256": self.sha256,
            "token": self.token,
        }


def build_core_revenue_signature(
    db_path: str | Path,
    rule_version: str = REVENUE_SCOPE_LABEL,
    *,
    read_only: bool = False,
) -> CoreRevenueSignature:
    """Build a deterministic signature for formal revenue inputs only."""
    path = Path(db_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"core revenue database unavailable: {path}")

    if read_only:
        db_tour, db_others = load_all_data_from_db(db_path=path, read_only=True)
    else:
        db_tour, db_others = load_all_data_from_db(db_path=path)
    raw_tour = normalize_runtime_columns(db_tour)
    raw_others = normalize_runtime_columns(db_others)
    formal_tour, formal_others, _ = build_revenue_scope_frames(raw_tour, raw_others)
    payload = {
        "schemaVersion": CORE_REVENUE_SIGNATURE_SCHEMA,
        "scopeLabel": str(rule_version),
        "scopeContractVersion": REVENUE_SCOPE_CONTRACT_VERSION,
        "sourceTables": list(CORE_REVENUE_SOURCE_TABLES),
        "rowCounts": {
            "tour_data": int(len(raw_tour)),
            "others_data": int(len(raw_others)),
        },
        "rawTour": canonical_frame_sha256(raw_tour),
        "rawOthers": canonical_frame_sha256(raw_others),
        "formalTour": canonical_frame_sha256(formal_tour),
        "formalOthers": canonical_frame_sha256(formal_others),
    }
    digest = canonical_payload_sha256(payload)
    return CoreRevenueSignature(
        schema_version=CORE_REVENUE_SIGNATURE_SCHEMA,
        scope_label=str(rule_version),
        scope_contract_version=REVENUE_SCOPE_CONTRACT_VERSION,
        source_tables=CORE_REVENUE_SOURCE_TABLES,
        row_counts=payload["rowCounts"],
        raw_tour_sha256=payload["rawTour"],
        raw_others_sha256=payload["rawOthers"],
        formal_tour_sha256=payload["formalTour"],
        formal_others_sha256=payload["formalOthers"],
        sha256=digest,
        token=f"{CORE_REVENUE_TOKEN_PREFIX}:{digest}",
    )
