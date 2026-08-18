from __future__ import annotations

import json

from backend.agents.context_agent_service import build_context_evidence_payload
from backend.agents.evidence_models import EvidenceBundle, EvidenceItem
from scripts import context_agent


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "integration", "objective": "inspect context", "scope": [], "forbidden": []},
        repository={"branch": "main", "head": "a" * 40, "dirtyFiles": []},
        guardrails={"baseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/context.md", content="context"),),
    )


def test_collect_only_memory_enrichment_preserves_canonical_fingerprint(monkeypatch):
    hints = {
        "schemaVersion": "memory-hints-v1", "queryFingerprint": "a" * 64, "status": "empty", "hints": [],
        "limits": {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800}, "hintsFingerprint": "" * 64,
    }
    # Use a valid model envelope produced by the adapter contract rather than
    # allowing the test to bypass Context Agent validation.
    from backend.agents.memory_sidecar_hint_models import MemoryHints
    hints = MemoryHints.empty(query_fingerprint="a" * 64, status="empty").to_dict()
    monkeypatch.setattr(context_agent, "query_context_memory", lambda **kwargs: {"status": "empty", "reason": "empty", "memoryHints": hints})
    base = build_context_evidence_payload(_bundle())
    enriched = build_context_evidence_payload(_bundle(), memory_hints=hints)
    assert enriched["bundleFingerprint"] == base["bundleFingerprint"]
    assert enriched["memoryHints"]["authority"] == "non_authoritative_memory"
    assert enriched["memoryHints"]["status"] == "ignored"


def test_non_ready_collect_result_is_canonical_only(monkeypatch):
    from backend.agents.memory_sidecar_hint_models import MemoryHints
    hints = MemoryHints.empty(query_fingerprint="a" * 64, status="degraded").to_dict()
    monkeypatch.setattr(context_agent, "query_context_memory", lambda **kwargs: {"status": "blocked", "reason": "provider_unavailable", "memoryHints": hints})
    result = context_agent._collect_memory_hints("governance")
    assert result["status"] == "blocked"
    payload = build_context_evidence_payload(_bundle(), memory_hints=None)
    assert "memoryHints" not in payload


def test_fixed_collect_query_uses_context_identity_and_bounds(monkeypatch):
    seen = {}
    def fake_query(**kwargs):
        seen.update(kwargs)
        from backend.agents.memory_sidecar_hint_models import MemoryHints
        return {"status": "blocked", "reason": "provider_unavailable", "memoryHints": MemoryHints.empty(query_fingerprint=kwargs["query"].query_fingerprint, status="degraded").to_dict()}
    monkeypatch.setattr(context_agent, "query_context_memory", fake_query)
    result = context_agent._collect_memory_hints("governance evidence")
    assert result["status"] == "blocked"
    assert seen["identity"].project_id == "nbs_analytics"
    assert seen["identity"].consumer_id == "context-agent"
    assert seen["query"].max_items == 3
    assert seen["query"].max_bytes == 6000
    assert seen["query"].timeout_ms == 800
    assert seen["query"].memory_kinds == ("evidence", "governance", "skill")
