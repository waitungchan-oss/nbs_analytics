from backend.agents.governance_graph_impact_service import GovernanceGraphImpactService
from backend.agents.governance_graph_risk_models import GovernanceGraphRiskFinding, GovernanceGraphRiskSummary


def _payload(rule_id="D3-VERIFICATION-REGRESSION", status="available"):
    finding = GovernanceGraphRiskFinding.from_dict({
        "findingId": f"{rule_id}:node:hermes:changed", "ruleId": rule_id, "level": "R1", "category": "x", "confidence": "high",
        "sourceChange": {"kind": "node", "identity": "hermes", "changeType": "changed"}, "evidenceIdentities": [], "rationaleCode": "x", "summary": "x",
    })
    summary = GovernanceGraphRiskSummary.from_parts(status=status, comparison_fingerprint="a" * 64, findings=() if status in {"invalid", "unavailable"} else (finding,), coverage={"observedChanges": 1, "classifiedChanges": 1, "unknownChanges": 0, "invalidChanges": 0, "blockedChanges": 0}, diagnostics=()).to_dict()
    return {"schemaVersion": "governance-graph-impact-input-v1", "riskSummary": summary}


def test_service_maps_known_rule_one_to_one():
    result = GovernanceGraphImpactService().evaluate(_payload())
    assert result.to_dict()["impacts"][0]["category"] == "verification_assurance"
    assert result.to_dict()["impacts"][0]["sourceFindingId"].startswith("D3-VERIFICATION-REGRESSION")


def test_service_fail_closes_unknown_rule():
    result = GovernanceGraphImpactService().evaluate(_payload("D3-NOT-REGISTERED"))
    assert result.status == "invalid"
    assert result.to_dict()["riskSummaryFingerprint"] is None
