from __future__ import annotations

from backend.agents.governance_graph_comparison_models import GovernanceGraphComparisonResult
from backend.agents.governance_graph_risk_service import (
    D3_DOCUMENTATION_NODE_ALLOWLIST_V1,
    GovernanceGraphRiskService,
)


def _comparison(*, status="available", changes=(), edges=(), evidence=(), diagnostics=()):
    identity = {
        "runId": "run-left", "graphFingerprint": "a" * 64,
        "generatedAt": "2026-07-29T00:00:00+00:00", "freshness": "fresh",
    }
    summary = {
        "addedNodes": sum(item["changeType"] == "added" for item in changes),
        "removedNodes": sum(item["changeType"] == "removed" for item in changes),
        "changedNodes": sum(item["changeType"] == "changed" for item in changes),
        "unchangedNodes": 0,
        "addedEdges": sum(item["changeType"] == "added" for item in edges),
        "removedEdges": sum(item["changeType"] == "removed" for item in edges),
        "changedEdges": sum(item["changeType"] == "changed" for item in edges),
        "addedEvidenceRefs": sum(item["changeType"] == "added" for item in evidence),
        "removedEvidenceRefs": sum(item["changeType"] == "removed" for item in evidence),
        "changedEvidenceRefs": sum(item["changeType"] == "changed" for item in evidence),
    }
    return GovernanceGraphComparisonResult.from_parts(
        status=status, left_reference={"runId": "run-left"}, right_reference={"runId": "run-right"},
        left_snapshot=None if status in {"invalid", "unavailable"} else identity,
        right_snapshot=None if status in {"invalid", "unavailable"} else {**identity, "runId": "run-right"},
        summary=summary, node_changes=changes, edge_changes=edges, evidence_changes=evidence, diagnostics=diagnostics,
    )


def _node(node_id, change_type="changed"):
    return {"nodeId": node_id, "changeType": change_type, "before": {"nodeId": node_id}, "after": {"nodeId": node_id}}


def test_invalid_and_unavailable_are_diagnostics_only():
    service = GovernanceGraphRiskService()
    assert service.evaluate(_comparison(status="invalid", diagnostics=({"code": "bad", "summary": "invalid input"},))).to_dict()["findings"] == []
    assert service.evaluate(_comparison(status="unavailable", diagnostics=({"code": "missing", "summary": "snapshot unavailable"},))).to_dict()["findings"] == []


def test_protected_signal_is_r2_and_verification_change_is_r1():
    summary = GovernanceGraphRiskService().evaluate(_comparison(changes=(_node("protected_incident"), _node("hermes"))))
    assert summary.overall_risk_level == "R2"
    assert {item["ruleId"] for item in summary.to_dict()["findings"]} == {"D3-PROTECTED-NODE", "D3-VERIFICATION-REGRESSION"}


def test_documentation_allowlist_is_exact_and_no_edge_inference():
    summary = GovernanceGraphRiskService().evaluate(_comparison(changes=(_node("documentation"),), edges=()))
    assert D3_DOCUMENTATION_NODE_ALLOWLIST_V1 == frozenset({"documentation"})
    assert summary.overall_risk_level == "R0"
    assert summary.to_dict()["findings"][0]["ruleId"] == "D3-DOCUMENTATION-ONLY"


def test_repeated_evaluation_is_byte_identical_and_unknown_is_not_low_risk():
    comparison = _comparison(status="unknown", changes=(_node("unknown_surface"),))
    first = GovernanceGraphRiskService().evaluate(comparison).to_dict()
    second = GovernanceGraphRiskService().evaluate(comparison).to_dict()
    assert first == second
    assert first["overallRiskLevel"] == "unknown"
