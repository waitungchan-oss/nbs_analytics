from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


MANAGEMENT_SUMMARY_SCHEMA = "governance-graph-management-summary-v1"
MANAGEMENT_EXPORT_SCHEMA = "governance-graph-management-summary-export-v1"
MANAGEMENT_POLICY_VERSION = "e4-management-summary-v1"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+@#%=-]{0,127}$")
_STATUSES = {"available", "partial", "unknown", "missing", "unavailable", "stale", "blocked", "invalid"}
_SOURCE_STATUSES = {"available", "blocked", "unknown", "missing", "unavailable", "stale", "invalid"}
_SOURCE_KINDS = {"d1_query", "d2_comparison", "d3_risk_summary", "d4_change_impact", "e1_evidence_lineage", "e3_owner_dependency_catalog"}
_RISK_LEVELS = {"R2", "R1", "R0", "unknown"}
_COVERAGE = {"available", "partial", "unknown", "missing", "unavailable", "stale", "blocked", "invalid"}
_ATTENTION_CATEGORIES = {"protected_governance_surface", "verification_assurance", "implementation_governance", "workflow_observability_blocked", "coverage_gap", "evidence_coverage_gap", "catalog_coverage_gap"}
_D4_CATEGORIES = _ATTENTION_CATEGORIES | {"coverage_unknown", "documentation_only"}
_PRESET_IDS = {"protected_surfaces", "blocked_verification", "unknown_coverage", "owner_dependency_gaps", "recent_changes"}
_SEVERITY_ORDER = {"R2": 0, "R1": 1, "R0": 2, "unknown": 3}
_STATE_ORDER = {"blocked": 0, "observed": 1, "unknown": 2}
_FORBIDDEN = ("absolutePath", "path", "uri", "prompt", "command", "stdout", "stderr", "secret", "rawPayload", "raw_payload")
_DRILLDOWN_KINDS = {"node", "edge", "evidence", "finding", "impact", "owner", "dependency"}
_TOP_KEYS = {"schemaVersion", "managementPolicyVersion", "status", "snapshotFingerprint", "summaryFingerprint", "overallRiskLevel", "headline", "risk", "impact", "coverage", "attentionItems", "trend", "presets", "diagnostics", "sourceRefs"}
_HEADLINE_KEYS = {"attentionStatus", "protectedCount", "blockedCount", "unknownCount", "evidenceCoverage", "ownerCoverage", "dependencyCoverage"}
_RISK_KEYS = {"status", "overallRiskLevel", "findingCount", "levels", "sourceRef"}
_IMPACT_KEYS = {"status", "observedCount", "blockedCount", "unknownCount", "categories", "sourceRef"}
_TREND_KEYS = {"status", "basis", "observations", "changedDimensions"}
_TREND_OBS_KEYS = {"snapshotFingerprint", "overallRiskLevel", "attentionCount", "unknownCount"}
_PRESET_KEYS = {"presetId", "labelCode", "available"}
_ATTENTION_KEYS = {"attentionId", "severity", "category", "state", "summaryCode", "sourceRefs", "drillDown"}
_DRILLDOWN_KEYS = {"kind", "identity"}
_SOURCE_REF_KEYS = {"kind", "identity", "fingerprint", "status"}
_DIAGNOSTIC_KEYS = {"code", "summary"}


class ManagementSummaryModelError(ValueError):
    pass


def _safe(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or not _SAFE_RE.fullmatch(value) or "/" in value or "\\" in value or ".." in value or any(token.lower() in value.lower() for token in _FORBIDDEN):
        raise ManagementSummaryModelError(f"{name} is not a bounded safe identifier")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ManagementSummaryModelError(f"{name} must be a lowercase SHA-256")
    return value


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100000:
        raise ManagementSummaryModelError(f"{name} must be a bounded non-negative integer")
    return value


def _exact_mapping(value: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ManagementSummaryModelError(f"{name} keys are invalid")
    return value


def _source_ref(value: Any, name: str = "sourceRef") -> dict[str, Any]:
    if value is None:
        return None
    item = _exact_mapping(value, _SOURCE_REF_KEYS, name)
    kind = _safe(item["kind"], f"{name}.kind")
    if not isinstance(kind, str) or kind not in _SOURCE_KINDS:
        raise ManagementSummaryModelError(f"{name}.kind is invalid")
    status = item["status"]
    if not isinstance(status, str) or status not in _SOURCE_STATUSES:
        raise ManagementSummaryModelError(f"{name}.status is invalid")
    return {"kind": kind, "identity": _safe(item["identity"], f"{name}.identity"), "fingerprint": _sha(item["fingerprint"], f"{name}.fingerprint"), "status": status}


def _diagnostics(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ManagementSummaryModelError("diagnostics are invalid")
    result = []
    for item in value:
        record = _exact_mapping(item, _DIAGNOSTIC_KEYS, "diagnostic")
        result.append({"code": _safe(record["code"], "diagnostic.code"), "summary": _safe(record["summary"], "diagnostic.summary")})
    return sorted({(item["code"], item["summary"]): item for item in result}.values(), key=lambda item: (item["code"], item["summary"]))


def _dedupe(records: list[dict[str, Any]], key: str, name: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in records:
        identity = item[key]
        if identity in seen and seen[identity] != item:
            raise ManagementSummaryModelError(f"{name} has conflicting duplicate identity")
        seen[identity] = item
    return list(seen.values())


def _dedupe_source_refs(records: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in records:
        identity = (item["kind"], item["identity"], item["fingerprint"])
        if identity in seen and seen[identity] != item:
            raise ManagementSummaryModelError(f"{name} has conflicting duplicate identity")
        seen[identity] = item
    return list(seen.values())


def _normalize(payload: Mapping[str, Any]) -> dict[str, Any]:
    _exact_mapping(payload, _TOP_KEYS, "summary")
    if payload["schemaVersion"] != MANAGEMENT_SUMMARY_SCHEMA or payload["managementPolicyVersion"] != MANAGEMENT_POLICY_VERSION:
        raise ManagementSummaryModelError("summary schema or policy version is invalid")
    if not isinstance(payload["status"], str) or payload["status"] not in _STATUSES or not isinstance(payload["overallRiskLevel"], str) or payload["overallRiskLevel"] not in _RISK_LEVELS:
        raise ManagementSummaryModelError("summary status or risk level is invalid")
    snapshot = _sha(payload["snapshotFingerprint"], "snapshotFingerprint")
    summary_fingerprint = _sha(payload["summaryFingerprint"], "summaryFingerprint")
    headline = _exact_mapping(payload["headline"], _HEADLINE_KEYS, "headline")
    if not isinstance(headline["attentionStatus"], str) or headline["attentionStatus"] not in {"clear", "attention", "unknown"}:
        raise ManagementSummaryModelError("headline.attentionStatus is invalid")
    normalized_headline = {"attentionStatus": headline["attentionStatus"], **{key: _count(headline[key], f"headline.{key}") for key in ("protectedCount", "blockedCount", "unknownCount")}, **{key: headline[key] for key in ("evidenceCoverage", "ownerCoverage", "dependencyCoverage")}}
    if any(value not in _COVERAGE for value in (normalized_headline["evidenceCoverage"], normalized_headline["ownerCoverage"], normalized_headline["dependencyCoverage"])):
        raise ManagementSummaryModelError("headline coverage is invalid")
    risk = _exact_mapping(payload["risk"], _RISK_KEYS, "risk")
    levels = _exact_mapping(risk["levels"], _RISK_LEVELS, "risk.levels")
    normalized_risk = {"status": risk["status"], "overallRiskLevel": risk["overallRiskLevel"], "findingCount": _count(risk["findingCount"], "risk.findingCount"), "levels": {key: _count(levels[key], f"risk.levels.{key}") for key in sorted(_RISK_LEVELS)}, "sourceRef": _source_ref(risk["sourceRef"], "risk.sourceRef")}
    if not isinstance(normalized_risk["status"], str) or normalized_risk["status"] not in _SOURCE_STATUSES or not isinstance(normalized_risk["overallRiskLevel"], str) or normalized_risk["overallRiskLevel"] not in _RISK_LEVELS:
        raise ManagementSummaryModelError("risk fields are invalid")
    impact = _exact_mapping(payload["impact"], _IMPACT_KEYS, "impact")
    if not isinstance(impact["status"], str) or impact["status"] not in _SOURCE_STATUSES or not isinstance(impact["categories"], list) or len(impact["categories"]) > 32 or any(not isinstance(item, str) or _safe(item, "impact.category") not in _D4_CATEGORIES for item in impact["categories"]):
        raise ManagementSummaryModelError("impact fields are invalid")
    mapped_categories = {"coverage_unknown": "coverage_gap", "documentation_only": None}
    normalized_impact = {"status": impact["status"], "observedCount": _count(impact["observedCount"], "impact.observedCount"), "blockedCount": _count(impact["blockedCount"], "impact.blockedCount"), "unknownCount": _count(impact["unknownCount"], "impact.unknownCount"), "categories": sorted({mapped_categories.get(item, item) for item in impact["categories"] if mapped_categories.get(item, item) is not None}), "sourceRef": _source_ref(impact["sourceRef"], "impact.sourceRef")}
    coverage = _exact_mapping(payload["coverage"], {"query", "comparison", "risk", "impact", "lineage", "catalog"}, "coverage")
    if any(value not in _COVERAGE for value in coverage.values()):
        raise ManagementSummaryModelError("coverage is invalid")
    if not isinstance(payload["attentionItems"], list) or len(payload["attentionItems"]) > 100:
        raise ManagementSummaryModelError("attentionItems are invalid")
    attention_items = []
    for raw in payload["attentionItems"]:
        item = _exact_mapping(raw, _ATTENTION_KEYS, "attentionItem")
        if not isinstance(item["severity"], str) or item["severity"] not in _RISK_LEVELS or not isinstance(item["state"], str) or item["state"] not in {"observed", "blocked", "unknown"} or not isinstance(item["category"], str) or item["category"] not in _ATTENTION_CATEGORIES:
            raise ManagementSummaryModelError("attention item enums are invalid")
        drill = _exact_mapping(item["drillDown"], _DRILLDOWN_KEYS, "attentionItem.drillDown")
        if drill["kind"] not in _DRILLDOWN_KINDS:
            raise ManagementSummaryModelError("attention drill-down kind is invalid")
        identity = _safe(drill["identity"], "attentionItem.drillDown.identity")
        if not isinstance(item["attentionId"], str):
            raise ManagementSummaryModelError("attentionId is invalid")
        parts = item["attentionId"].split(":")
        if len(parts) != 5 or not isinstance(parts[4], str) or (parts[4] != "none" and _safe(parts[4], "attentionId.sourceIdentity") != parts[4]):
            raise ManagementSummaryModelError("attentionId sourceIdentity is invalid")
        expected = f"{item['category']}:{drill['kind']}:{identity}:{item['state']}:{parts[4]}"
        if item["attentionId"] != expected:
            raise ManagementSummaryModelError("attentionId is not canonical")
        if not isinstance(item["sourceRefs"], list) or len(item["sourceRefs"]) > 20:
            raise ManagementSummaryModelError("attentionItem.sourceRefs are invalid")
        refs = [_source_ref(ref, "attentionItem.sourceRefs") for ref in item["sourceRefs"]]
        normalized_refs = _dedupe_source_refs(refs, "attentionItem.sourceRefs")
        if normalized_refs and parts[4] != "none" and parts[4] not in {ref["identity"] for ref in normalized_refs}:
            raise ManagementSummaryModelError("attentionId sourceIdentity is not bound to sourceRefs")
        attention_items.append({"attentionId": _safe(item["attentionId"], "attentionId"), "severity": item["severity"], "category": item["category"], "state": item["state"], "summaryCode": _safe(item["summaryCode"], "summaryCode"), "sourceRefs": sorted(normalized_refs, key=lambda ref: (ref["kind"], ref["identity"], ref["fingerprint"])), "drillDown": {"kind": drill["kind"], "identity": identity}})
    attention_items = _dedupe(attention_items, "attentionId", "attentionItems")
    trend = _exact_mapping(payload["trend"], _TREND_KEYS, "trend")
    if not isinstance(trend["status"], str) or trend["status"] not in _STATUSES or not _safe(trend["basis"], "trend.basis") or not isinstance(trend["observations"], list) or len(trend["observations"]) > 30 or not isinstance(trend["changedDimensions"], list) or len(trend["changedDimensions"]) > 20:
        raise ManagementSummaryModelError("trend is invalid")
    observations = []
    for raw in trend["observations"]:
        observation = _exact_mapping(raw, _TREND_OBS_KEYS, "trend.observation")
        observations.append({"snapshotFingerprint": _sha(observation["snapshotFingerprint"], "trend.snapshotFingerprint"), "overallRiskLevel": observation["overallRiskLevel"] if isinstance(observation["overallRiskLevel"], str) and observation["overallRiskLevel"] in _RISK_LEVELS else (_ for _ in ()).throw(ManagementSummaryModelError("trend risk level is invalid")), "attentionCount": _count(observation["attentionCount"], "trend.attentionCount"), "unknownCount": _count(observation["unknownCount"], "trend.unknownCount")})
    if not isinstance(payload["presets"], list) or len(payload["presets"]) > 10:
        raise ManagementSummaryModelError("presets are invalid")
    presets = []
    for raw in payload["presets"]:
        item = _exact_mapping(raw, _PRESET_KEYS, "preset")
        preset_id = _safe(item["presetId"], "preset.presetId")
        if preset_id not in _PRESET_IDS:
            raise ManagementSummaryModelError("preset.presetId is invalid")
        presets.append({"presetId": preset_id, "labelCode": _safe(item["labelCode"], "preset.labelCode"), "available": item["available"] if isinstance(item["available"], bool) else (_ for _ in ()).throw(ManagementSummaryModelError("preset.available is invalid"))})
    presets = _dedupe(presets, "presetId", "presets")
    if not isinstance(payload["sourceRefs"], list) or len(payload["sourceRefs"]) > 20:
        raise ManagementSummaryModelError("sourceRefs are invalid")
    source_refs = _dedupe_source_refs([_source_ref(item, "sourceRefs") for item in payload["sourceRefs"]], "sourceRefs")
    source_refs = sorted(source_refs, key=lambda ref: (ref["kind"], ref["identity"], ref["fingerprint"]))
    return {"schemaVersion": MANAGEMENT_SUMMARY_SCHEMA, "managementPolicyVersion": MANAGEMENT_POLICY_VERSION, "status": payload["status"], "snapshotFingerprint": snapshot, "summaryFingerprint": summary_fingerprint, "overallRiskLevel": payload["overallRiskLevel"], "headline": normalized_headline, "risk": normalized_risk, "impact": normalized_impact, "coverage": dict(sorted(coverage.items())), "attentionItems": sorted(attention_items, key=lambda item: (_SEVERITY_ORDER[item["severity"]], _STATE_ORDER[item["state"]], item["attentionId"])), "trend": {"status": trend["status"], "basis": _safe(trend["basis"], "trend.basis"), "observations": observations, "changedDimensions": sorted({_safe(value, "changedDimension") for value in trend["changedDimensions"]})}, "presets": sorted(presets, key=lambda item: item["presetId"]), "diagnostics": _diagnostics(payload["diagnostics"]), "sourceRefs": source_refs}


def canonical_management_summary_payload(summary: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize(summary)
    normalized.pop("summaryFingerprint", None)
    return normalized


def fingerprint_management_summary(summary: Mapping[str, Any]) -> str:
    body = canonical_management_summary_payload(summary)
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_management_summary_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize(payload)
    expected = fingerprint_management_summary(payload)
    if payload["summaryFingerprint"] != expected:
        raise ManagementSummaryModelError("summaryFingerprint does not match canonical payload")
    return normalized
