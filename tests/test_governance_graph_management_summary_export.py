from __future__ import annotations

import hashlib

from backend.agents.governance_graph_management_summary_export import serialize_management_summary_export
from backend.agents.governance_graph_management_summary_service import GovernanceGraphManagementSummaryService


def test_export_preserves_summary_fingerprint_and_selected_preset():
    summary = GovernanceGraphManagementSummaryService.compose(snapshot_fingerprint=hashlib.sha256(b"snapshot").hexdigest(), query=None, comparison=None, risk=None, impact=None, lineage=None, catalog=None)
    result = serialize_management_summary_export(summary, "owner_dependency_gaps")
    assert result["selectedPresetId"] == "owner_dependency_gaps"
    assert result["summaryFingerprint"] == summary["summaryFingerprint"]
    assert len(result["exportFingerprint"]) == 64
