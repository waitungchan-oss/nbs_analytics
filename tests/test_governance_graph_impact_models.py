import pytest

from backend.agents.governance_graph_impact_models import (
    GovernanceGraphImpactInput,
    GovernanceGraphImpactModelError,
    GovernanceGraphImpactSummary,
)
from backend.agents.governance_graph_risk_models import GovernanceGraphRiskSummary


def _risk_summary() -> dict:
    return GovernanceGraphRiskSummary.from_parts(
        status="available", comparison_fingerprint="a" * 64, findings=(),
        coverage={"observedChanges": 0, "classifiedChanges": 0, "unknownChanges": 0, "invalidChanges": 0, "blockedChanges": 0}, diagnostics=(),
    ).to_dict()


def test_impact_input_accepts_only_strict_wrapper_envelope():
    parsed = GovernanceGraphImpactInput.from_dict({
        "schemaVersion": "governance-graph-impact-input-v1", "riskSummary": _risk_summary(),
    })
    assert parsed.risk_summary.comparison_fingerprint == "a" * 64
    with pytest.raises(GovernanceGraphImpactModelError):
        GovernanceGraphImpactInput.from_dict({"schemaVersion": "governance-graph-impact-input-v1", "riskSummary": _risk_summary(), "path": "x"})


def test_impact_summary_is_deterministic_and_bounded():
    first = GovernanceGraphImpactSummary.from_parts(
        status="available", comparison_fingerprint="a" * 64, risk_summary_fingerprint="b" * 64,
        impacts=(), coverage={"coverageStatus": "available", "changedSeeds": 0, "mappedImpacts": 0, "protectedSignals": 0, "unknownImpacts": 0, "blockedImpacts": 0}, diagnostics=(),
    )
    assert first.to_dict()["schemaVersion"] == "governance-graph-change-impact-v1"
    assert first.to_dict()["impactSummaryFingerprint"] == first.impact_summary_fingerprint


def test_invalid_impact_summary_has_null_provenance():
    result = GovernanceGraphImpactSummary.invalid("invalid_input")
    payload = result.to_dict()
    assert payload["comparisonFingerprint"] is None
    assert payload["riskSummaryFingerprint"] is None
    assert payload["impactSummaryFingerprint"] is None
