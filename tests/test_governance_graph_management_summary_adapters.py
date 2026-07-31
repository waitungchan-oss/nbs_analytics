from __future__ import annotations

import hashlib

import pytest

from backend.agents.governance_graph_management_summary_adapters import (
    SourceCoverage,
    adapt_d1_coverage,
    adapt_d2_coverage,
    adapt_d3_coverage,
    adapt_d4_coverage,
    adapt_e1_coverage,
    adapt_e3_coverage,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_d1_unavailable_is_optional():
    result = adapt_d1_coverage(None, sha("snapshot"))
    assert result.status == "unavailable"
    assert result.required is False


def test_d1_wrong_snapshot_is_stale():
    result = adapt_d1_coverage({"schemaVersion": "governance-graph-query-v1", "status": "available", "runId": "run-1", "queryFingerprint": sha("query"), "snapshotFingerprint": sha("other")}, sha("snapshot"))
    assert result.status == "stale"


def test_d2_requires_fresh_left_and_right_references():
    source = {"schemaVersion": "governance-graph-comparison-v1", "status": "available", "leftSnapshot": {"graphFingerprint": sha("left"), "freshness": "fresh"}, "rightSnapshot": {"graphFingerprint": sha("right"), "freshness": "fresh"}, "comparisonFingerprint": sha("comparison"), "summary": {}}
    result = adapt_d2_coverage(source, sha("right"))
    assert result.status == "available"


def test_d3_partial_does_not_become_complete():
    source = {"schemaVersion": "governance-graph-risk-summary-v1", "status": "available", "comparisonFingerprint": sha("comparison"), "riskSummaryFingerprint": sha("risk"), "coverage": {"observedChanges": 2, "classifiedChanges": 1, "unknownChanges": 1, "invalidChanges": 0, "blockedChanges": 0}}
    assert adapt_d3_coverage(source).status == "partial"


def test_d4_maps_existing_coverage_status():
    source = {"schemaVersion": "governance-graph-change-impact-v1", "status": "available", "comparisonFingerprint": sha("comparison"), "riskSummaryFingerprint": sha("risk"), "impactSummaryFingerprint": sha("impact"), "coverage": {"coverageStatus": "unknown"}}
    assert adapt_d4_coverage(source).status == "unknown"


def test_e1_and_e3_require_snapshot_binding():
    snapshot = sha("snapshot")
    assert adapt_e1_coverage({"schemaVersion": "governance-graph-evidence-lineage-v1", "status": "available", "snapshotFingerprint": snapshot, "lineageFingerprint": sha("lineage"), "evidence": []}, snapshot).status == "partial"
    assert adapt_e3_coverage({"schemaVersion": "governance-graph-owner-dependency-read-v1", "status": "available", "snapshotFingerprint": snapshot, "readModelFingerprint": sha("read"), "coverage": {"ownerStatus": "available", "dependencyStatus": "available"}}, snapshot).status == "available"
