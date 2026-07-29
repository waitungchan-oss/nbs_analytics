from __future__ import annotations

import pytest

from backend.agents.governance_graph_comparison_models import (
    GovernanceGraphComparisonResult,
    GovernanceGraphComparisonSchemaError,
    GovernanceGraphSnapshotReference,
)


def _identity(run_id: str) -> dict[str, str]:
    return {
        "runId": run_id,
        "graphFingerprint": "a" * 64,
        "generatedAt": "2026-07-29T00:00:00+00:00",
        "freshness": "fresh",
    }


def _reference(run_id: str) -> GovernanceGraphSnapshotReference:
    return GovernanceGraphSnapshotReference.from_dict({"runId": run_id})


def _result(*, left_run_id: str = "before", right_run_id: str = "after", diagnostics=()):
    return GovernanceGraphComparisonResult.from_parts(
        status="available",
        left_reference=_reference(left_run_id),
        right_reference=_reference(right_run_id),
        left_snapshot=_identity(left_run_id),
        right_snapshot=_identity(right_run_id),
        summary={
            "addedNodes": 0,
            "removedNodes": 0,
            "changedNodes": 0,
            "unchangedNodes": 0,
            "addedEdges": 0,
            "removedEdges": 0,
            "changedEdges": 0,
            "addedEvidenceRefs": 0,
            "removedEvidenceRefs": 0,
            "changedEvidenceRefs": 0,
        },
        node_changes=(),
        edge_changes=(),
        evidence_changes=(),
        diagnostics=diagnostics,
    )


def test_reference_requires_safe_run_id_and_optional_sha256():
    reference = GovernanceGraphSnapshotReference.from_dict({"runId": "run-before"})

    assert reference.to_dict() == {"runId": "run-before", "snapshotFingerprint": None}
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphSnapshotReference.from_dict({"runId": "../escape"})


def test_comparison_fingerprint_is_order_sensitive_and_reproducible():
    first = _result(left_run_id="before", right_run_id="after")
    second = _result(left_run_id="before", right_run_id="after")
    reversed_result = _result(left_run_id="after", right_run_id="before")

    assert first.comparison_fingerprint == second.comparison_fingerprint
    assert first.comparison_fingerprint != reversed_result.comparison_fingerprint


def test_result_rejects_raw_or_absolute_metadata():
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        _result(diagnostics=({"code": "bad", "summary": "/private/raw.json"},))


def test_result_serializes_fixed_envelope_and_bounded_change():
    result = GovernanceGraphComparisonResult.from_parts(
        status="available",
        left_reference=_reference("before"),
        right_reference=_reference("after"),
        left_snapshot=_identity("before"),
        right_snapshot=_identity("after"),
        summary={
            "addedNodes": 1,
            "removedNodes": 0,
            "changedNodes": 0,
            "unchangedNodes": 0,
            "addedEdges": 0,
            "removedEdges": 0,
            "changedEdges": 0,
            "addedEvidenceRefs": 0,
            "removedEvidenceRefs": 0,
            "changedEvidenceRefs": 0,
        },
        node_changes=({
            "changeType": "added",
            "nodeId": "task_gate",
            "before": None,
            "after": {"nodeId": "task_gate", "status": "ready"},
        },),
        edge_changes=(),
        evidence_changes=(),
        diagnostics=(),
    )

    payload = result.to_dict()

    assert payload["schemaVersion"] == "governance-graph-comparison-v1"
    assert payload["nodeChanges"][0]["changeType"] == "added"
    assert payload["nodeChanges"][0]["before"] is None
