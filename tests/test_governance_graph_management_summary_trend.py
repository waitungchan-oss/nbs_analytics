from __future__ import annotations

import hashlib

from backend.agents.governance_graph_management_summary_service import GovernanceGraphManagementSummaryService


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_trend_requires_two_comparable_snapshots_and_preserves_order():
    first = GovernanceGraphManagementSummaryService.compose(snapshot_fingerprint=sha("a"), query=None, comparison=None, risk=None, impact=None, lineage=None, catalog=None)
    second = GovernanceGraphManagementSummaryService.compose(snapshot_fingerprint=sha("b"), query=None, comparison=None, risk=None, impact=None, lineage=None, catalog=None)
    snapshots = [{"schemaVersion": "governance-graph-management-summary-v1", "managementPolicyVersion": "e4-management-summary-v1", "snapshotFamily": "run-family", "snapshotFingerprint": first["snapshotFingerprint"], "summaryFingerprint": first["summaryFingerprint"], "overallRiskLevel": "R1", "attentionCount": 1, "unknownCount": 0, "headline": first["headline"], "summary": first}, {"schemaVersion": "governance-graph-management-summary-v1", "managementPolicyVersion": "e4-management-summary-v1", "snapshotFamily": "run-family", "snapshotFingerprint": second["snapshotFingerprint"], "summaryFingerprint": second["summaryFingerprint"], "overallRiskLevel": "R0", "attentionCount": 0, "unknownCount": 0, "headline": second["headline"], "summary": second}]
    result = GovernanceGraphManagementSummaryService.build_trend(snapshots)
    assert result["status"] == "available"
    assert [item["snapshotFingerprint"] for item in result["observations"]] == [sha("a"), sha("b")]


def test_trend_rejects_duplicate_fingerprint():
    summary = GovernanceGraphManagementSummaryService.compose(snapshot_fingerprint=sha("a"), query=None, comparison=None, risk=None, impact=None, lineage=None, catalog=None)
    item = {"schemaVersion": "governance-graph-management-summary-v1", "managementPolicyVersion": "e4-management-summary-v1", "snapshotFamily": "run-family", "snapshotFingerprint": summary["snapshotFingerprint"], "summaryFingerprint": summary["summaryFingerprint"], "overallRiskLevel": "R1", "attentionCount": 0, "unknownCount": 0, "headline": summary["headline"], "summary": summary}
    assert GovernanceGraphManagementSummaryService.build_trend([item, dict(item)])["status"] == "invalid"
