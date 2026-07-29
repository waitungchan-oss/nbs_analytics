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
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphSnapshotReference.from_dict({"runId": "run.v1"})


def test_comparison_fingerprint_is_order_sensitive_and_reproducible():
    first = _result(left_run_id="before", right_run_id="after")
    second = _result(left_run_id="before", right_run_id="after")
    reversed_result = _result(left_run_id="after", right_run_id="before")

    assert first.comparison_fingerprint == second.comparison_fingerprint
    assert first.comparison_fingerprint != reversed_result.comparison_fingerprint


def test_result_rejects_raw_or_absolute_metadata():
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        _result(diagnostics=({"code": "bad", "summary": "/private/raw.json"},))


def test_change_collections_are_sorted_before_fingerprinting():
    changes = (
        {"changeType": "added", "nodeId": "zeta", "before": None, "after": {"nodeId": "zeta"}},
        {"changeType": "added", "nodeId": "alpha", "before": None, "after": {"nodeId": "alpha"}},
    )
    kwargs = {
        "status": "available",
        "left_reference": _reference("before"),
        "right_reference": _reference("after"),
        "left_snapshot": _identity("before"),
        "right_snapshot": _identity("after"),
        "summary": {
            "addedNodes": 2, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0,
            "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
            "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
        },
        "edge_changes": (), "evidence_changes": (), "diagnostics": (),
    }
    first = GovernanceGraphComparisonResult.from_parts(node_changes=changes, **kwargs)
    second = GovernanceGraphComparisonResult.from_parts(node_changes=tuple(reversed(changes)), **kwargs)

    assert first.comparison_fingerprint == second.comparison_fingerprint
    assert first.to_dict()["nodeChanges"][0]["nodeId"] == "alpha"


def test_available_result_requires_both_snapshot_identities():
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_parts(
            status="available",
            left_reference=_reference("before"),
            right_reference=_reference("after"),
            left_snapshot=None,
            right_snapshot=_identity("after"),
            summary={
                "addedNodes": 0, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0,
                "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
                "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
            },
            node_changes=(), edge_changes=(), evidence_changes=(), diagnostics=(),
        )


def test_summary_counts_must_match_change_records():
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_parts(
            status="available",
            left_reference=_reference("before"),
            right_reference=_reference("after"),
            left_snapshot=_identity("before"),
            right_snapshot=_identity("after"),
            summary={
                "addedNodes": 0, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0,
                "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
                "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
            },
            node_changes=({"changeType": "added", "nodeId": "task_gate", "before": None, "after": {}},),
            edge_changes=(), evidence_changes=(), diagnostics=(),
        )


def test_duplicate_change_identity_is_rejected():
    change = {"changeType": "added", "nodeId": "task_gate", "before": None, "after": {}}
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_parts(
            status="available",
            left_reference=_reference("before"),
            right_reference=_reference("after"),
            left_snapshot=_identity("before"),
            right_snapshot=_identity("after"),
            summary={
                "addedNodes": 2, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0,
                "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
                "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
            },
            node_changes=(change, dict(change)), edge_changes=(), evidence_changes=(), diagnostics=(),
        )


def test_same_node_identity_with_conflicting_change_type_is_rejected():
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_parts(
            status="available",
            left_reference=_reference("before"),
            right_reference=_reference("after"),
            left_snapshot=_identity("before"),
            right_snapshot=_identity("after"),
            summary={
                "addedNodes": 1, "removedNodes": 1, "changedNodes": 0, "unchangedNodes": 0,
                "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
                "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
            },
            node_changes=(
                {"changeType": "added", "nodeId": "task_gate", "before": None, "after": {}},
                {"changeType": "removed", "nodeId": "task_gate", "before": {}, "after": None},
            ),
            edge_changes=(), evidence_changes=(), diagnostics=(),
        )


@pytest.mark.parametrize(
    ("changes", "summary_key", "identity_fields"),
    [
        (
            (
                {"changeType": "added", "source": "risk", "target": "spec_gate", "type": "requires", "before": None, "after": {}},
                {"changeType": "removed", "source": "risk", "target": "spec_gate", "type": "requires", "before": {}, "after": None},
            ),
            ("addedEdges", "removedEdges"),
            ("edge",),
        ),
        (
            (
                {"changeType": "added", "path": "task-gate.json", "sha256": "a" * 64, "before": None, "after": {}},
                {"changeType": "removed", "path": "task-gate.json", "sha256": "a" * 64, "before": {}, "after": None},
            ),
            ("addedEvidenceRefs", "removedEvidenceRefs"),
            ("evidence",),
        ),
    ],
)
def test_edge_and_evidence_identity_conflicts_are_rejected(changes, summary_key, identity_fields):
    del identity_fields
    summary = {
        "addedNodes": 0, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0,
        "addedEdges": 0, "removedEdges": 0, "changedEdges": 0,
        "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0,
    }
    summary[summary_key[0]] = 1
    summary[summary_key[1]] = 1
    kwargs = {
        "status": "available",
        "left_reference": _reference("before"),
        "right_reference": _reference("after"),
        "left_snapshot": _identity("before"),
        "right_snapshot": _identity("after"),
        "summary": summary,
        "node_changes": (),
        "diagnostics": (),
    }
    if summary_key[0].endswith("Edges"):
        kwargs["edge_changes"] = changes
        kwargs["evidence_changes"] = ()
    else:
        kwargs["edge_changes"] = ()
        kwargs["evidence_changes"] = changes
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_parts(**kwargs)


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


def test_comparison_output_exposes_both_input_references():
    result = _result(left_run_id="run-left", right_run_id="run-right")

    payload = result.to_dict()

    assert payload["leftReference"] == {"runId": "run-left", "snapshotFingerprint": None}
    assert payload["rightReference"] == {"runId": "run-right", "snapshotFingerprint": None}


def test_comparison_from_dict_round_trips_public_contract():
    result = _result(left_run_id="run-left", right_run_id="run-right")

    restored = GovernanceGraphComparisonResult.from_dict(result.to_dict())

    assert restored.to_dict() == result.to_dict()


def test_comparison_from_dict_rejects_missing_reference():
    payload = _result().to_dict()
    payload.pop("rightReference")

    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_dict(payload)
