from __future__ import annotations

from typing import Any, Mapping

from .governance_graph_impact_models import GovernanceGraphImpactInput, GovernanceGraphImpactModelError, GovernanceGraphImpactSummary


_RULES = {
    "D3-PROTECTED-NODE": ("protected_governance_surface", "observed"),
    "D3-PROTECTED-SURFACE": ("protected_governance_surface", "observed"),
    "D3-VERIFICATION-REGRESSION": ("verification_assurance", "observed"),
    "D3-BEHAVIORAL-CHANGE": ("implementation_governance", "observed"),
    "D3-DOCUMENTATION-ONLY": ("documentation_only", "observed"),
    "D3-BLOCKED-COMPARISON": ("workflow_observability_blocked", "blocked"),
    "D3-UNKNOWN-COVERAGE": ("coverage_unknown", "unknown"),
}


class GovernanceGraphImpactService:
    """Pure D-3 finding projection; it never reads snapshots or runtime state."""

    def evaluate(self, payload: Mapping[str, Any] | GovernanceGraphImpactInput) -> GovernanceGraphImpactSummary:
        try:
            source = payload if isinstance(payload, GovernanceGraphImpactInput) else GovernanceGraphImpactInput.from_dict(payload)
        except GovernanceGraphImpactModelError:
            return GovernanceGraphImpactSummary.invalid("invalid_input")
        summary = source.risk_summary
        if summary.status in {"invalid", "unavailable"}:
            return GovernanceGraphImpactSummary.from_parts(status=summary.status, comparison_fingerprint=None, risk_summary_fingerprint=None, impacts=(), coverage={"coverageStatus": "unknown", "changedSeeds": 0, "mappedImpacts": 0, "protectedSignals": 0, "unknownImpacts": 0, "blockedImpacts": 0}, diagnostics=summary.diagnostics)
        impacts = []
        for finding in summary.findings:
            mapped = _RULES.get(finding.rule_id)
            if mapped is None:
                return GovernanceGraphImpactSummary.invalid("unknown_risk_rule")
            category, impact_state = mapped
            impacts.append({"impactId": finding.finding_id, "sourceFindingId": finding.finding_id, "riskLevel": finding.level, "category": category, "impactState": impact_state, "sourceChange": dict(finding.source_change), "evidenceIdentities": list(finding.evidence_identities), "rationaleCode": finding.rationale_code})
        coverage_status = "blocked" if summary.status == "blocked" else "unknown" if summary.status == "unknown" else "available"
        return GovernanceGraphImpactSummary.from_parts(status=summary.status, comparison_fingerprint=summary.comparison_fingerprint, risk_summary_fingerprint=summary.risk_summary_fingerprint, impacts=impacts, coverage={"coverageStatus": coverage_status, "changedSeeds": len(summary.findings), "mappedImpacts": len(impacts), "protectedSignals": sum(item["riskLevel"] == "R2" for item in impacts), "unknownImpacts": sum(item["impactState"] == "unknown" for item in impacts), "blockedImpacts": sum(item["impactState"] == "blocked" for item in impacts)}, diagnostics=summary.diagnostics)
