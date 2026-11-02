from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import pipeline
from pipeline import process_raw_files

BETA_SELLER_SCOPE_CACHE_VERSION = "beta-seller-scope-v2"
BETA_REQUIRED_SOURCE_ROLES = ("main", "tour", "transport", "ticket", "package", "hotel")
_CACHE_FILENAME = "beta_seller_scope_v2.pkl"
_UNSET = object()


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


def _row_keys(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(index=frame.index, dtype="string")
    if "收款單號" in frame.columns:
        receipt_ids = frame["收款單號"].fillna("").astype(str).str.strip()
    else:
        receipt_ids = pd.Series("", index=frame.index, dtype="string")
    fallback = pd.Series(
        pd.util.hash_pandas_object(frame, index=False).astype(str).to_numpy(),
        index=frame.index,
        dtype="string",
    )
    keys = fallback.map(lambda value: f"row:{value}")
    has_receipt = receipt_ids.ne("")
    keys.loc[has_receipt] = "receipt:" + receipt_ids.loc[has_receipt]
    return keys


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
    merged = pd.concat([existing, incoming], ignore_index=True, sort=False)
    keys = _row_keys(merged)
    return (
        merged.assign(_beta_seller_scope_key=keys)
        .drop_duplicates(subset=["_beta_seller_scope_key"], keep="last")
        .drop(columns=["_beta_seller_scope_key"])
        .reset_index(drop=True)
    )


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
    merged_tour = _merge_frames(existing_tour, tour)
    merged_others = _merge_frames(existing_others, others)
    merged_coverage = _merge_coverage(existing_coverage, coverage)
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
