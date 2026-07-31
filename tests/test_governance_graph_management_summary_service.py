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


def test_invalid_source_produces_closed_diagnostic():
    result = GovernanceGraphManagementSummaryService.compose(
        snapshot_fingerprint=sha("snapshot"),
        query=None,
        comparison={"schemaVersion": "wrong", "status": "available"},
        risk=None,
        impact=None,
        lineage=None,
        catalog=None,
    )
    assert result["diagnostics"]
    assert all(item["code"] in {
        "source_schema_invalid", "source_fingerprint_invalid", "source_snapshot_missing",
        "source_snapshot_mismatch", "source_status_invalid", "source_binding_invalid",
        "source_payload_forbidden", "trend_envelope_invalid", "trend_fingerprint_mismatch",
        "preset_selection_invalid", "preset_snapshot_mismatch",
    } for item in result["diagnostics"])
