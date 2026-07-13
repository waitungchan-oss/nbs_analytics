from __future__ import annotations

import hashlib
import json
import os
import pickle
from pathlib import Path

from database import load_all_data_from_db
from pipeline import build_dashboard_data_excluding_receipt_types
from backend.services.revenue_scope_service import build_revenue_scope_frames


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTS_CACHE_DIR = PROJECT_ROOT / ".nbs_runtime_cache"
FACTS_SERVICE_VERSION = "dashboard-facts-v1"
FACTS_CACHE_PREFIX = "dashboard_facts_"
REQUIRED_PAYLOAD_KEYS = {
    "serviceVersion",
    "cacheKey",
    "generationToken",
    "dbPath",
    "scopeAudit",
    "rawTour",
    "rawOthers",
    "analysisTour",
    "analysisOthers",
    "branchFacts",
    "specialistFacts",
}


def build_facts_cache_key(
    generation_token: str,
    branch_mapping: dict,
    target_branches_s3: list[str],
    cruise_depts: list[str],
    sales_rep_list: list[str],
) -> str:
    contract = {
        "serviceVersion": FACTS_SERVICE_VERSION,
        "generationToken": str(generation_token),
        "branchMapping": branch_mapping,
        "targetBranches": list(target_branches_s3),
        "cruiseDepartments": list(cruise_depts),
        "salesReps": list(sales_rep_list),
    }
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def facts_cache_path(cache_dir: str | Path | None, cache_key: str) -> Path:
    directory = Path(cache_dir) if cache_dir is not None else DEFAULT_FACTS_CACHE_DIR
    return directory / f"{FACTS_CACHE_PREFIX}{cache_key}.pkl"


def _validate_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or not REQUIRED_PAYLOAD_KEYS.issubset(payload):
        raise ValueError("Dashboard Facts cache payload contract is incomplete")
    return payload


def _load_cached_payload(path: Path, cache_key: str, generation_token: str) -> dict | None:
    try:
        payload = _validate_payload(pickle.loads(path.read_bytes()))
    except (FileNotFoundError, OSError, EOFError, pickle.PickleError, ValueError, TypeError):
        return None
    if payload.get("cacheKey") != cache_key or payload.get("generationToken") != str(generation_token):
        return None
    payload["factsCacheStatus"] = "hit"
    return payload


def _save_cached_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
    os.replace(temporary, path)


def build_dashboard_facts(
    *,
    db_path: str | Path,
    generation_token: str,
    branch_mapping: dict,
    target_branches_s3: list[str],
    cruise_depts: list[str],
    sales_rep_list: list[str],
    cache_dir: str | Path | None = None,
) -> dict:
    cache_key = build_facts_cache_key(
        generation_token,
        branch_mapping,
        target_branches_s3,
        cruise_depts,
        sales_rep_list,
    )
    cache_path = facts_cache_path(cache_dir, cache_key)
    cached = _load_cached_payload(cache_path, cache_key, generation_token)
    if cached is not None:
        return cached

    explicit_db_path = Path(db_path)
    raw_tour, raw_others = load_all_data_from_db(db_path=explicit_db_path)
    analysis_tour, analysis_others, scope_audit = build_revenue_scope_frames(raw_tour, raw_others)
    _, branch_facts, specialist_facts = build_dashboard_data_excluding_receipt_types(
        raw_tour,
        raw_others,
        branch_mapping,
        target_branches_s3,
        cruise_depts,
        sales_rep_list,
        ["掛賬核銷"],
        excluded_payment_methods=["TT 退款轉團款"],
        make_workbook=False,
    )
    payload = {
        "serviceVersion": FACTS_SERVICE_VERSION,
        "cacheKey": cache_key,
        "generationToken": str(generation_token),
        "dbPath": str(explicit_db_path),
        "scopeAudit": scope_audit,
        "rawTour": raw_tour,
        "rawOthers": raw_others,
        "analysisTour": analysis_tour,
        "analysisOthers": analysis_others,
        "branchFacts": branch_facts,
        "specialistFacts": specialist_facts,
        "factsCacheStatus": "rebuilt",
        "factsCachePath": str(cache_path),
    }
    _validate_payload(payload)
    _save_cached_payload(cache_path, payload)
    return payload
