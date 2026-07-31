from __future__ import annotations

from typing import Any, Mapping, Sequence

from .governance_graph_management_summary_adapters import (
    SourceCoverage,
    adapt_d1_coverage,
    adapt_d2_coverage,
    adapt_d3_coverage,
    adapt_d4_coverage,
    adapt_e1_coverage,
    adapt_e3_coverage,
)
from .governance_graph_management_summary_models import fingerprint_management_summary, validate_management_summary_payload


_PRECEDENCE = {"invalid": 0, "stale": 1, "blocked": 2, "unknown": 3, "missing": 4, "unavailable": 5, "partial": 6, "available": 7}


def _coverage_diagnostic(source_kind: str, source: Mapping[str, Any] | None, coverage: SourceCoverage) -> dict[str, str] | None:
    if coverage.diagnostics:
        return None
    if source is not None:
        status = source.get("status")
        if not isinstance(status, str) or status not in {"available", "partial", "unknown", "missing", "unavailable", "stale", "blocked", "invalid"}:
            return {"code": "source_status_invalid", "summary": f"{source_kind}_status"}
        schema = source.get("schemaVersion")
        if schema is not None and not isinstance(schema, str):
            return {"code": "source_schema_invalid", "summary": f"{source_kind}_schema"}
        if any(key in source for key in ("path", "uri", "absolutePath", "rawPayload", "raw_payload", "secret", "command", "stdout", "stderr")):
            return {"code": "source_payload_forbidden", "summary": f"{source_kind}_payload"}
        for key in ("snapshotFingerprint", "comparisonFingerprint", "riskSummaryFingerprint", "impactSummaryFingerprint", "lineageFingerprint", "readModelFingerprint"):
            if key in source and source[key] is not None and (not isinstance(source[key], str) or len(source[key]) != 64 or any(char not in "0123456789abcdef" for char in source[key])):
                return {"code": "source_fingerprint_invalid", "summary": f"{source_kind}_{key}"}
    mapping = {
        "invalid": "source_schema_invalid",
        "stale": "source_snapshot_mismatch",
        "missing": "source_snapshot_missing",
        "unknown": "source_binding_invalid",
    }
    code = mapping.get(coverage.status)
    if code is None:
        return None
    return {"code": code, "summary": f"{source_kind}_{coverage.status}"}


class GovernanceGraphManagementSummaryService:
    @staticmethod
    def build_trend(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if len(snapshots) < 2:
            return {"status": "unknown", "basis": "insufficient_comparable_snapshots", "observations": [], "changedDimensions": []}
        required = {"schemaVersion", "managementPolicyVersion", "snapshotFamily", "snapshotFingerprint", "summaryFingerprint", "overallRiskLevel", "attentionCount", "unknownCount", "headline", "summary"}
        seen = set()
        observations = []
        for item in snapshots:
            if not isinstance(item, Mapping) or set(item) != required or item.get("schemaVersion") != "governance-graph-management-summary-v1" or item.get("managementPolicyVersion") != "e4-management-summary-v1" or not isinstance(item.get("snapshotFamily"), str) or not __import__("re").fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", item["snapshotFamily"]) or not isinstance(item.get("snapshotFingerprint"), str) or not isinstance(item.get("summaryFingerprint"), str) or len(item["snapshotFingerprint"]) != 64 or len(item["summaryFingerprint"]) != 64 or any(char not in "0123456789abcdef" for char in item["snapshotFingerprint"] + item["summaryFingerprint"]) or item["snapshotFingerprint"] in seen or item.get("overallRiskLevel") not in {"R2", "R1", "R0", "unknown"}:
                return {"status": "invalid", "basis": "invalid_comparable_snapshot", "observations": [], "changedDimensions": []}
            try:
                validated_summary = validate_management_summary_payload(item["summary"])
            except Exception:
                return {"status": "invalid", "basis": "invalid_comparable_snapshot", "observations": [], "changedDimensions": []}
            if validated_summary["summaryFingerprint"] != item["summaryFingerprint"] or validated_summary["snapshotFingerprint"] != item["snapshotFingerprint"]:
                return {"status": "invalid", "basis": "invalid_comparable_snapshot", "observations": [], "changedDimensions": []}
            seen.add(item["snapshotFingerprint"])
            headline = item["headline"]
            if not isinstance(headline, Mapping) or any(headline.get(key) not in {"available", "partial", "unknown", "missing", "stale", "blocked", "invalid"} for key in ("ownerCoverage", "dependencyCoverage", "evidenceCoverage")) or headline.get("attentionStatus") not in {"clear", "attention", "unknown"}:
                return {"status": "invalid", "basis": "invalid_comparable_snapshot", "observations": [], "changedDimensions": []}
            if any(isinstance(item.get(key), bool) or not isinstance(item.get(key), int) or item.get(key) < 0 for key in ("attentionCount", "unknownCount")):
                return {"status": "invalid", "basis": "invalid_comparable_snapshot", "observations": [], "changedDimensions": []}
            observations.append({"snapshotFingerprint": item["snapshotFingerprint"], "overallRiskLevel": item["overallRiskLevel"], "attentionCount": item["attentionCount"], "unknownCount": item["unknownCount"]})
        if len({item["snapshotFamily"] for item in snapshots}) != 1:
            return {"status": "invalid", "basis": "incomparable_snapshot_family", "observations": [], "changedDimensions": []}
        first, last = snapshots[0], snapshots[-1]
        changed = []
        for key in ("overallRiskLevel",):
            if first[key] != last[key]: changed.append(key)
        for key in ("attentionCount", "unknownCount"):
            if observations[0][key] != observations[-1][key]: changed.append(key)
        if first["headline"]["attentionStatus"] != last["headline"]["attentionStatus"]: changed.append("attentionStatus")
        for key in ("ownerCoverage", "dependencyCoverage", "evidenceCoverage"):
            if first["headline"][key] != last["headline"][key]: changed.append(key)
        return {"status": "available", "basis": "explicit_summary_snapshots", "observations": observations, "changedDimensions": changed}

    @staticmethod
    def apply_preset(summary: Mapping[str, Any], preset_id: str | None, snapshot_fingerprint: str | None = None) -> dict[str, Any]:
        validate_management_summary_payload(summary)
        if preset_id is None:
            return {"selectedPresetId": None, "summary": dict(summary)}
        allowed = {"protected_surfaces", "blocked_verification", "unknown_coverage", "owner_dependency_gaps", "recent_changes"}
        if preset_id not in allowed:
            raise ValueError("preset id is invalid")
        if snapshot_fingerprint is not None and summary.get("snapshotFingerprint") != snapshot_fingerprint:
            return {"selectedPresetId": None, "summary": dict(summary)}
        predicates = {
            "protected_surfaces": lambda item: item.get("category") == "protected_governance_surface",
            "blocked_verification": lambda item: item.get("state") == "blocked" or item.get("category") == "verification_assurance",
            "unknown_coverage": lambda item: item.get("state") == "unknown",
            "owner_dependency_gaps": lambda item: item.get("category") == "catalog_coverage_gap",
            "recent_changes": lambda item: item.get("category") in {"implementation_governance", "verification_assurance"},
        }
        from copy import deepcopy

        projected = deepcopy(dict(summary))
        projected["attentionItems"] = [item for item in summary.get("attentionItems", []) if predicates[preset_id](item)]
        projected["headline"] = deepcopy(summary["headline"])
        projected["headline"]["protectedCount"] = sum(item["category"] == "protected_governance_surface" for item in projected["attentionItems"])
        projected["headline"]["blockedCount"] = sum(item["state"] == "blocked" for item in projected["attentionItems"])
        projected["headline"]["unknownCount"] = sum(item["state"] == "unknown" for item in projected["attentionItems"])
        projected["summaryFingerprint"] = fingerprint_management_summary(projected)
        validate_management_summary_payload(projected)
        return {"selectedPresetId": preset_id, "summary": projected}
    @staticmethod
    def compose(*, snapshot_fingerprint: str, query: Mapping[str, Any] | None, comparison: Mapping[str, Any] | None, risk: Mapping[str, Any] | None, impact: Mapping[str, Any] | None, lineage: Mapping[str, Any] | None, catalog: Mapping[str, Any] | None, trend_snapshots: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        coverages = {
            "query": adapt_d1_coverage(query, snapshot_fingerprint),
            "comparison": adapt_d2_coverage(comparison, snapshot_fingerprint),
            "risk": adapt_d3_coverage(risk),
            "impact": adapt_d4_coverage(impact),
            "lineage": adapt_e1_coverage(lineage, snapshot_fingerprint),
            "catalog": adapt_e3_coverage(catalog, snapshot_fingerprint),
        }
        if comparison and risk and risk.get("comparisonFingerprint") != comparison.get("comparisonFingerprint"):
            coverages["risk"] = SourceCoverage("stale")
        if impact and risk and (impact.get("riskSummaryFingerprint") != risk.get("riskSummaryFingerprint") or impact.get("comparisonFingerprint") != (comparison or {}).get("comparisonFingerprint")):
            coverages["impact"] = SourceCoverage("stale")
        diagnostics: list[dict[str, str]] = []
        sources = {"query": query, "comparison": comparison, "risk": risk, "impact": impact, "lineage": lineage, "catalog": catalog}
        for source_kind, coverage in coverages.items():
            diagnostics.extend(dict(item) for item in coverage.diagnostics)
            fallback = _coverage_diagnostic(source_kind, sources[source_kind], coverage)
            if fallback is not None:
                diagnostics.append(fallback)
        trend = GovernanceGraphManagementSummaryService.build_trend(trend_snapshots)
        if trend["status"] == "invalid":
            diagnostics.append({"code": "trend_envelope_invalid", "summary": trend["basis"]})
        diagnostics = sorted({(item["code"], item["summary"]): item for item in diagnostics}.values(), key=lambda item: (item["code"], item["summary"]))
        required = [coverages[key] for key in ("comparison", "risk", "impact", "lineage", "catalog")]
        status_inputs = required + ([coverages["query"]] if coverages["query"].status != "unavailable" else [])
        status = min((item.status for item in status_inputs), key=lambda value: _PRECEDENCE.get(value, 0))
        if all(item.status == "available" for item in status_inputs):
            status = "available"
        elif all(item.status in {"available", "partial"} for item in status_inputs):
            status = "partial"
        risk_status = risk.get("status", "missing") if risk else "missing"
        risk_fp = risk.get("riskSummaryFingerprint") if risk else "0" * 64
        if not isinstance(risk_fp, str) or len(risk_fp) != 64:
            risk_fp = "0" * 64
        risk_valid = coverages["comparison"].status == "available" and coverages["risk"].status in {"available", "partial", "blocked", "unknown"} and risk_status in {"available", "partial", "blocked", "unknown"} and isinstance(risk, Mapping)
        findings = risk.get("findings", []) if risk_valid else []
        levels = {"R2": 0, "R1": 0, "R0": 0, "unknown": 0}
        attention = []
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            level = finding.get("level") if finding.get("level") in levels else "unknown"
            levels[level] += 1
            rule = finding.get("ruleId")
            mapping = {"D3-PROTECTED-NODE": ("protected_governance_surface", "R2", "observed"), "D3-PROTECTED-SURFACE": ("protected_governance_surface", "R2", "observed"), "D3-VERIFICATION-REGRESSION": ("verification_assurance", "R1", "observed"), "D3-BEHAVIORAL-CHANGE": ("implementation_governance", "R1", "observed"), "D3-BLOCKED-COMPARISON": ("workflow_observability_blocked", "unknown", "blocked"), "D3-UNKNOWN-COVERAGE": ("coverage_gap", "unknown", "unknown")}
            if rule not in mapping:
                continue
            category, severity, state = mapping[rule]
            change = finding.get("sourceChange") if isinstance(finding.get("sourceChange"), Mapping) else {"kind": "finding", "identity": str(finding.get("findingId", "finding"))}
            kind = change.get("kind", "finding") if change.get("kind") in {"node", "edge", "evidence", "finding"} else "finding"
            identity = change.get("identity", finding.get("findingId", "finding"))
            source_identity = finding.get("findingId", "none")
            attention.append({"attentionId": f"{category}:{kind}:{identity}:{state}:{source_identity}", "severity": severity, "category": category, "state": state, "summaryCode": finding.get("rationaleCode", "risk_signal"), "sourceRefs": [{"kind": "d3_risk_summary", "identity": source_identity, "fingerprint": risk_fp, "status": "available"}], "drillDown": {"kind": kind, "identity": identity}})
        if coverages["lineage"].status != "available":
            attention.append({"attentionId": "evidence_coverage_gap:evidence:lineage:unknown:none", "severity": "unknown", "category": "evidence_coverage_gap", "state": "unknown", "summaryCode": "lineage_coverage_gap", "sourceRefs": [], "drillDown": {"kind": "evidence", "identity": "lineage"}})
        if coverages["catalog"].status in {"missing", "unknown", "partial", "stale", "blocked"}:
            attention.append({"attentionId": "catalog_coverage_gap:dependency:catalog:unknown:none", "severity": "unknown", "category": "catalog_coverage_gap", "state": "unknown", "summaryCode": "catalog_coverage_gap", "sourceRefs": [], "drillDown": {"kind": "dependency", "identity": "catalog"}})
        overall_risk = max((level for level, count in levels.items() if count and level in {"R2", "R1", "R0"}), key={"R2": 0, "R1": 1, "R0": 2}.get, default="unknown")
        impact_valid = coverages["impact"].status in {"available", "partial", "blocked", "unknown"} and isinstance(impact, Mapping) and impact.get("status") in {"available", "blocked", "unknown"}
        impact_status = impact.get("status", "missing") if impact_valid else coverages["impact"].status
        impact_fp = impact.get("impactSummaryFingerprint") if impact_valid and isinstance(impact.get("impactSummaryFingerprint"), str) else None
        impact_records = [item for item in (impact.get("impacts", []) if impact_valid else []) if isinstance(item, Mapping)]
        categories = sorted({item.get("category") for item in impact_records if item.get("category") in {"protected_governance_surface", "verification_assurance", "implementation_governance", "workflow_observability_blocked", "coverage_unknown", "documentation_only"}})
        impact_blocked = sum(item.get("impactState") == "blocked" for item in impact_records)
        impact_unknown = sum(item.get("impactState") == "unknown" for item in impact_records)
        for item in impact_records:
            category = {"coverage_unknown": "coverage_gap", "documentation_only": None}.get(item.get("category"), item.get("category"))
            state = item.get("impactState")
            if category in {"protected_governance_surface", "verification_assurance", "implementation_governance", "workflow_observability_blocked", "coverage_gap"} and state in {"observed", "blocked", "unknown"}:
                identity = item.get("sourceFindingId", item.get("impactId", "impact"))
                kind = item.get("sourceChange", {}).get("kind", "impact") if isinstance(item.get("sourceChange"), Mapping) else "impact"
                if kind not in {"node", "edge", "evidence", "impact"}:
                    kind = "impact"
                drill_identity = item.get("sourceChange", {}).get("identity", identity) if isinstance(item.get("sourceChange"), Mapping) else identity
                severity = item.get("riskLevel", "unknown") if item.get("riskLevel") in {"R2", "R1", "R0", "unknown"} else "unknown"
                attention.append({"attentionId": f"{category}:{kind}:{drill_identity}:{state}:{identity}", "severity": severity, "category": category, "state": state, "summaryCode": item.get("rationaleCode", "impact_signal"), "sourceRefs": [], "drillDown": {"kind": kind, "identity": drill_identity}})
        required_available = all(item.status == "available" for item in required)
        summary = {"schemaVersion": "governance-graph-management-summary-v1", "managementPolicyVersion": "e4-management-summary-v1", "status": status, "snapshotFingerprint": snapshot_fingerprint, "summaryFingerprint": "0" * 64, "overallRiskLevel": overall_risk if required_available else "unknown", "headline": {"attentionStatus": "attention" if attention else ("clear" if required_available else "unknown"), "protectedCount": sum(item["category"] == "protected_governance_surface" for item in attention) + sum(item.get("category") == "protected_governance_surface" for item in impact_records), "blockedCount": sum(item["state"] == "blocked" for item in attention) + impact_blocked, "unknownCount": sum(item.status in {"unknown", "missing", "stale", "blocked"} for item in coverages.values()), "evidenceCoverage": coverages["lineage"].status, "ownerCoverage": coverages["catalog"].status, "dependencyCoverage": coverages["catalog"].status}, "risk": {"status": risk_status if risk_valid else coverages["risk"].status, "overallRiskLevel": overall_risk if required_available else "unknown", "findingCount": len(findings), "levels": levels, "sourceRef": {"kind": "d3_risk_summary", "identity": "risk-summary", "fingerprint": risk_fp, "status": risk_status} if risk_valid else None}, "impact": {"status": impact_status, "observedCount": len(impact_records), "blockedCount": impact_blocked, "unknownCount": impact_unknown, "categories": categories, "sourceRef": {"kind": "d4_change_impact", "identity": "impact-summary", "fingerprint": impact_fp, "status": impact_status} if impact_valid else None}, "coverage": {key: item.status for key, item in coverages.items()}, "attentionItems": attention, "trend": trend, "presets": [{"presetId": value, "labelCode": value, "available": any((value == "protected_surfaces" and item["category"] == "protected_governance_surface") or (value == "blocked_verification" and item["state"] == "blocked") or (value == "unknown_coverage" and item["state"] == "unknown") for item in attention) or (value == "owner_dependency_gaps" and coverages["catalog"].status in {"missing", "unknown", "partial"}) or (value == "recent_changes" and coverages["comparison"].status == "available" and bool(comparison and comparison.get("nodeChanges"))),} for value in ("protected_surfaces", "blocked_verification", "unknown_coverage", "owner_dependency_gaps", "recent_changes")], "diagnostics": diagnostics, "sourceRefs": []}
        summary["summaryFingerprint"] = fingerprint_management_summary(summary)
        validate_management_summary_payload(summary)
        return summary
