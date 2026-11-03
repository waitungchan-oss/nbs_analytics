from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import pipeline
from pipeline import process_raw_files

BETA_SELLER_SCOPE_CACHE_VERSION = "beta-seller-scope-v3"
BETA_REQUIRED_SOURCE_ROLES = ("main", "tour", "transport", "ticket", "package", "hotel")
_CACHE_FILENAME = "beta_seller_scope_v3.pkl"
_LEGACY_CACHE_VERSION = "beta-seller-scope-v2"
_LEGACY_CACHE_FILENAME = "beta_seller_scope_v2.pkl"
_PAYMENT_DETAIL_IDENTITY_FIELDS = (
    "來源單據號", "收款單號", "收款流水號", "收款時間", "統一日期",
    "收款類型", "收款方式", "原幣幣種", "銷售點", "銷售員",
    "團代號", "團名稱", "交易時間", "來源報表標籤",
)
_BETA_EXCLUDED_RECEIPT_TYPES = ("掛賬核銷",)
_BETA_EXCLUDED_PAYMENT_METHODS = ("TT 退款轉團款",)
BETA_CLOSED_MONTH_POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "beta_closed_month_policy.json"
_UNSET = object()
_BETA_FROZEN_MONTH = "2026-10"
_BETA_FROZEN_AMOUNT_HKD = 3_239_226
_BETA_PRESERVED_RECEIPT = ("SK2610004589", 3_000)
_BETA_FROZEN_SOURCE_SHA256 = "acc3d3b8945a84818754aa3b9b4ca2c686f94e20e6f907b0a437c6e27d3fc635"
_BETA_REGISTER_SOURCE_SHA256 = "c4316395fdcf09f9340da83d6608a05d40288d44be57fc4602de5b6b2e84db6f"


def _adjustment_source_row_fingerprint(entry: dict) -> str:
    fields = (
        "receiptNo", "sourceOrderNo", "salesperson", "paymentDate", "amountHkd",
        "receiptType", "paymentMethod", "reason", "relatedExcludedReceiptNos",
    )
    payload = {key: entry.get(key) for key in fields}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _entity_audit_reconciled(coverage: dict) -> bool:
    """Verify the main-row and secondary-ID audit without requiring zero orphans."""
    required = (
        "主表有效來源單據號數", "副表有效交易號碼數", "成功匹配唯一單號數",
        "成功匹配主表行數", "主表未匹配行數", "副表未落主表單號數",
    )
    values = {}
    for row in coverage.get("entityAuditSummary") or []:
        if not isinstance(row, dict) or row.get("指標") not in required:
            continue
        name = row["指標"]
        if name in values:
            return False
        try:
            number = float(row.get("數值"))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            return False
        values[name] = int(number)
    if set(values) != set(required):
        return False
    try:
        raw_input_rows = float(coverage.get("audit", {}).get("inputRows"))
    except (AttributeError, TypeError, ValueError):
        return False
    if not math.isfinite(raw_input_rows) or raw_input_rows < 0 or not raw_input_rows.is_integer():
        return False
    input_rows = int(raw_input_rows)
    main_unique, secondary_unique, matched_unique, matched_rows, main_unmatched, secondary_only = (
        values[name] for name in required
    )
    return (
        matched_unique <= main_unique
        and matched_unique <= secondary_unique
        and matched_unique <= matched_rows
        and main_unique - matched_unique <= main_unmatched
        and matched_rows + main_unmatched == input_rows
        and secondary_unique - matched_unique == secondary_only
    )


def _default_cache_path() -> Path:
    cache_root = Path(os.environ.get("NBS_ANALYTICS_CACHE_DIR", ".nbs_runtime_cache"))
    return cache_root / _CACHE_FILENAME


def _rewind(value) -> None:
    if value is not None and hasattr(value, "seek"):
        value.seek(0)


def _source_content(source) -> tuple[str, bytes]:
    if source is None:
        return "missing", b""
    if isinstance(source, tuple) and len(source) == 2:
        label, content = source
        if isinstance(content, pd.DataFrame):
            encoded = content.to_json(
                orient="split", date_format="iso", force_ascii=False, default_handler=str
            ).encode("utf-8")
            return str(label), encoded
        nested_label, encoded = _source_content(content)
        return f"{label}:{nested_label}", encoded
    if isinstance(source, pd.DataFrame):
        encoded = source.to_json(
            orient="split", date_format="iso", force_ascii=False, default_handler=str
        ).encode("utf-8")
        return str(getattr(source, "name", "dataframe")), encoded
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_file():
            return path.name, path.read_bytes()
        return str(source), str(source).encode("utf-8")
    if hasattr(source, "getvalue"):
        return str(getattr(source, "name", "upload")), bytes(source.getvalue())
    if hasattr(source, "read"):
        position = source.tell() if hasattr(source, "tell") else 0
        try:
            source.seek(0)
            payload = source.read()
        finally:
            source.seek(position)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return str(getattr(source, "name", "upload")), bytes(payload)
    return type(source).__name__, repr(source).encode("utf-8")


def _filename_source_role(name: str) -> str | None:
    label = Path(name).stem.casefold()
    matches = []
    if "交通" in label or "transport" in label:
        matches.append("transport")
    if any(token in label for token in ("門券", "票務", "ticket")):
        matches.append("ticket")
    if "套票" in label or "package" in label:
        matches.append("package")
    if "酒店" in label or "hotel" in label:
        matches.append("hotel")
    return matches[0] if len(matches) == 1 else None


def _classify_source_role(role: str, name: str, declared_role: str | None = None) -> str | None:
    if role == "main":
        return "main" if name != "missing" else None
    if role == "tour":
        return "tour" if name != "missing" else None
    if not role.startswith("other-") or declared_role not in {
        "transport", "ticket", "package", "hotel",
    }:
        return None
    filename_role = _filename_source_role(name)
    if filename_role is not None and filename_role != declared_role:
        return None
    return declared_role


def _source_fingerprint(main_file, tour_file, other_files) -> tuple[str, list[dict]]:
    records = []
    sources = [("main", main_file), ("tour", tour_file)]
    sources.extend((f"other-{index}", source) for index, source in enumerate(other_files or []))
    for role, source in sources:
        name, content = _source_content(source)
        role_source = source[1] if isinstance(source, tuple) and len(source) == 2 else source
        declared_role = getattr(role_source, "beta_source_role", None)
        records.append(
            {
                "role": role,
                "sourceCategory": _classify_source_role(role, name, declared_role),
                "name": name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    records.sort(key=lambda item: (item["role"], item["name"], item["sha256"]))
    canonical = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), records


def _date_intervals(frames: list[pd.DataFrame]) -> list[dict[str, str]]:
    observed: set[date] = set()
    for frame in frames:
        column = next(
            (name for name in ("收款時間", "統一日期", pipeline.COL_DATE) if name in frame.columns),
            None,
        )
        if column is None:
            continue
        parsed = pd.to_datetime(frame[column], errors="coerce").dropna()
        observed.update(value.date() for value in parsed)
    if not observed:
        return []
    ordered = sorted(observed)
    intervals: list[dict[str, str]] = []
    start = previous = ordered[0]
    for current in ordered[1:]:
        if current != previous + timedelta(days=1):
            intervals.append({"start": start.isoformat(), "end": previous.isoformat()})
            start = current
        previous = current
    intervals.append({"start": start.isoformat(), "end": previous.isoformat()})
    return intervals


def _uncovered_date_intervals(required, covered) -> list[dict[str, str]]:
    required_dates: set[date] = set()
    covered_dates: set[date] = set()
    for intervals, destination in ((required or [], required_dates), (covered or [], covered_dates)):
        for interval in intervals:
            try:
                current = date.fromisoformat(str(interval["start"]))
                end = date.fromisoformat(str(interval["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            while current <= end:
                destination.add(current)
                current += timedelta(days=1)
    missing = sorted(required_dates - covered_dates)
    if not missing:
        return []
    result: list[dict[str, str]] = []
    start = previous = missing[0]
    for current in missing[1:]:
        if current != previous + timedelta(days=1):
            result.append({"start": start.isoformat(), "end": previous.isoformat()})
            start = current
        previous = current
    result.append({"start": start.isoformat(), "end": previous.isoformat()})
    return result


def _seller_audit(frames: list[pd.DataFrame], included_rows: int) -> dict[str, int]:
    total = blank = unknown = 0
    for frame in frames:
        total += len(frame)
        if pipeline.COL_SALESPERSON not in frame.columns:
            blank += len(frame)
            continue
        normalized = frame[pipeline.COL_SALESPERSON].map(pipeline._normalize_beta_salesperson)
        blank += int(normalized.eq("未指定").sum())
        unknown += int(
            (
                normalized.ne("未指定")
                & ~normalized.map(
                    lambda value: pipeline._canonical_beta_salesperson(value)
                ).notna()
            ).sum()
        )
    return {
        "inputRows": total,
        "includedRows": included_rows,
        "excludedRows": total - included_rows,
        "blankSalespersonRows": blank,
        "unknownSalespersonRows": unknown,
    }


def build_beta_seller_scope_frames(
    main_file,
    tour_file,
    other_files,
    branch_mapping,
    exclude_prefixes,
    sales_rep_list,
    *,
    process_runner=None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Capture rows using main-table seller identity and Beta-specific approved sellers."""
    _rewind(main_file)
    _rewind(tour_file)
    for item in other_files or []:
        _rewind(item)
    source_fingerprint, source_files = _source_fingerprint(main_file, tour_file, other_files)
    unclassified = [record for record in source_files if record.get("sourceCategory") is None]
    secondary_roles = [
        str(record["sourceCategory"])
        for record in source_files
        if str(record.get("role", "")).startswith("other-") and record.get("sourceCategory")
    ]
    source_roles = sorted(
        {str(record["sourceCategory"]) for record in source_files if record.get("sourceCategory")}
    )
    missing_source_roles = sorted(set(BETA_REQUIRED_SOURCE_ROLES) - set(source_roles))
    duplicate_roles = sorted(
        role for role in set(secondary_roles) if secondary_roles.count(role) > 1
    )
    mapping_issues = [
        f"unclassified:{record['role']}:{record['name']}" for record in unclassified
    ] + [f"duplicate:{role}" for role in duplicate_roles] + [
        f"missing_required:{role}" for role in missing_source_roles
    ]
    source_role_mapping_verified = not mapping_issues
    runner = process_runner or process_raw_files
    if source_role_mapping_verified:
        processed = runner(
            main_file,
            tour_file,
            other_files or [],
            branch_mapping,
            exclude_prefixes,
            sales_rep_list,
            return_entity_audit=True,
            beta_capture_mode=True,
        )
        raw_tour, raw_others = processed[:2]
        entity_audit = processed[3] if len(processed) > 3 and isinstance(processed[3], dict) else {}
    else:
        raw_tour, raw_others, entity_audit = pd.DataFrame(), pd.DataFrame(), {}
    beta_tour, beta_others = pipeline._select_beta_salesperson_frames(raw_tour, raw_others)
    included_rows = len(beta_tour) + len(beta_others)
    audit = _seller_audit([raw_tour, raw_others], included_rows)
    dates = _date_intervals([raw_tour, raw_others]) if source_role_mapping_verified else []
    entity_summary = entity_audit.get("summary")
    entity_summary_rows = (
        entity_summary.to_dict(orient="records")
        if isinstance(entity_summary, pd.DataFrame)
        else list(entity_summary or [])
    )
    if not source_role_mapping_verified:
        beta_tour, beta_others = pd.DataFrame(), pd.DataFrame()
    return beta_tour, beta_others, {
        "version": BETA_SELLER_SCOPE_CACHE_VERSION,
        "sourceFingerprint": source_fingerprint,
        "sourceFiles": source_files,
        "sourceRoles": source_roles,
        "requiredSourceRoles": list(BETA_REQUIRED_SOURCE_ROLES),
        "missingSourceRoles": missing_source_roles,
        "sourceRoleMappingVerified": bool(source_role_mapping_verified),
        "sourceRoleMappingIssues": mapping_issues,
        "dateIntervals": dates,
        "sourceStart": dates[0]["start"] if dates else None,
        "sourceEnd": dates[-1]["end"] if dates else None,
        "complete": False,
        "status": "unverified",
        "audit": audit,
        "entityAuditSummary": entity_summary_rows,
    }


def _key_value(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _row_keys(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(index=frame.index, dtype="string")
    identity = pd.DataFrame(index=frame.index)
    for column in _PAYMENT_DETAIL_IDENTITY_FIELDS:
        values = frame[column] if column in frame.columns else pd.Series("", index=frame.index)
        identity[column] = values.map(_key_value)
    receipt_ids = identity.get("收款單號", pd.Series("", index=frame.index)).ne("")
    serial_ids = identity.get("收款流水號", pd.Series("", index=frame.index)).ne("")
    has_receipt_identity = receipt_ids | serial_ids
    for column in ("收款原幣金額", "收款本幣金額"):
        values = frame[column] if column in frame.columns else pd.Series("", index=frame.index)
        identity[column] = values.map(_key_value).where(~has_receipt_identity, "")
    tokens = identity.apply(
        lambda row: hashlib.sha256(
            json.dumps(row.tolist(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        axis=1,
    )
    no_identity = identity.eq("").all(axis=1)
    if no_identity.any():
        fallback = frame.loc[no_identity].apply(
            lambda row: hashlib.sha256(
                json.dumps(
                    [_key_value(value) for value in row.tolist()],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            axis=1,
        )
        tokens.loc[no_identity] = fallback
    occurrences = tokens.groupby(tokens, sort=False).cumcount().astype(str)
    return (tokens + ":" + occurrences).astype("string")


def _frames_fingerprint(tour: pd.DataFrame, others: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    for label, frame in (("tour", tour), ("others", others)):
        digest.update(label.encode("utf-8"))
        payload = frame.to_json(
            orient="split", date_format="iso", force_ascii=False, default_handler=str
        ).encode("utf-8")
        digest.update(payload)
    return digest.hexdigest()


def _merge_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return incoming.copy().reset_index(drop=True)
    if incoming.empty:
        return existing.copy().reset_index(drop=True)
    existing_keys = _row_keys(existing).reset_index(drop=True)
    incoming_keys = _row_keys(incoming).reset_index(drop=True)
    merged = pd.concat([existing, incoming], ignore_index=True, sort=False)
    keys = pd.concat([existing_keys, incoming_keys], ignore_index=True)
    return (
        merged.assign(_beta_seller_scope_key=keys)
        .drop_duplicates(subset=["_beta_seller_scope_key"], keep="last")
        .drop(columns=["_beta_seller_scope_key"])
        .reset_index(drop=True)
    )


def _filter_beta_rowwise_exclusions(
    frame: pd.DataFrame,
    excluded_receipt_types,
    excluded_payment_methods,
) -> pd.DataFrame:
    """Apply official Beta exclusions to each payment row without order-ID cascades."""
    excluded_types = {
        str(value).strip()
        for value in excluded_receipt_types
        if str(value).strip()
    }
    excluded_methods = {
        str(value).strip()
        for value in excluded_payment_methods
        if str(value).strip()
    }
    if frame.empty:
        return frame.copy()
    excluded = pd.Series(False, index=frame.index)
    if excluded_types and "收款類型" in frame.columns:
        excluded |= frame["收款類型"].fillna("").astype(str).str.strip().isin(excluded_types)
    if excluded_methods and "收款方式" in frame.columns:
        excluded |= frame["收款方式"].fillna("").astype(str).str.strip().isin(excluded_methods)
    return frame.loc[~excluded].copy()


def _load_beta_closed_month_policy() -> dict:
    try:
        policy = json.loads(BETA_CLOSED_MONTH_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Beta closed-month policy unavailable or invalid: {exc}") from exc
    if not isinstance(policy, dict) or policy.get("schemaVersion") != "beta-closed-month-policy-v1":
        raise ValueError("Beta closed-month policy schema is invalid")
    baseline = policy.get("frozenBaseline")
    register = policy.get("adjustmentRegister")
    entries = register.get("entries") if isinstance(register, dict) else None
    if (
        policy.get("scope") != "beta_market_ecommerce_peer_export_only"
        or not isinstance(baseline, dict)
        or not isinstance(register, dict)
        or not isinstance(entries, list)
        or not entries
    ):
        raise ValueError("Beta closed-month policy is incomplete")
    if (
        policy.get("frozenMonth") != _BETA_FROZEN_MONTH
        or policy.get("currency") != "HKD"
        or baseline.get("amountHkd") != _BETA_FROZEN_AMOUNT_HKD
        or baseline.get("sourceSha256") != _BETA_FROZEN_SOURCE_SHA256
        or baseline.get("sourceFile") != "Beta_市場電商及同業_不含掛賬核銷與TT退款轉團款 (7).xlsx"
        or register.get("sourceSha256") != _BETA_REGISTER_SOURCE_SHA256
        or register.get("sourceFile") != "財務收款總數-0101-1102.xlsx"
    ):
        raise ValueError("Beta closed-month frozen references do not match the approved October baseline")
    preserved = baseline.get("preservedReceipts")
    if (
        not isinstance(preserved, list)
        or len(preserved) != 1
        or preserved[0].get("receiptNo") != _BETA_PRESERVED_RECEIPT[0]
        or preserved[0].get("amountHkd") != _BETA_PRESERVED_RECEIPT[1]
    ):
        raise ValueError("Beta closed-month preserved receipt does not match the approved reference")
    receipt_ids = []
    total = 0.0
    required_fields = (
        "receiptNo", "sourceOrderNo", "salesperson", "paymentDate", "amountHkd", "reason",
        "receiptType", "paymentMethod", "relatedExcludedReceiptNos", "sourceRowSha256",
    )
    for entry in entries:
        if not isinstance(entry, dict) or any(
            key not in entry or not str(entry.get(key) or "").strip() for key in required_fields
        ):
            raise ValueError("Beta closed-month adjustment entry is missing required lineage")
        if (
            not isinstance(entry["relatedExcludedReceiptNos"], list)
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry["sourceRowSha256"]))
            or entry["sourceRowSha256"] != _adjustment_source_row_fingerprint(entry)
        ):
            raise ValueError("Beta closed-month adjustment source fingerprint is invalid")
        receipt_ids.append(str(entry["receiptNo"]).strip())
        try:
            total += float(entry["amountHkd"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Beta closed-month adjustment amount is invalid") from exc
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("Beta closed-month adjustment receipt numbers must be unique")
    if len(entries) != int(register.get("expectedReceiptCount", -1)):
        raise ValueError("Beta closed-month adjustment receipt count does not match its register")
    if round(total, 2) != round(float(register.get("expectedAdjustmentAmountHkd", -1)), 2):
        raise ValueError("Beta closed-month adjustment total does not match its register")
    return policy


def beta_closed_month_policy_fingerprint() -> str:
    return hashlib.sha256(BETA_CLOSED_MONTH_POLICY_PATH.read_bytes()).hexdigest()


def _date_values(frame: pd.DataFrame) -> pd.Series:
    values = pd.to_datetime(frame.get("統一日期", pd.Series(index=frame.index)), errors="coerce")
    if "收款時間" in frame.columns:
        values = values.fillna(pd.to_datetime(frame["收款時間"], errors="coerce"))
    return values


def _apply_beta_closed_month_adjustments(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    policy = _load_beta_closed_month_policy()
    register = policy["adjustmentRegister"]
    entries = {str(item["receiptNo"]).strip(): item for item in register["entries"]}
    if frame.empty or "收款單號" not in frame.columns:
        return frame.copy(), {"adjustmentAmount": 0.0, "adjustmentCount": 0, "matchedReceiptNos": []}

    result = frame.copy()
    receipt_ids = result["收款單號"].fillna("").astype(str).str.strip()
    dates = _date_values(result)
    frozen_month = str(policy["frozenMonth"])
    target_rows = receipt_ids.isin(entries) & dates.dt.strftime("%Y-%m").eq(frozen_month)
    matched = result.loc[target_rows].copy()
    if matched.empty:
        return result, {"adjustmentAmount": 0.0, "adjustmentCount": 0, "matchedReceiptNos": []}

    if matched["收款單號"].fillna("").astype(str).str.strip().duplicated().any():
        raise ValueError("Beta closed-month adjustment receipt is duplicated in seller-scope rows")
    amounts = pd.to_numeric(matched.get("收款本幣金額"), errors="coerce")
    if amounts.isna().any():
        raise ValueError("Beta closed-month adjustment row has no valid HKD amount")
    for index, row in matched.iterrows():
        receipt_no = str(row.get("收款單號") or "").strip()
        entry = entries[receipt_no]
        row_date = dates.loc[index]
        expected_date = str(entry["paymentDate"])
        checks = (
            str(row.get("來源單據號") or "").strip() == str(entry["sourceOrderNo"]).strip(),
            str(row.get("銷售員") or "").strip() == str(entry["salesperson"]).strip(),
            row_date.strftime("%Y-%m-%d") == expected_date,
            abs(float(amounts.loc[index]) - float(entry["amountHkd"])) <= 0.01,
            str(row.get("收款類型") or "").strip() == str(entry["receiptType"]).strip(),
            str(row.get("收款方式") or "").strip() == str(entry["paymentMethod"]).strip(),
        )
        if not all(checks):
            raise ValueError(f"Beta closed-month adjustment source evidence mismatch for {receipt_no}")

    matched_ids = receipt_ids.loc[target_rows]
    result = result.loc[~target_rows].copy()
    return result, {
        "adjustmentAmount": -round(float(amounts.sum()), 2),
        "adjustmentCount": int(len(matched)),
        "matchedReceiptNos": sorted(matched_ids.tolist()),
    }


def filter_beta_seller_scope_rows(
    tour: pd.DataFrame,
    others: pd.DataFrame,
    excluded_receipt_types=None,
    excluded_payment_methods=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply row-wise exclusions and the reversible October receipt register."""
    excluded_types = _BETA_EXCLUDED_RECEIPT_TYPES if excluded_receipt_types is None else excluded_receipt_types
    excluded_methods = _BETA_EXCLUDED_PAYMENT_METHODS if excluded_payment_methods is None else excluded_payment_methods
    rowwise_tour = _filter_beta_rowwise_exclusions(tour, excluded_types, excluded_methods)
    rowwise_others = _filter_beta_rowwise_exclusions(others, excluded_types, excluded_methods)
    adjusted_tour, _ = _apply_beta_closed_month_adjustments(rowwise_tour)
    adjusted_others, _ = _apply_beta_closed_month_adjustments(rowwise_others)
    return adjusted_tour, adjusted_others


def _monthly_beta_amount_breakdown(tour: pd.DataFrame, others: pd.DataFrame) -> dict:
    frames = [frame for frame in (tour, others) if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not frames:
        return {"raw": {}, "adjusted": {}, "adjustments": {}, "latestMonth": None}
    combined = pd.concat(frames, ignore_index=True, sort=False)
    raw_rows = _filter_beta_rowwise_exclusions(
        combined, _BETA_EXCLUDED_RECEIPT_TYPES, _BETA_EXCLUDED_PAYMENT_METHODS
    )
    adjusted_rows, adjustment_info = _apply_beta_closed_month_adjustments(raw_rows)
    date_values = _date_values(raw_rows)
    valid_dates = date_values.dropna()
    latest_month = valid_dates.max().strftime("%Y-%m") if not valid_dates.empty else None
    if "收款本幣金額" not in raw_rows.columns:
        return {"raw": {}, "adjusted": {}, "adjustments": {}, "latestMonth": latest_month}
    raw_amounts = pd.to_numeric(raw_rows["收款本幣金額"], errors="coerce").fillna(0)
    adjusted_dates = _date_values(adjusted_rows)
    adjusted_amounts = pd.to_numeric(adjusted_rows["收款本幣金額"], errors="coerce").fillna(0)
    raw_monthly = raw_amounts.groupby(date_values.dt.strftime("%Y-%m")).sum()
    adjusted_monthly = adjusted_amounts.groupby(adjusted_dates.dt.strftime("%Y-%m")).sum()
    policy = _load_beta_closed_month_policy()
    frozen_month = policy["frozenMonth"]
    preserved_receipts = policy["frozenBaseline"].get("preservedReceipts") or []
    preserved_ids = {str(item.get("receiptNo") or "").strip() for item in preserved_receipts}
    row_receipts = raw_rows.get("收款單號", pd.Series(index=raw_rows.index)).fillna("").astype(str).str.strip()
    preserved_amount = raw_amounts.loc[
        row_receipts.isin(preserved_ids) & date_values.dt.strftime("%Y-%m").eq(frozen_month)
    ].sum()
    adjustments = {
        frozen_month: {
            **adjustment_info,
            "preservedReceiptAmount": round(float(preserved_amount), 2),
        }
    }
    return {
        "raw": {str(month): float(value) for month, value in raw_monthly.items() if month and month != "NaT"},
        "adjusted": {str(month): float(value) for month, value in adjusted_monthly.items() if month and month != "NaT"},
        "adjustments": adjustments,
        "latestMonth": latest_month,
    }


def _monthly_beta_official_amounts(tour: pd.DataFrame, others: pd.DataFrame) -> tuple[dict[str, float], str | None]:
    breakdown = _monthly_beta_amount_breakdown(tour, others)
    return breakdown["adjusted"], breakdown["latestMonth"]


def _closed_month_variance_check(
    previous_tour: pd.DataFrame,
    previous_others: pd.DataFrame,
    current_tour: pd.DataFrame,
    current_others: pd.DataFrame,
    *,
    coverage_complete: bool,
    latest_source_month: str | None = None,
) -> dict:
    if not coverage_complete:
        return {"status": "not_checked", "reason": "coverage_incomplete", "months": []}
    previous_breakdown = _monthly_beta_amount_breakdown(previous_tour, previous_others)
    current_breakdown = _monthly_beta_amount_breakdown(current_tour, current_others)
    previous = previous_breakdown["adjusted"]
    current = current_breakdown["adjusted"]
    latest_row_month = current_breakdown["latestMonth"]
    latest_month = latest_source_month or latest_row_month
    if latest_month is None:
        return {"status": "not_checked", "reason": "missing_payment_dates", "months": []}
    closed_months = sorted(month for month in set(previous) | set(current) if month < latest_month)
    if not closed_months:
        return {"status": "no_closed_months", "months": []}
    rows = []
    has_previous = False
    has_missing_previous = False
    has_variance = False
    for month in closed_months:
        snapshot_amount = previous.get(month)
        frozen_baseline = None
        if month == _load_beta_closed_month_policy()["frozenMonth"]:
            frozen_baseline = float(_load_beta_closed_month_policy()["frozenBaseline"]["amountHkd"])
        old_amount = frozen_baseline if frozen_baseline is not None else snapshot_amount
        new_amount = current.get(month, 0.0)
        if old_amount is None:
            has_missing_previous = True
            delta = None
        else:
            has_previous = True
            delta = round(new_amount - old_amount, 2)
            has_variance |= abs(delta) > 0.01
        row = {
                "month": month,
                "previousAmount": round(old_amount, 2) if old_amount is not None else None,
                "currentAmount": round(new_amount, 2),
                "deltaAmount": delta,
            }
        if frozen_baseline is not None:
            adjustment = current_breakdown["adjustments"].get(month, {})
            row.update({
                "frozenBaselineAmount": round(frozen_baseline, 2),
                "previousSnapshotAmount": round(snapshot_amount, 2) if snapshot_amount is not None else None,
                "rawCurrentAmount": round(current_breakdown["raw"].get(month, 0.0), 2),
                "adjustmentAmount": round(float(adjustment.get("adjustmentAmount", 0.0)), 2),
                "adjustmentCount": int(adjustment.get("adjustmentCount", 0)),
                "preservedReceiptAmount": round(float(adjustment.get("preservedReceiptAmount", 0.0)), 2),
            })
        rows.append(row)
    status = (
        "variance" if has_variance else
        "partial_snapshot" if has_missing_previous and has_previous else
        "no_previous_snapshot" if not has_previous else
        "matched"
    )
    return {"status": status, "latestSourceMonth": latest_month, "months": rows}


def _merge_intervals(*interval_groups) -> list[dict[str, str]]:
    ranges: list[tuple[date, date]] = []
    for group in interval_groups:
        for item in group or []:
            try:
                start = date.fromisoformat(str(item["start"]))
                end = date.fromisoformat(str(item["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            if start <= end:
                ranges.append((start, end))
    if not ranges:
        return []
    ranges.sort()
    merged: list[list[date]] = []
    for start, end in ranges:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [{"start": start.isoformat(), "end": end.isoformat()} for start, end in merged]


def _merge_coverage(existing: dict, incoming: dict) -> dict:
    old = existing if isinstance(existing, dict) else {}
    new = incoming if isinstance(incoming, dict) else {}
    fingerprints = []
    for value in [*(old.get("sourceFingerprints") or []), old.get("sourceFingerprint"), *(new.get("sourceFingerprints") or []), new.get("sourceFingerprint")]:
        if isinstance(value, str) and value and value not in fingerprints:
            fingerprints.append(value)
    intervals = _merge_intervals(old.get("dateIntervals"), new.get("dateIntervals"))
    required = _merge_intervals(old.get("requiredDateIntervals"), new.get("requiredDateIntervals"))
    missing = _uncovered_date_intervals(required, intervals)
    source_roles = sorted(set(new.get("sourceRoles") or []))
    required_source_roles = list(BETA_REQUIRED_SOURCE_ROLES)
    missing_source_roles = sorted(set(required_source_roles) - set(source_roles))
    source_mapping_verified = (
        new.get("sourceRoleMappingVerified") is True
        and not missing_source_roles
        and not list(new.get("sourceRoleMappingIssues") or [])
    )
    same_source = bool(
        new.get("sourceFingerprint")
        and new.get("sourceFingerprint") == old.get("sourceFingerprint")
    )
    new_verification = new.get("verification")
    if source_mapping_verified and isinstance(new_verification, dict) and new_verification:
        verification = dict(new_verification)
    elif source_mapping_verified and same_source:
        old_verification = old.get("verification")
        verification = dict(old_verification) if isinstance(old_verification, dict) else {}
    else:
        verification = {}
    verification["sourceRoleMappingStatus"] = "verified" if source_mapping_verified else "unverified"
    verification["sourceRolesStatus"] = (
        "complete" if source_mapping_verified and not missing_source_roles else "incomplete"
    )
    verification_complete = (
        verification.get("preflightStatus") == "matched"
        and verification.get("driftStatus") in {"no_drift", "matched"}
        and verification.get("entityMatchStatus") == "matched"
        and verification.get("sourceRolesStatus") == "complete"
        and verification.get("sourceRoleMappingStatus") == "verified"
        and verification.get("liveDbUnchanged") is True
    )
    complete = (
        bool(required)
        and not missing
        and not missing_source_roles
        and source_mapping_verified
        and verification_complete
    )
    status = "complete" if complete else ("partial" if required else "unverified")
    return {
        **old,
        **new,
        "version": BETA_SELLER_SCOPE_CACHE_VERSION,
        "sourceFingerprint": new.get("sourceFingerprint") or old.get("sourceFingerprint"),
        "sourceFingerprints": fingerprints,
        "dateIntervals": intervals,
        "requiredDateIntervals": required,
        "missingDateIntervals": missing,
        "sourceRoles": source_roles,
        "requiredSourceRoles": required_source_roles,
        "missingSourceRoles": missing_source_roles,
        "sourceRoleMappingVerified": source_mapping_verified,
        "sourceRoleMappingIssues": list(new.get("sourceRoleMappingIssues") or []),
        "verification": verification,
        "complete": complete,
        "betaExportReady": complete,
        "status": status,
    }


def _load_beta_seller_scope_frames(
    path: Path, *, include_incomplete: bool, expected_data_generation=_UNSET
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if not path.exists():
        return pd.DataFrame(), pd.DataFrame(), {}
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict) or payload.get("version") != BETA_SELLER_SCOPE_CACHE_VERSION:
            return pd.DataFrame(), pd.DataFrame(), {}
        tour = payload.get("tour")
        others = payload.get("others")
        coverage = payload.get("coverage")
        if not isinstance(tour, pd.DataFrame) or not isinstance(others, pd.DataFrame) or not isinstance(coverage, dict):
            return pd.DataFrame(), pd.DataFrame(), {}
        coverage = dict(coverage)
        coverage["version"] = BETA_SELLER_SCOPE_CACHE_VERSION
        coverage["dataGeneration"] = payload.get("dataGeneration")
        coverage["rowsFingerprintMatched"] = payload.get("rowsFingerprint") == _frames_fingerprint(tour, others)
        if not coverage["rowsFingerprintMatched"]:
            coverage["complete"] = False
            coverage["status"] = "invalid_cache"
            return pd.DataFrame(), pd.DataFrame(), coverage
        if expected_data_generation is not _UNSET:
            try:
                generation_matches = (
                    expected_data_generation is not None
                    and coverage.get("dataGeneration") is not None
                    and int(expected_data_generation) == int(coverage["dataGeneration"])
                )
            except (TypeError, ValueError):
                generation_matches = False
            if not generation_matches:
                coverage["complete"] = False
                coverage["status"] = "stale_generation"
                return pd.DataFrame(), pd.DataFrame(), coverage
        if (
            not include_incomplete
            and expected_data_generation is not _UNSET
            and not coverage.get("complete")
            and isinstance(coverage.get("verification"), dict)
            and coverage["verification"].get("entityMatchStatus") == "unmatched_or_unverified"
            and _entity_audit_reconciled(coverage)
        ):
            revised = dict(coverage)
            revised["verification"] = {
                **coverage["verification"], "entityMatchStatus": "matched",
                "betaExportReady": True,
            }
            promoted = _merge_coverage({}, revised)
            if promoted.get("complete"):
                coverage = promoted
        if not include_incomplete and not coverage.get("complete"):
            return pd.DataFrame(), pd.DataFrame(), coverage
        return tour, others, coverage
    except Exception:
        return pd.DataFrame(), pd.DataFrame(), {}


def load_beta_seller_scope_frames(
    *, cache_path: str | Path | None = None, expected_data_generation=_UNSET
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    return _load_beta_seller_scope_frames(
        path, include_incomplete=False, expected_data_generation=expected_data_generation
    )


def _load_legacy_v2_beta_seller_scope_frames(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the prior cache only as a comparison baseline; never migrate its rows."""
    if path.name == _LEGACY_CACHE_FILENAME:
        return pd.DataFrame(), pd.DataFrame()
    legacy_path = path.with_name(_LEGACY_CACHE_FILENAME)
    if not legacy_path.exists():
        return pd.DataFrame(), pd.DataFrame()
    try:
        with legacy_path.open("rb") as handle:
            payload = pickle.load(handle)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _LEGACY_CACHE_VERSION
            or not isinstance(payload.get("tour"), pd.DataFrame)
            or not isinstance(payload.get("others"), pd.DataFrame)
        ):
            return pd.DataFrame(), pd.DataFrame()
        tour, others = payload["tour"], payload["others"]
        if payload.get("rowsFingerprint") != _frames_fingerprint(tour, others):
            return pd.DataFrame(), pd.DataFrame()
        return tour, others
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


def persist_beta_seller_scope_frames(
    tour: pd.DataFrame,
    others: pd.DataFrame,
    coverage: dict,
    *,
    cache_path: str | Path | None = None,
    data_generation: int | None = None,
) -> dict:
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    incoming_source_roles = set(coverage.get("sourceRoles") or []) if isinstance(coverage, dict) else set()
    incoming_roles_complete = (
        incoming_source_roles.issuperset(BETA_REQUIRED_SOURCE_ROLES)
        and isinstance(coverage, dict)
        and coverage.get("sourceRoleMappingVerified") is True
        and not list(coverage.get("sourceRoleMappingIssues") or [])
    )
    if not incoming_roles_complete:
        tour = pd.DataFrame(columns=tour.columns)
        others = pd.DataFrame(columns=others.columns)
    existing_tour, existing_others, existing_coverage = _load_beta_seller_scope_frames(
        path, include_incomplete=True
    )
    previous_check = existing_coverage.get("closedMonthVarianceCheck")
    previous_check_status = (
        previous_check.get("status") if isinstance(previous_check, dict) else None
    )
    has_completed_closed_month_check = previous_check_status in {
        "matched", "variance", "partial_snapshot", "no_previous_snapshot"
    }
    if path.exists() and has_completed_closed_month_check:
        previous_tour, previous_others = existing_tour, existing_others
    else:
        previous_tour, previous_others = _load_legacy_v2_beta_seller_scope_frames(path)
    merged_tour = _merge_frames(existing_tour, tour)
    merged_others = _merge_frames(existing_others, others)
    merged_coverage = _merge_coverage(existing_coverage, coverage)
    coverage_end_dates = []
    for interval in merged_coverage.get("dateIntervals") or []:
        try:
            coverage_end_dates.append(date.fromisoformat(str(interval["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    latest_source_month = (
        max(coverage_end_dates).strftime("%Y-%m") if coverage_end_dates else None
    )
    merged_coverage["closedMonthVarianceCheck"] = _closed_month_variance_check(
        previous_tour,
        previous_others,
        merged_tour,
        merged_others,
        coverage_complete=merged_coverage.get("complete") is True,
        latest_source_month=latest_source_month,
    )
    rows_fingerprint = _frames_fingerprint(merged_tour, merged_others)
    merged_coverage["dataGeneration"] = int(data_generation) if data_generation is not None else None
    merged_coverage["rowsFingerprintMatched"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        pickle.dump(
            {
                "version": BETA_SELLER_SCOPE_CACHE_VERSION,
                "tour": merged_tour,
                "others": merged_others,
                "coverage": merged_coverage,
                "dataGeneration": merged_coverage["dataGeneration"],
                "rowsFingerprint": rows_fingerprint,
            },
            handle,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    temporary.replace(path)
    return {
        "status": "saved",
        "tourRows": int(len(merged_tour)),
        "othersRows": int(len(merged_others)),
        "coverage": merged_coverage,
        "path": str(path),
    }


def clear_beta_seller_scope_cache(*, cache_path: str | Path | None = None) -> None:
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    path.unlink(missing_ok=True)
