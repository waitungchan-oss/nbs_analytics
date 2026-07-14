from __future__ import annotations

import hashlib
import json
import os
import pickle
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from database import load_all_data_from_db
from pipeline import build_dashboard_data_excluding_receipt_types
from backend.services.dashboard_analytics_service import build_analytics_from_facts
from backend.services.revenue_scope_service import build_revenue_scope_frames


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTS_CACHE_DIR = PROJECT_ROOT / ".nbs_runtime_cache"
FACTS_SERVICE_VERSION = "dashboard-facts-v1"
FACTS_CACHE_PREFIX = "dashboard_facts_"
DASHBOARD_READ_MODEL_VERSION = "dashboard-read-model-v1"
DASHBOARD_READ_MODEL_CACHE_PREFIX = "dashboard_read_model_"
DASHBOARD_READ_MODEL_REQUIRED_KEYS = {
    "status",
    "serviceVersion",
    "generationToken",
    "cacheKey",
    "factsCacheStatus",
    "revenueScope",
    "scopeAudit",
    "kpiTotals",
    "monthlyTotals",
    "branchRanking",
    "specialistRanking",
    "productTotals",
    "reconciliation",
}
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
_FACTS_CACHE_LOCKS: dict[str, Lock] = {}
_READ_MODEL_CACHE_LOCKS: dict[str, Lock] = {}
_CACHE_LOCKS_GUARD = Lock()


def _cache_lock(registry: dict[str, Lock], cache_key: str) -> Lock:
    with _CACHE_LOCKS_GUARD:
        return registry.setdefault(cache_key, Lock())


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
    with NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_model_cache_key(facts_cache_key: str) -> str:
    contract = {
        "serviceVersion": DASHBOARD_READ_MODEL_VERSION,
        "factsCacheKey": str(facts_cache_key),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_model_cache_path(cache_dir: str | Path | None, cache_key: str) -> Path:
    directory = Path(cache_dir) if cache_dir is not None else DEFAULT_FACTS_CACHE_DIR
    return directory / f"{DASHBOARD_READ_MODEL_CACHE_PREFIX}{cache_key}.json"


def _read_model_payload_checksum(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_read_model_cache(path: Path, cache_key: str, facts_cache_key: str) -> dict | None:
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(wrapper, dict):
        return None
    if (
        wrapper.get("serviceVersion") != DASHBOARD_READ_MODEL_VERSION
        or wrapper.get("cacheKey") != cache_key
        or wrapper.get("factsCacheKey") != facts_cache_key
        or not isinstance(wrapper.get("payload"), dict)
    ):
        return None
    payload = wrapper["payload"]
    if not DASHBOARD_READ_MODEL_REQUIRED_KEYS.issubset(payload):
        return None
    if payload.get("cacheKey") != facts_cache_key:
        return None
    if wrapper.get("payloadSha256") != _read_model_payload_checksum(payload):
        return None
    return payload


def _save_read_model_cache(path: Path, cache_key: str, facts_cache_key: str, payload: dict) -> None:
    wrapper = {
        "serviceVersion": DASHBOARD_READ_MODEL_VERSION,
        "cacheKey": cache_key,
        "factsCacheKey": facts_cache_key,
        "payload": payload,
        "payloadSha256": _read_model_payload_checksum(payload),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(wrapper, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
    with _cache_lock(_FACTS_CACHE_LOCKS, cache_key):
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


def build_dashboard_facts_read_model(
    *,
    db_path: str | Path,
    generation_token: str,
    branch_mapping: dict,
    target_branches_s3: list[str],
    cruise_depts: list[str],
    sales_rep_list: list[str],
    cache_dir: str | Path | None = None,
) -> dict:
    facts = build_dashboard_facts(
        db_path=db_path,
        generation_token=generation_token,
        branch_mapping=branch_mapping,
        target_branches_s3=target_branches_s3,
        cruise_depts=cruise_depts,
        sales_rep_list=sales_rep_list,
        cache_dir=cache_dir,
    )
    read_model_key = _read_model_cache_key(facts["cacheKey"])
    read_model_path = _read_model_cache_path(cache_dir, read_model_key)
    cached = _load_read_model_cache(read_model_path, read_model_key, facts["cacheKey"])
    if cached is not None:
        return {
            **cached,
            "factsCacheStatus": facts["factsCacheStatus"],
            "readModelCacheStatus": "hit",
        }
    with _cache_lock(_READ_MODEL_CACHE_LOCKS, read_model_key):
        cached = _load_read_model_cache(read_model_path, read_model_key, facts["cacheKey"])
        if cached is not None:
            return {
                **cached,
                "factsCacheStatus": facts["factsCacheStatus"],
                "readModelCacheStatus": "hit",
            }
        analytics = build_analytics_from_facts(
            facts["branchFacts"],
            facts["specialistFacts"],
            {"years": [], "months": [], "dateRange": [], "branch": "全部分社", "salesGroup": "全部銷售組"},
        )
        branch_revenue = sum(row["totalRevenue"] for row in analytics["branchRanking"])
        specialist_revenue = sum(row["totalRevenue"] for row in analytics["specialistRanking"])
        product_rows = []
        for product in ("旅行團", "郵輪", "票務"):
            revenue = sum(
                row["revenue"]
                for rows in analytics["productDrilldown"].values()
                for row in rows
                if row["product"] == product
            )
            product_rows.append({"product": product, "revenue": float(revenue)})
        total_revenue = sum(row["revenue"] for row in product_rows)
        for row in product_rows:
            row["sharePct"] = round(row["revenue"] / total_revenue * 100, 2) if total_revenue else 0.0

        payload = {
            "status": "ready",
            "serviceVersion": facts["serviceVersion"],
            "generationToken": facts["generationToken"],
            "cacheKey": facts["cacheKey"],
            "factsCacheStatus": facts["factsCacheStatus"],
            "revenueScope": facts["scopeAudit"]["scope_label"],
            "scopeAudit": facts["scopeAudit"],
            "kpiTotals": {
                "branchRevenue": float(branch_revenue),
                "specialistRevenue": float(specialist_revenue),
                "combinedRevenue": float(branch_revenue + specialist_revenue),
                "tourRevenue": product_rows[0]["revenue"],
                "cruiseRevenue": product_rows[1]["revenue"],
                "ticketRevenue": product_rows[2]["revenue"],
            },
            "monthlyTotals": analytics["monthlyTrend"],
            "branchRanking": analytics["branchRanking"],
            "specialistRanking": analytics["specialistRanking"],
            "productTotals": product_rows,
            "reconciliation": analytics["reconciliation"],
        }
        _save_read_model_cache(read_model_path, read_model_key, facts["cacheKey"], payload)
        return {**payload, "readModelCacheStatus": "rebuilt"}
