from __future__ import annotations

import hashlib

from backend.agents.governance_graph_management_summary_service import GovernanceGraphManagementSummaryService


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_optional_query_does_not_lower_overall_status():
    snapshot = sha("right")
    comparison = {"schemaVersion": "governance-graph-comparison-v1", "status": "available", "leftSnapshot": {"graphFingerprint": sha("left"), "freshness": "fresh"}, "rightSnapshot": {"graphFingerprint": snapshot, "freshness": "fresh"}, "comparisonFingerprint": sha("comparison"), "summary": {}}
    result = GovernanceGraphManagementSummaryService.compose(snapshot_fingerprint=snapshot, query=None, comparison=comparison, risk=None, impact=None, lineage=None, catalog=None)
    assert result["coverage"]["query"] == "unavailable"
    assert result["status"] in {"missing", "unknown"}

