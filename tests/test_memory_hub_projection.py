from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from backend.agents.memory_hub_models import MemoryQueryResult, MemoryRecord, MemorySource
from backend.agents.memory_hub_projection import project_memory_result
from backend.agents.context_agent_service import build_context_evidence_payload
from backend.agents.evidence_models import EvidenceBundle, EvidenceItem
from backend.agents.memory_sidecar_hint_models import MemoryHints
from backend.agents.memory_sidecar_models import MemorySidecarProviderMetadata


def _source(*, artifact_ref: str = "docs/guide.md") -> MemorySource:
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    content_sha = sha256(artifact_ref.encode()).hexdigest()
    return MemorySource.from_parts(
        source_kind="governance_document", artifact_ref=artifact_ref,
        artifact_sha256=content_sha, run_id=None, git_head=None,
        scope="project", owner="governance", status="verified",
        generated_at=now.isoformat(), expires_at=(now + timedelta(days=7)).isoformat(),
        policy_version="memory-hub-policy-v1",
    )


def _result(status: str = "ready", *, freshness: str = "fresh") -> MemoryQueryResult:
    source = _source()
    record = MemoryRecord.from_parts(
        memory_kind="governance", summary="Use canonical artifacts as the source of truth.",
        source_refs=(source,), scope="project", owner="governance", freshness=freshness, status="ready",
    )
    return MemoryQueryResult.from_parts(
        query_fingerprint="a" * 64, status=status, records=(record,) if status == "ready" else (), acl_decisions=(),
    )


def test_ready_projection_is_non_authoritative_and_preserves_evidence() -> None:
    projected = project_memory_result(_result())
    assert projected is not None
    hints = projected
    assert hints.status == "ready"
    assert len(hints.hints) == 1
    assert hints.hints[0].source_refs == ("docs/guide.md",)
    assert hints.hints[0].source_fingerprints == (_source().artifact_sha256,)
    assert projected.to_dict()["schemaVersion"] == "memory-hints-v1"


def test_projection_is_accepted_by_existing_context_memory_hint_contract() -> None:
    bundle = EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "projection-task", "objective": "preserve authority", "scope": [], "forbidden": []},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/guide.md", content="canonical"),),
    )
    payload = build_context_evidence_payload(bundle, memory_hints=project_memory_result(_result()))
    assert payload["memoryHints"]["authority"] == "non_authoritative_memory"
    assert payload["memoryHints"]["status"] == "ready"
    assert payload["memoryHints"]["hints"][0]["memoryId"] == _result().records[0].memory_id
    assert payload["bundleFingerprint"] == build_context_evidence_payload(bundle)["bundleFingerprint"]


def test_ready_result_with_stale_record_fails_closed_to_degraded() -> None:
    projected = project_memory_result(_result(freshness="stale"))
    assert projected is not None
    assert projected.status == "degraded"
    assert projected.hints == ()


def test_projection_over_sidecar_byte_cap_fails_closed() -> None:
    source = _source()
    records = tuple(
        MemoryRecord.from_parts(
            memory_kind="governance", summary=("x" * 2048), source_refs=(source,),
            scope="project", owner="governance", freshness="fresh", status="ready",
        )
        for _ in range(3)
    )
    result = MemoryQueryResult.from_parts(query_fingerprint="a" * 64, status="ready", records=records, acl_decisions=())
    projected = project_memory_result(result)
    assert projected is not None
    assert projected.status == "degraded"
    assert projected.hints == ()


@pytest.mark.parametrize("status", ["empty", "timeout", "degraded", "blocked"])
def test_non_ready_result_never_injects_hints(status: str) -> None:
    projected = project_memory_result(_result(status))
    assert projected is not None
    assert projected.hints == ()


def test_missing_result_and_provider_defaults_are_safe() -> None:
    assert project_memory_result(None) is None
    metadata = MemorySidecarProviderMetadata()
    assert metadata.recall_enabled is False
    assert metadata.writer_enabled is False
    assert metadata.shadow_mode is True
