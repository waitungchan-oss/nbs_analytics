from __future__ import annotations

import hashlib

import pytest

from backend.agents.governance_graph_management_summary_models import (
    MANAGEMENT_SUMMARY_SCHEMA,
    MANAGEMENT_POLICY_VERSION,
    ManagementSummaryModelError,
    canonical_management_summary_payload,
    fingerprint_management_summary,
    validate_management_summary_payload,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _summary(**overrides):
    payload = {
        "schemaVersion": MANAGEMENT_SUMMARY_SCHEMA,
        "managementPolicyVersion": MANAGEMENT_POLICY_VERSION,
        "status": "available",
        "snapshotFingerprint": _sha("snapshot"),
        "summaryFingerprint": _sha("summary"),
        "overallRiskLevel": "R1",
        "headline": {
            "attentionStatus": "attention",
            "protectedCount": 1,
            "blockedCount": 0,
            "unknownCount": 0,
            "evidenceCoverage": "available",
            "ownerCoverage": "available",
            "dependencyCoverage": "available",
        },
        "risk": {"status": "available", "overallRiskLevel": "R1", "findingCount": 1,
                 "levels": {"R2": 0, "R1": 1, "R0": 0, "unknown": 0},
                 "sourceRef": {"kind": "d3_risk_summary", "identity": "risk-summary",
                                "fingerprint": _sha("risk"), "status": "available"}},
        "impact": {"status": "available", "observedCount": 1, "blockedCount": 0,
                   "unknownCount": 0, "categories": ["protected_governance_surface"],
                   "sourceRef": {"kind": "d4_change_impact", "identity": "impact-summary",
                                  "fingerprint": _sha("impact"), "status": "available"}},
        "coverage": {"query": "unavailable", "comparison": "available", "risk": "available",
                     "impact": "available", "lineage": "available", "catalog": "available"},
        "attentionItems": [{
            "attentionId": "protected_governance_surface:node:protected_incident:observed:D3-PROTECTED-SURFACE",
            "severity": "R2", "category": "protected_governance_surface", "state": "observed",
            "summaryCode": "protected_signal_requires_governance_review", "sourceRefs": [],
            "drillDown": {"kind": "node", "identity": "protected_incident"},
        }],
        "trend": {"status": "unknown", "basis": "insufficient_comparable_snapshots",
                  "observations": [], "changedDimensions": []},
        "presets": [{"presetId": "protected_surfaces", "labelCode": "protected_surfaces", "available": True}],
        "diagnostics": [],
        "sourceRefs": [],
    }
    payload.update(overrides)
    try:
        payload["summaryFingerprint"] = fingerprint_management_summary(payload)
    except ManagementSummaryModelError:
        payload["summaryFingerprint"] = _sha("invalid-summary")
    return payload


def test_valid_summary_has_exact_schema_and_deterministic_fingerprint():
    payload = _summary()
    validated = validate_management_summary_payload(payload)
    assert validated["schemaVersion"] == MANAGEMENT_SUMMARY_SCHEMA
    assert fingerprint_management_summary(payload) == fingerprint_management_summary(dict(reversed(payload.items())))
    assert canonical_management_summary_payload(payload)["attentionItems"] == payload["attentionItems"]


def test_unknown_top_level_key_is_rejected():
    payload = _summary(extraField="forbidden")
    with pytest.raises(ManagementSummaryModelError):
        validate_management_summary_payload(payload)


@pytest.mark.parametrize("field,value", [
    ("status", "complete"),
    ("overallRiskLevel", "R3"),
])
def test_closed_enums_are_rejected(field, value):
    with pytest.raises(ManagementSummaryModelError):
        validate_management_summary_payload(_summary(**{field: value}))


def test_source_ref_rejects_path_and_non_allowlisted_kind():
    payload = _summary(sourceRefs=[{"kind": "filesystem", "identity": "/tmp/x", "fingerprint": _sha("x"), "status": "available"}])
    with pytest.raises(ManagementSummaryModelError):
        validate_management_summary_payload(payload)


def test_attention_id_requires_source_identity_and_closed_drilldown_kind():
    payload = _summary(attentionItems=[{
        "attentionId": "protected_governance_surface:node:protected_incident:observed:none",
        "severity": "R2", "category": "protected_governance_surface", "state": "observed",
        "summaryCode": "protected_signal_requires_governance_review", "sourceRefs": [],
        "drillDown": {"kind": "filesystem", "identity": "x"},
    }])
    with pytest.raises(ManagementSummaryModelError):
        validate_management_summary_payload(payload)
