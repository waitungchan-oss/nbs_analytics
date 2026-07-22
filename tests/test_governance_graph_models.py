from __future__ import annotations

import hashlib
import json

import pytest

from backend.agents.governance_graph_models import (
    GATE_SCHEMA,
    GRAPH_SCHEMA,
    RISK_SCHEMA,
    GovernanceGraphSchemaError,
    GovernanceEvidenceRef,
    GovernanceGraphNode,
    GovernanceGraphSnapshot,
    GovernanceGate,
    GovernanceRisk,
)


def _valid_snapshot() -> dict:
    payload = {
        "schemaVersion": GRAPH_SCHEMA,
        "runId": "run-123",
        "generatedAt": "2026-07-22T10:00:00+00:00",
        "graphFingerprint": "0" * 64,
        "risk": None,
        "authorizationMode": "per_task",
        "overallStatus": "awaiting_authorization",
        "nodes": [
            {
                "nodeId": "risk",
                "nodeType": "risk",
                "status": "not_started",
                "attempt": 0,
                "maxAttempts": 1,
                "evidenceRefs": [],
                "fingerprint": "0" * 64,
                "reasonCode": None,
            }
        ],
        "allowedNextNodes": ["risk"],
        "blockers": [],
        "freshness": {},
        "diagnostics": [],
    }
    canonical = dict(payload)
    canonical.pop("graphFingerprint")
    payload["graphFingerprint"] = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return payload


def test_graph_snapshot_round_trips_with_stable_fingerprint():
    snapshot = GovernanceGraphSnapshot.from_dict(_valid_snapshot())

    assert snapshot.to_dict()["schemaVersion"] == GRAPH_SCHEMA
    assert snapshot.graph_fingerprint == snapshot.canonical_fingerprint
    assert GovernanceGraphSnapshot.from_dict(snapshot.to_dict()) == snapshot


@pytest.mark.parametrize(
    "field,value",
    [("schemaVersion", "unknown-v1"), ("runId", "../escape"), ("authorizationMode", "automatic")],
)
def test_graph_snapshot_rejects_invalid_contract(field, value):
    payload = _valid_snapshot()
    payload[field] = value

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


def test_graph_snapshot_rejects_fingerprint_and_unknown_next_node():
    payload = _valid_snapshot()
    payload["allowedNextNodes"] = ["missing"]

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)

    payload = _valid_snapshot()
    payload["graphFingerprint"] = "1" * 64
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


@pytest.mark.parametrize("field", ["risk", "nodes"])
def test_graph_snapshot_rejects_malformed_nested_evidence_refs(field):
    payload = _valid_snapshot()
    if field == "risk":
        payload["risk"] = {
            "schemaVersion": RISK_SCHEMA,
            "level": "R1",
            "surfaces": [],
            "evidenceRefs": None,
        }
    else:
        payload["nodes"][0]["evidenceRefs"] = None

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


def test_graph_snapshot_rejects_invalid_timestamp_as_graph_schema_error():
    payload = _valid_snapshot()
    payload["generatedAt"] = "not-a-timestamp"

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


def test_evidence_ref_rejects_governance_graph_projection_artifact():
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceEvidenceRef.from_dict(
            {
                "schemaVersion": "nbs-governance-evidence-ref-v1",
                "path": "governance-graph.json",
                "sha256": "0" * 64,
                "status": "passed",
                "generatedAt": "2026-07-22T10:00:00+00:00",
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("freshness", {"workspace": "/Users/chanwaitung2025/Downloads/nbs_analytics"}),
        ("blockers", [{"kind": "runner", "value": "python -m pytest"}]),
        ("diagnostics", [{"kind": "log", "value": "line 1\nline 2"}]),
    ],
)
def test_graph_snapshot_rejects_unsafe_metadata_content(field, value):
    payload = _valid_snapshot()
    payload[field] = value

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


@pytest.mark.parametrize("field", ["nodeId", "nodeType", "gateId"])
def test_graph_snapshot_and_nested_gates_reject_runner_like_identifiers(field):
    payload = _valid_snapshot()
    if field == "gateId":
        gate = {
            "schemaVersion": GATE_SCHEMA,
            "gateId": "python -m pytest tests/secret_prompt.txt",
            "status": "passed",
            "fingerprint": "0" * 64,
            "evidenceRefs": [],
            "reasonCode": None,
        }
        with pytest.raises(GovernanceGraphSchemaError):
            GovernanceGate.from_dict(gate)
        return
    payload["nodes"][0][field] = "python -m pytest tests/secret_prompt.txt"

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


def test_graph_snapshot_rejects_too_many_nodes_or_evidence_refs():
    payload = _valid_snapshot()
    payload["nodes"] = [
        {
            "nodeId": f"node{i}",
            "nodeType": "risk",
            "status": "not_started",
            "attempt": 0,
            "maxAttempts": 1,
            "evidenceRefs": [],
            "fingerprint": "0" * 64,
            "reasonCode": None,
        }
        for i in range(13)
    ]

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)

    payload = _valid_snapshot()
    payload["nodes"][0]["evidenceRefs"] = [
        {
            "schemaVersion": "nbs-governance-evidence-ref-v1",
            "path": "context.json",
            "sha256": "0" * 64,
            "status": "passed",
            "generatedAt": "2026-07-22T10:00:00+00:00",
        }
        for _ in range(13)
    ]

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)


def test_direct_construction_enforces_invariants():
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot(
            schema_version=GRAPH_SCHEMA,
            run_id="run-123",
            generated_at="2026-07-22T10:00:00+00:00",
            graph_fingerprint="1" * 64,
            risk=None,
            authorization_mode="per_task",
            overall_status="awaiting_authorization",
            nodes=(),
            allowed_next_nodes=(),
            blockers=(),
            freshness={},
            diagnostics=(),
        )

    evidence = GovernanceEvidenceRef(
        schema_version="nbs-governance-evidence-ref-v1",
        path="context.json",
        sha256="0" * 64,
        status="passed",
        generated_at="2026-07-22T10:00:00+00:00",
    )
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphNode(
            node_id="python -m pytest",
            node_type="risk",
            status="not_started",
            attempt=0,
            max_attempts=1,
            evidence_refs=(evidence,),
            fingerprint="0" * 64,
            reason_code=None,
        )


def test_direct_snapshot_construction_rejects_wrong_nested_member_types():
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot(
            schema_version=GRAPH_SCHEMA,
            run_id="run-123",
            generated_at="2026-07-22T10:00:00+00:00",
            graph_fingerprint="0" * 64,
            risk=None,
            authorization_mode="per_task",
            overall_status="awaiting_authorization",
            nodes=({"nodeId": "risk"},),
            allowed_next_nodes=("risk",),
            blockers=(),
            freshness={},
            diagnostics=(),
        )

    node = GovernanceGraphNode(
        node_id="risk",
        node_type="risk",
        status="not_started",
        attempt=0,
        max_attempts=1,
        evidence_refs=(),
        fingerprint="0" * 64,
        reason_code=None,
    )
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot(
            schema_version=GRAPH_SCHEMA,
            run_id="run-123",
            generated_at="2026-07-22T10:00:00+00:00",
            graph_fingerprint="0" * 64,
            risk="R1",
            authorization_mode="per_task",
            overall_status="awaiting_authorization",
            nodes=(node,),
            allowed_next_nodes=("risk",),
            blockers=(),
            freshness={},
            diagnostics=(),
        )


def test_direct_nested_construction_rejects_wrong_evidence_ref_types():
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceRisk(
            schema_version=RISK_SCHEMA,
            level="R1",
            surfaces=(),
            evidence_refs=({"path": "context.json"},),
        )

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGate(
            schema_version=GATE_SCHEMA,
            gate_id="spec_gate",
            status="passed",
            fingerprint="0" * 64,
            evidence_refs=({"path": "context.json"},),
            reason_code=None,
        )

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphNode(
            node_id="risk",
            node_type="risk",
            status="not_started",
            attempt=0,
            max_attempts=1,
            evidence_refs=({"path": "context.json"},),
            fingerprint="0" * 64,
            reason_code=None,
        )


def test_nested_risk_and_gate_require_exact_schema_versions():
    risk = {"schemaVersion": "wrong", "level": "R1", "surfaces": [], "evidenceRefs": []}
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceRisk.from_dict(risk)

    gate = {
        "schemaVersion": "wrong",
        "gateId": "spec_gate",
        "status": "passed",
        "fingerprint": "0" * 64,
        "evidenceRefs": [],
        "reasonCode": None,
    }
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGate.from_dict(gate)

    assert RISK_SCHEMA != GATE_SCHEMA


@pytest.mark.parametrize(
    "field,value",
    [
        ("freshness", {"unexpected": "ready"}),
        ("freshness", {"status": "sk-proj-secret-token"}),
        ("blockers", [{"code": "ghp_secret_token"}]),
        ("diagnostics", [{"code": "runner", "summary": "sk-live-secret"}]),
    ],
)
def test_graph_snapshot_rejects_unknown_or_secret_metadata(field, value):
    payload = _valid_snapshot()
    payload[field] = value

    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)
