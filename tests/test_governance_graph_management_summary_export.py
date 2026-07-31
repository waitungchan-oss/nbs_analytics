from __future__ import annotations

import hashlib

from backend.agents.governance_graph_management_summary_export import serialize_management_summary_export
from backend.agents.governance_graph_management_summary_service import GovernanceGraphManagementSummaryService


def test_export_preserves_summary_fingerprint_and_selected_preset():
    summary = GovernanceGraphManagementSummaryService.compose(snapshot_fingerprint=hashlib.sha256(b"snapshot").hexdigest(), query=None, comparison=None, risk=None, impact=None, lineage=None, catalog=None)
    result = serialize_management_summary_export(summary, "owner_dependency_gaps")
    assert result["selectedPresetId"] == "owner_dependency_gaps"
    assert result["summaryFingerprint"] == result["summary"]["summaryFingerprint"]
    assert result["originalSummaryFingerprint"] == summary["summaryFingerprint"]
    assert result["originalSummaryFingerprint"] == summary["summaryFingerprint"]
    assert len(result["exportFingerprint"]) == 64


def test_preset_export_contains_filtered_view_and_original_provenance():
    summary = GovernanceGraphManagementSummaryService.compose(
        snapshot_fingerprint=hashlib.sha256(b"snapshot").hexdigest(),
        query=None,
        comparison=None,
        risk=None,
        impact=None,
        lineage=None,
        catalog=None,
    )
    original_fingerprint = summary["summaryFingerprint"]
    result = serialize_management_summary_export(summary, "owner_dependency_gaps")
    assert result["originalSummaryFingerprint"] == original_fingerprint
    assert result["summaryFingerprint"] == result["summary"]["summaryFingerprint"]
    assert result["summary"]["summaryFingerprint"] != original_fingerprint
    assert all(item["category"] == "catalog_coverage_gap" for item in result["summary"]["attentionItems"])
