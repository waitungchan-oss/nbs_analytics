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


class GovernanceGraphManagementSummaryService:
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
        summary = {"schemaVersion": "governance-graph-management-summary-v1", "managementPolicyVersion": "e4-management-summary-v1", "status": status, "snapshotFingerprint": snapshot_fingerprint, "summaryFingerprint": "0" * 64, "overallRiskLevel": overall_risk if required_available else "unknown", "headline": {"attentionStatus": "attention" if attention else ("clear" if required_available else "unknown"), "protectedCount": sum(item["category"] == "protected_governance_surface" for item in attention) + sum(item.get("category") == "protected_governance_surface" for item in impact_records), "blockedCount": sum(item["state"] == "blocked" for item in attention) + impact_blocked, "unknownCount": sum(item.status in {"unknown", "missing", "stale", "blocked"} for item in coverages.values()), "evidenceCoverage": coverages["lineage"].status, "ownerCoverage": coverages["catalog"].status, "dependencyCoverage": coverages["catalog"].status}, "risk": {"status": risk_status if risk_valid else coverages["risk"].status, "overallRiskLevel": overall_risk if required_available else "unknown", "findingCount": len(findings), "levels": levels, "sourceRef": {"kind": "d3_risk_summary", "identity": "risk-summary", "fingerprint": risk_fp, "status": risk_status} if risk_valid else None}, "impact": {"status": impact_status, "observedCount": len(impact_records), "blockedCount": impact_blocked, "unknownCount": impact_unknown, "categories": categories, "sourceRef": {"kind": "d4_change_impact", "identity": "impact-summary", "fingerprint": impact_fp, "status": impact_status} if impact_valid else None}, "coverage": {key: item.status for key, item in coverages.items()}, "attentionItems": attention, "trend": {"status": "unknown", "basis": "insufficient_comparable_snapshots", "observations": [], "changedDimensions": []}, "presets": [{"presetId": value, "labelCode": value, "available": any((value == "protected_surfaces" and item["category"] == "protected_governance_surface") or (value == "blocked_verification" and item["state"] == "blocked") or (value == "unknown_coverage" and item["state"] == "unknown") for item in attention) or (value == "owner_dependency_gaps" and coverages["catalog"].status in {"missing", "unknown", "partial"}) or (value == "recent_changes" and coverages["comparison"].status == "available" and bool(comparison and comparison.get("nodeChanges"))),} for value in ("protected_surfaces", "blocked_verification", "unknown_coverage", "owner_dependency_gaps", "recent_changes")], "diagnostics": [], "sourceRefs": []}
        summary["summaryFingerprint"] = fingerprint_management_summary(summary)
        validate_management_summary_payload(summary)
        return summary
