from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.agents.memory_hub_integration_models import (
    MemoryHubIntegrationEvidence,
    build_memory_hub_integration_evidence,
)


def test_builds_bounded_non_authoritative_evidence() -> None:
    evidence = build_memory_hub_integration_evidence(
        project_id="nbs_analytics",
        consumer_id="context-agent",
        integration_mode="direct_query",
        status="ready",
        reason="enriched",
        query_fingerprint="a" * 64,
        hints_fingerprint="b" * 64,
        policy_decision_fingerprints=("c" * 64,),
        source_refs=("runs/context/memory-hints.json",),
        hint_count=1,
        generated_at=datetime(2026, 8, 18, tzinfo=timezone.utc).isoformat(),
    )
    payload = evidence.to_dict()
    assert payload["schemaVersion"] == "memory-hub-agent-integration-v1"
    assert payload["authority"] == "non_authoritative_memory"
    assert payload["evidenceFingerprint"] == evidence.evidence_fingerprint


def test_rejects_raw_memory_and_absolute_source_refs() -> None:
    payload = {
        "schemaVersion": "memory-hub-agent-integration-v1",
        "projectId": "nbs_analytics",
        "consumerId": "context-agent",
        "integrationMode": "direct_query",
        "status": "ready",
        "reason": "enriched",
        "authority": "non_authoritative_memory",
        "queryFingerprint": None,
        "hintsFingerprint": None,
        "policyDecisionFingerprints": [],
        "sourceRefs": ["/tmp/raw-memory.json"],
        "hintCount": 0,
        "generatedAt": "2026-08-18T00:00:00+00:00",
        "evidenceFingerprint": "d" * 64,
        "rawMemory": "must not pass",
    }
    with pytest.raises(ValueError):
        MemoryHubIntegrationEvidence.from_dict(payload)


def test_rejects_unknown_mode_and_over_cap() -> None:
    with pytest.raises(ValueError):
        build_memory_hub_integration_evidence(
            project_id="nbs_analytics", consumer_id="context-agent", integration_mode="free_query",
            status="ready", reason="enriched", query_fingerprint=None, hints_fingerprint=None,
            policy_decision_fingerprints=(), source_refs=(), hint_count=0,
            generated_at="2026-08-18T00:00:00+00:00",
        )
    with pytest.raises(ValueError):
        build_memory_hub_integration_evidence(
            project_id="nbs_analytics", consumer_id="context-agent", integration_mode="direct_query",
            status="ready", reason="enriched", query_fingerprint=None, hints_fingerprint=None,
            policy_decision_fingerprints=(), source_refs=(), hint_count=4,
            generated_at="2026-08-18T00:00:00+00:00",
        )
