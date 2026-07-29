from __future__ import annotations

import json

import pytest

from backend.agents.governance_graph_comparison_models import GovernanceGraphComparisonSchemaError
from backend.agents.governance_graph_risk_models import (
    GovernanceGraphRiskFinding,
    GovernanceGraphRiskInput,
    GovernanceGraphRiskSchemaError,
    GovernanceGraphRiskSummary,
    RISK_RULE_REGISTRY_VERSION,
)


def _comparison_payload() -> dict:
    identity = {
        "runId": "run-left",
        "graphFingerprint": "a" * 64,
        "generatedAt": "2026-07-29T00:00:00+00:00",
        "freshness": "fresh",
    }
    summary = {
        "addedNodes": 0, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0,
        "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
        "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
    }
    from backend.agents.governance_graph_comparison_models import GovernanceGraphComparisonResult
    return GovernanceGraphComparisonResult.from_parts(
        status="available",
        left_reference={"runId": "run-left"},
        right_reference={"runId": "run-right"},
        left_snapshot=identity,
        right_snapshot={**identity, "runId": "run-right"},
        summary=summary, node_changes=(), edge_changes=(), evidence_changes=(), diagnostics=(),
    ).to_dict()


def _finding(rule_id: str = "D3-VERIFICATION-REGRESSION") -> GovernanceGraphRiskFinding:
    return GovernanceGraphRiskFinding.from_dict({
        "findingId": f"{rule_id}:node:hermes:changed",
        "ruleId": rule_id,
        "level": "R1",
        "category": "verification_integrity",
        "confidence": "high",
        "sourceChange": {"kind": "node", "identity": "hermes", "changeType": "changed"},
        "evidenceIdentities": [],
        "rationaleCode": "verification_node_changed",
        "summary": "Hermes bounded status changed between snapshots.",
    })


def _summary(findings=()):
    return GovernanceGraphRiskSummary.from_parts(
        status="available", comparison_fingerprint="a" * 64,
        findings=findings,
        coverage={"observedChanges": len(findings), "classifiedChanges": len(findings), "unknownChanges": 0, "invalidChanges": 0, "blockedChanges": 0},
        diagnostics=(),
    )


def test_risk_input_requires_bridge_complete_comparison():
    payload = _comparison_payload()
    payload.pop("leftReference")
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskInput.from_dict(payload)


def test_risk_input_rejects_d2_schema_errors():
    payload = _comparison_payload()
    payload["comparisonFingerprint"] = "b" * 64
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskInput.from_dict(payload)


def test_risk_summary_fingerprint_is_reproducible_and_bounded():
    first = _summary(findings=(_finding(),))
    second = _summary(findings=(_finding(),))
    assert first.risk_summary_fingerprint == second.risk_summary_fingerprint
    assert "/private/raw" not in json.dumps(first.to_dict())
    assert first.to_dict()["riskRuleRegistryVersion"] == RISK_RULE_REGISTRY_VERSION


def test_finding_rejects_absolute_or_unbounded_metadata():
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskFinding.from_dict({
            "findingId": "D3-X:node:bad:changed", "ruleId": "D3-X", "level": "R1",
            "category": "x", "confidence": "high",
            "sourceChange": {"kind": "node", "identity": "/private/raw", "changeType": "changed"},
            "evidenceIdentities": [], "rationaleCode": "x", "summary": "x",
        })


def test_invalid_summary_cannot_contain_findings():
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskSummary.from_parts(
            status="invalid", comparison_fingerprint="a" * 64,
            findings=(_finding(),), coverage={"observedChanges": 1, "classifiedChanges": 0, "unknownChanges": 0, "invalidChanges": 1, "blockedChanges": 0}, diagnostics=(),
        )


def test_risk_summary_from_dict_round_trips_exact_public_envelope():
    payload = _summary(findings=(_finding(),)).to_dict()
    parsed = GovernanceGraphRiskSummary.from_dict(payload)
    assert parsed.to_dict() == payload


def test_risk_summary_from_dict_rejects_extra_top_level_key():
    payload = _summary().to_dict()
    payload["extra"] = "not allowed"
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskSummary.from_dict(payload)


def test_risk_summary_from_dict_rejects_tampered_fingerprint():
    payload = _summary().to_dict()
    payload["riskSummaryFingerprint"] = "b" * 64
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskSummary.from_dict(payload)


def test_risk_summary_from_dict_rejects_duplicate_finding_id():
    finding = _finding().to_dict()
    payload = _summary(findings=(_finding(),)).to_dict()
    payload["findings"].append(finding)
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskSummary.from_dict(payload)
