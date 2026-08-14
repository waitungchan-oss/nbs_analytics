from __future__ import annotations

import pytest

from backend.agents.memory_hub_models import (
    MemoryACLDecision,
    MemoryHubSchemaError,
    MemoryQuery,
    MemoryQueryResult,
    MemoryRecord,
    MemorySource,
    RuntimeIdentity,
)


def _source(*, kind: str = "governance_document", artifact_ref: str = "docs/spec.md") -> MemorySource:
    return MemorySource.from_parts(
        source_kind=kind,
        artifact_ref=artifact_ref,
        artifact_sha256="a" * 64,
        run_id="run-1" if kind != "governance_document" else None,
        git_head="b" * 40 if kind != "governance_document" else None,
        scope="project",
        owner="governance",
        status="verified",
        generated_at="2026-08-14T00:00:00+00:00",
        expires_at="2026-11-12T00:00:00+00:00",
        policy_version="memory-freshness-v1",
    )


def test_source_identity_is_derived_and_round_trips_exactly():
    source = _source()
    assert len(source.source_id) == 64
    assert source.source_id == MemorySource.from_dict(source.to_dict()).source_id
    assert source.source_fingerprint == MemorySource.from_dict(source.to_dict()).source_fingerprint


def test_source_rejects_tampered_derived_identity_fields():
    payload = _source().to_dict()
    payload["sourceId"] = "f" * 64
    with pytest.raises(MemoryHubSchemaError):
        MemorySource.from_dict(payload)
    payload = _source().to_dict()
    payload["sourceFingerprint"] = "f" * 64
    with pytest.raises(MemoryHubSchemaError):
        MemorySource.from_dict(payload)


def test_source_allows_only_three_canonical_kinds():
    for kind in ("governance_document", "verified_evidence", "approved_skill"):
        source = _source(kind=kind, artifact_ref=f"{kind}.json")
        assert source.source_kind == kind
    with pytest.raises(MemoryHubSchemaError):
        _source(kind="conversation")


def test_source_rejects_unsafe_paths_and_unapproved_artifacts():
    for path in ("/tmp/secret.md", "../secret.md", "data.sqlite", "rows.csv", ".env", "logs/run.log", "credentials/key.json"):
        with pytest.raises(MemoryHubSchemaError):
            _source(artifact_ref=path)


def test_verified_evidence_requires_completed_run_and_git_head():
    with pytest.raises(MemoryHubSchemaError):
        MemorySource.from_parts(
            source_kind="verified_evidence", artifact_ref="evidence.json", artifact_sha256="a" * 64,
            run_id=None, git_head=None, scope="project", owner="review",
            status="verified", generated_at="2026-08-14T00:00:00+00:00",
            expires_at="2026-11-12T00:00:00+00:00", policy_version="memory-freshness-v1",
        )


def test_record_fingerprint_is_rederived_from_source_and_summary():
    record = MemoryRecord.from_parts(
        memory_kind="governance", summary="Use canonical evidence first.", source_refs=(_source(),),
        scope="project", owner="governance", freshness="fresh", status="ready",
    )
    payload = record.to_dict()
    assert record.memory_id == MemoryRecord.from_dict(payload, {record.source_refs[0].source_id: record.source_refs[0]}).memory_id
    payload["summary"] = "tampered"
    with pytest.raises(MemoryHubSchemaError):
        MemoryRecord.from_dict(payload, {record.source_refs[0].source_id: record.source_refs[0]})


def test_query_and_identity_are_bounded_and_fingerprint_bound():
    identity = RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a", team_id="team-a")
    query = MemoryQuery.from_parts(query="canonical evidence", consumer_id=identity.consumer_id, scope="project", memory_kinds=("governance",))
    assert query.query_fingerprint == MemoryQuery.from_dict(query.to_dict()).query_fingerprint
    assert identity.project_id == "nbs_analytics"
    with pytest.raises(MemoryHubSchemaError):
        MemoryQuery.from_parts(query="x" * 513, consumer_id="agent-a", scope="project", memory_kinds=("governance",))


def test_models_reject_extra_keys():
    payload = _source().to_dict()
    payload["extra"] = True
    with pytest.raises(MemoryHubSchemaError):
        MemorySource.from_dict(payload)


def test_acl_decision_and_query_result_are_strict_contracts():
    decision = MemoryACLDecision.from_parts(
        consumer_id="agent-a", requested_scope="project", record_scope="project",
        decision="allow", reason="same_project",
    )
    assert MemoryACLDecision.from_dict(decision.to_dict()).to_dict() == decision.to_dict()
    result = MemoryQueryResult.from_parts(
        query_fingerprint="c" * 64, status="empty", records=(), acl_decisions=(decision,)
    )
    assert MemoryQueryResult.from_dict(result.to_dict()).to_dict() == result.to_dict()
    tampered = decision.to_dict()
    tampered["decisionFingerprint"] = "f" * 64
    with pytest.raises(MemoryHubSchemaError):
        MemoryACLDecision.from_dict(tampered)
