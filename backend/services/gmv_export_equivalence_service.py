"""Semantic equivalence gates for GMV export artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Mapping

import pandas as pd

from .export_equivalence_service import canonicalize_workbook, compare_workbooks
from .gmv_export_cache_service import CANONICAL_CACHE_ARTIFACT_KEYS


VALID_DIMENSIONS = {"total", "paid"}
CANONICAL_ARTIFACT_IDS = frozenset(key.replace(".workbook.", ".") for key in CANONICAL_CACHE_ARTIFACT_KEYS)
MONEY_COLUMNS = (
    "收款原幣金額", "退款原幣金額", "退款扣減金額", "退款後收款原幣金額",
    "實際扣減金額", "超額退款金額", "數值", "金額合計",
)
STABLE_KEY_COLUMNS = ("來源單據號", "退款單號", "指標", "日期", "統一日期")
_DYNAMIC_PROVENANCE_KEYS = frozenset({
    "version_id", "versionid", "generated_at", "generatedat", "created_at", "createdat",
    "timestamp", "generation_path", "generationpath", "cache_key", "cachekey",
    "reference_id", "referenceid", "build_duration_ms", "builddurationms",
})
_JSON_MONEY_KEYS = frozenset(MONEY_COLUMNS) | frozenset({
    "amount", "refundtotal", "appliedrefundtotal", "unmatchedamount",
    "refund_total", "applied_refund_total", "unmatched_amount",
})
_JSON_PROVENANCE_CONTAINER_KEYS = frozenset({"provenance", "export_provenance", "exportprovenance"})


@dataclass(frozen=True, slots=True)
class EquivalenceResult:
    artifact_id: str
    status: str
    schema_fingerprint: str
    data_fingerprint: str
    row_counts: Mapping[str, int]
    mismatch_count: int
    mismatch_examples: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class EquivalenceReport:
    status: str
    mismatch_count: int
    artifact_results: Mapping[str, EquivalenceResult]
    mismatch_examples: tuple[Mapping[str, object], ...]


def _expected_artifact_kind(artifact_id: str) -> str:
    logical_id = _logical_artifact_id(artifact_id)
    if logical_id.endswith(".xlsx"):
        return "xlsx"
    if logical_id.endswith(".detail"):
        return "csv"
    if logical_id == "summaries":
        return "json"
    raise ValueError(f"unknown GMV artifact identity: {artifact_id}")


def _normalized_scalar(value: Any, *, money: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if money:
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _is_dynamic_provenance_label(value: object) -> bool:
    normalized = str(value or "").strip().replace("-", "_").lower()
    return normalized in _DYNAMIC_PROVENANCE_KEYS or any(
        token in normalized for token in ("timestamp", "generated_at", "created_at", "build_duration")
    )


def _workbook_semantic_record(data: bytes) -> dict[str, object]:
    canonical = canonicalize_workbook(
        data,
        money_columns=MONEY_COLUMNS,
        stable_key_columns=STABLE_KEY_COLUMNS,
    )
    sheets: list[tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]]] = []
    for sheet_name, headers, rows in canonical.sheets:
        filtered_rows = rows
        if str(sheet_name).strip().lower() == "provenance" or headers[:1] == ("欄位",):
            filtered_rows = tuple(
                row for row in rows
                if not row or not _is_dynamic_provenance_label(row[0])
            )
        sheets.append((sheet_name, headers, filtered_rows))
    schema_payload = [(name, headers) for name, headers, _ in sheets]
    semantic_payload = [(name, headers, rows) for name, headers, rows in sheets]
    return {
        "kind": "xlsx",
        "schemaFingerprint": hashlib.sha256(
            json.dumps(schema_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "semanticFingerprint": hashlib.sha256(
            json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "rowCount": sum(len(rows) for _, _, rows in sheets),
        "sheetCount": len(sheets),
    }


def _csv_semantic_record(data: bytes) -> dict[str, object]:
    frame = pd.read_csv(BytesIO(data), dtype=object, keep_default_na=False)
    columns = tuple(str(column).strip() for column in frame.columns)
    money_indexes = {index for index, column in enumerate(columns) if column in MONEY_COLUMNS}
    rows = [
        tuple(_normalized_scalar(value, money=index in money_indexes) for index, value in enumerate(row))
        for row in frame.itertuples(index=False, name=None)
    ]
    stable_indexes = tuple(index for index, column in enumerate(columns) if column in STABLE_KEY_COLUMNS)
    if stable_indexes:
        rows.sort(key=lambda row: tuple(row[index] for index in stable_indexes))
    schema_payload = {"columns": columns}
    semantic_payload = {"columns": columns, "rows": rows}
    return {
        "kind": "csv",
        "schemaFingerprint": hashlib.sha256(
            json.dumps(schema_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "semanticFingerprint": hashlib.sha256(
            json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "rowCount": len(rows),
        "sheetCount": 0,
    }


def _json_key_label(value: object) -> str:
    return str(value or "").strip().replace("-", "_").lower()


def _normalized_json_number(value: object, *, money: bool = False) -> str:
    number = Decimal(str(value))
    if money:
        number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        number = number.normalize()
    if number == 0:
        return "0.00" if money else "0"
    return format(number, "f")


def _normalize_json_value(
    value: object, *, key: object = None, provenance_context: bool = False,
) -> object:
    key_label = _json_key_label(key)
    if isinstance(value, dict):
        current_provenance_context = provenance_context or key_label in _JSON_PROVENANCE_CONTAINER_KEYS
        return {
            str(item_key): _normalize_json_value(
                item_value, key=item_key, provenance_context=current_provenance_context,
            )
            for item_key, item_value in value.items()
            if not (current_provenance_context and _is_dynamic_provenance_label(item_key))
        }
    if isinstance(value, list):
        # JSON arrays are ordered unless the artifact contract explicitly says
        # otherwise. Sorting here could hide a business-visible report-order
        # change behind the shadow gate.
        return [
            _normalize_json_value(item, key=key, provenance_context=provenance_context)
            for item in value
        ]
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            return _normalized_json_number(value, money=key_label in _JSON_MONEY_KEYS)
        except Exception:
            return str(value)
    if isinstance(value, str) and key_label in _JSON_MONEY_KEYS:
        try:
            return _normalized_json_number(value, money=True)
        except Exception:
            return value.strip()
    return value


def _json_semantic_record(data: bytes) -> dict[str, object]:
    payload = json.loads(data.decode("utf-8"))
    normalized = _normalize_json_value(payload)
    if isinstance(normalized, list):
        row_count = len(normalized)
    else:
        row_count = 1
    schema_payload = {"jsonType": type(payload).__name__}
    return {
        "kind": "json",
        "schemaFingerprint": hashlib.sha256(
            json.dumps(schema_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "semanticFingerprint": hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "rowCount": row_count,
        "sheetCount": 0,
    }


def build_gmv_artifact_semantic_records(
    artifacts: Mapping[str, bytes], kinds: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    """Extract bounded semantic records without retaining a second workbook set."""
    if set(artifacts) != set(kinds):
        raise ValueError("GMV artifact keys and kinds must match")
    records: dict[str, dict[str, object]] = {}
    for artifact_id in sorted(artifacts):
        _validate_artifact_id(artifact_id)
        expected_kind = _expected_artifact_kind(artifact_id)
        kind = str(kinds[artifact_id])
        if kind != expected_kind:
            raise ValueError(f"artifact kind mismatch: {artifact_id}")
        data = artifacts[artifact_id]
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError(f"artifact bytes are invalid: {artifact_id}")
        if kind == "xlsx":
            record = _workbook_semantic_record(bytes(data))
        elif kind == "csv":
            record = _csv_semantic_record(bytes(data))
        else:
            record = _json_semantic_record(bytes(data))
        records[artifact_id] = record
    return records


def compare_gmv_artifact_semantics(
    reference: Mapping[str, Mapping[str, object]],
    candidate: Mapping[str, Mapping[str, object]],
) -> EquivalenceReport:
    """Compare compact records and fail closed on exact-key/schema/semantic drift."""
    examples: list[Mapping[str, object]] = []
    results: dict[str, EquivalenceResult] = {}
    reference_logical = {}
    candidate_logical = {}
    for artifact_id in reference:
        _validate_artifact_id(artifact_id)
        logical_id = _logical_artifact_id(artifact_id)
        if logical_id in reference_logical:
            raise ValueError(f"duplicate logical GMV artifact id: {logical_id}")
        reference_logical[logical_id] = artifact_id
    for artifact_id in candidate:
        _validate_artifact_id(artifact_id)
        logical_id = _logical_artifact_id(artifact_id)
        if logical_id in candidate_logical:
            raise ValueError(f"duplicate logical GMV artifact id: {logical_id}")
        candidate_logical[logical_id] = artifact_id
    missing = sorted(set(reference_logical) ^ set(candidate_logical))
    if set(reference_logical) != CANONICAL_ARTIFACT_IDS or set(candidate_logical) != CANONICAL_ARTIFACT_IDS:
        missing = sorted(set(missing) | (CANONICAL_ARTIFACT_IDS - set(reference_logical)) | (CANONICAL_ARTIFACT_IDS - set(candidate_logical)))
    if missing:
        examples.append({"kind": "artifact_keys", "missing_or_extra": missing})
    for logical_id in sorted(set(reference_logical) & set(candidate_logical)):
        reference_id = reference_logical[logical_id]
        candidate_id = candidate_logical[logical_id]
        left = reference[reference_id]
        right = candidate[candidate_id]
        mismatches: list[Mapping[str, object]] = []
        for field in ("kind", "schemaFingerprint", "semanticFingerprint", "rowCount", "sheetCount"):
            if left.get(field) != right.get(field):
                mismatches.append({
                    "kind": field,
                    "reference": left.get(field),
                    "candidate": right.get(field),
                })
        result = EquivalenceResult(
            artifact_id=logical_id,
            status="PASS" if not mismatches else "FAIL",
            schema_fingerprint=str(left.get("schemaFingerprint", "")),
            data_fingerprint=str(left.get("semanticFingerprint", "")),
            row_counts={"rows": int(left.get("rowCount", 0) or 0), "sheets": int(left.get("sheetCount", 0) or 0)},
            mismatch_count=len(mismatches),
            mismatch_examples=tuple(mismatches),
        )
        results[logical_id] = result
        examples.extend({"artifact": logical_id, **example} for example in mismatches)
    mismatch_count = (1 if missing else 0) + sum(result.mismatch_count for result in results.values())
    return EquivalenceReport(
        status="PASS" if mismatch_count == 0 else "FAIL",
        mismatch_count=mismatch_count,
        artifact_results=results,
        mismatch_examples=tuple(examples[:20]),
    )


def _validate_artifact_id(artifact_id: str) -> None:
    if _logical_artifact_id(artifact_id) not in CANONICAL_ARTIFACT_IDS:
        raise ValueError(f"unknown GMV artifact identity: {artifact_id}")


def _logical_artifact_id(artifact_id: str) -> str:
    value = str(artifact_id)
    if ".workbook." in value:
        dimension, filename = value.split(".workbook.", 1)
        return f"{dimension}.{filename}"
    return value


def compare_gmv_workbook_semantics(
    *,
    reference_bytes: bytes,
    candidate_bytes: bytes,
    artifact_id: str,
) -> EquivalenceResult:
    _validate_artifact_id(artifact_id)
    report = compare_workbooks(
        reference_bytes,
        candidate_bytes,
        money_columns=MONEY_COLUMNS,
        stable_key_columns=STABLE_KEY_COLUMNS,
    )
    return EquivalenceResult(
        artifact_id=artifact_id,
        status=report.status,
        schema_fingerprint=report.schema_fingerprint,
        data_fingerprint=report.data_fingerprint,
        row_counts=report.row_counts,
        mismatch_count=report.mismatch_count,
        mismatch_examples=report.mismatch_examples,
    )


def compare_gmv_artifact_sets(
    reference: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
) -> EquivalenceReport:
    for artifact_id in set(reference) | set(candidate):
        _validate_artifact_id(artifact_id)
    examples: list[Mapping[str, object]] = []
    results: dict[str, EquivalenceResult] = {}
    reference_logical = {}
    candidate_logical = {}
    for logical_id, original_id in ((_logical_artifact_id(key), key) for key in reference):
        if logical_id in reference_logical:
            raise ValueError(f"duplicate logical GMV artifact id: {logical_id}")
        reference_logical[logical_id] = original_id
    for logical_id, original_id in ((_logical_artifact_id(key), key) for key in candidate):
        if logical_id in candidate_logical:
            raise ValueError(f"duplicate logical GMV artifact id: {logical_id}")
        candidate_logical[logical_id] = original_id
    missing = sorted(set(reference_logical) ^ set(candidate_logical))
    if set(reference_logical) != CANONICAL_ARTIFACT_IDS or set(candidate_logical) != CANONICAL_ARTIFACT_IDS:
        missing = sorted(set(missing) | (CANONICAL_ARTIFACT_IDS - set(reference_logical)) | (CANONICAL_ARTIFACT_IDS - set(candidate_logical)))
    if missing:
        examples.append({"kind": "artifact_keys", "missing_or_extra": missing})
    for logical_id in sorted(set(reference_logical) & set(candidate_logical)):
        reference_id = reference_logical[logical_id]
        candidate_id = candidate_logical[logical_id]
        if logical_id.endswith(".xlsx"):
            result = compare_gmv_workbook_semantics(
                reference_bytes=reference[reference_id],
                candidate_bytes=candidate[candidate_id],
                artifact_id=logical_id,
            )
        else:
            same = reference[reference_id] == candidate[candidate_id]
            result = EquivalenceResult(
                artifact_id=logical_id,
                status="PASS" if same else "FAIL",
                schema_fingerprint=hashlib.sha256(reference[reference_id]).hexdigest(),
                data_fingerprint=hashlib.sha256(reference[reference_id]).hexdigest(),
                row_counts={},
                mismatch_count=0 if same else 1,
                mismatch_examples=() if same else ({"kind": "binary_artifact_mismatch"},),
            )
        results[logical_id] = result
        examples.extend({"artifact": logical_id, **example} for example in result.mismatch_examples)
    mismatch_count = (1 if missing else 0) + sum(result.mismatch_count for result in results.values())
    return EquivalenceReport(
        status="PASS" if mismatch_count == 0 else "FAIL",
        mismatch_count=mismatch_count,
        artifact_results=results,
        mismatch_examples=tuple(examples[:20]),
    )
