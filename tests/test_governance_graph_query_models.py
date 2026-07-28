from __future__ import annotations

import pytest

from backend.agents.governance_graph_query_models import (
    GovernanceGraphQuery,
    GovernanceGraphQueryResult,
    GovernanceGraphQuerySchemaError,
)


def _identity(**overrides):
    value = {
        "runId": "run-1",
        "graphFingerprint": "a" * 64,
        "generatedAt": "2026-07-28T00:00:00+00:00",
        "freshness": "fresh",
    }
    value.update(overrides)
    return value


def _result(*, filters=None, diagnostics=None):
    return GovernanceGraphQueryResult.from_parts(
        status="available",
        snapshot_identity=_identity(),
        filters=filters or {},
        matched_nodes=(),
        matched_edges=(),
        evidence_refs=(),
        unknown_count=0,
        invalid_count=0,
        blocked_count=0,
        diagnostics=diagnostics or (),
    )


def test_query_normalizes_exact_filters_and_rejects_unknown_keys():
    query = GovernanceGraphQuery.from_dict({"nodeType": "task_gate", "nodeStatus": "blocked"})

    assert query.normalized() == {"nodeStatus": "blocked", "nodeType": "task_gate"}
    with pytest.raises(GovernanceGraphQuerySchemaError):
        GovernanceGraphQuery.from_dict({"freeText": "risk"})


def test_query_supports_all_exact_filter_dimensions():
    query = GovernanceGraphQuery.from_dict({
        "runId": "run-1",
        "nodeType": "task_gate",
        "nodeStatus": "invalid",
        "nodeId": "task_gate",
        "edgeType": "derived_from",
        "artifactKind": "task_gate",
        "evidenceStatus": "invalid",
        "snapshotFingerprint": "b" * 64,
    })

    assert query.normalized() == {
        "artifactKind": "task_gate",
        "edgeType": "derived_from",
        "evidenceStatus": "invalid",
        "nodeId": "task_gate",
        "nodeStatus": "invalid",
        "nodeType": "task_gate",
        "runId": "run-1",
        "snapshotFingerprint": "b" * 64,
    }


@pytest.mark.parametrize("key,value", [
    ("nodeType", "made_up"),
    ("edgeType", "made_up"),
    ("artifactKind", "made_up"),
])
def test_query_rejects_unknown_enums(key, value):
    with pytest.raises(GovernanceGraphQuerySchemaError):
        GovernanceGraphQuery.from_dict({key: value})


def test_result_fingerprint_is_reproducible_and_changes_with_filters():
    first = _result(filters={"nodeType": "task_gate"})
    second = _result(filters={"nodeType": "task_gate"})
    changed = _result(filters={"nodeType": "plan_gate"})

    assert first.query_fingerprint == second.query_fingerprint
    assert first.query_fingerprint != changed.query_fingerprint


def test_result_rejects_raw_or_absolute_path_metadata():
    with pytest.raises(GovernanceGraphQuerySchemaError):
        _result(diagnostics=({"code": "bad", "path": "/private/raw.json"},))


def test_result_allows_run_relative_evidence_basename():
    result = GovernanceGraphQueryResult.from_parts(
        status="available",
        snapshot_identity=_identity(),
        filters={},
        matched_nodes=(),
        matched_edges=(),
        evidence_refs=({
            "schemaVersion": "nbs-governance-evidence-ref-v1",
            "path": "task-gate.json",
            "sha256": "c" * 64,
            "status": "available",
            "finalizedAt": "2026-07-28T00:00:00+00:00",
        },),
        unknown_count=0,
        invalid_count=0,
        blocked_count=0,
        diagnostics=(),
    )
    assert result.to_dict()["evidenceRefs"][0]["path"] == "task-gate.json"


@pytest.mark.parametrize("status", ["available", "unavailable", "unknown", "invalid", "blocked"])
def test_result_accepts_fixed_statuses(status):
    result = GovernanceGraphQueryResult.from_parts(
        status=status,
        snapshot_identity=None,
        filters={},
        matched_nodes=(),
        matched_edges=(),
        evidence_refs=(),
        unknown_count=0,
        invalid_count=0,
        blocked_count=0,
        diagnostics=(),
    )
    assert result.to_dict()["status"] == status
