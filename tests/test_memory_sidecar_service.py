from __future__ import annotations

from pathlib import Path
from threading import Event
from time import sleep

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_adapter import FakeMemorySidecarProvider, MemorySidecarProviderError
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from backend.agents.memory_sidecar_policy import MemorySidecarPolicy
from backend.agents.memory_sidecar_service import MemorySidecarService


ROOT = Path(__file__).resolve().parents[1]


def _policy() -> MemorySidecarPolicy:
    return MemorySidecarPolicy.from_file(ROOT / "agent_config" / "memory_sidecar_policy.json")


def _policy_with(**changes: int) -> MemorySidecarPolicy:
    policy = _policy()
    values = {
        "schema_version": policy.schema_version,
        "max_items": policy.max_items,
        "max_bytes": policy.max_bytes,
        "timeout_ms": policy.timeout_ms,
        "summary_max_bytes": policy.summary_max_bytes,
        "ttl_days": policy.ttl_days,
        "allowed_kinds": policy.allowed_kinds,
        "denied_patterns": policy.denied_patterns,
    }
    values.update(changes)
    return MemorySidecarPolicy(**values)


def _hints(*, freshness: str = "fresh") -> MemoryHints:
    query_fingerprint = canonical_fingerprint({"query": "review runtime"})
    hint = MemoryHint(
        "a" * 64, "Use the focused Hermes pack.", ("review.json",), freshness, "high", ("b" * 64,)
    )
    return MemoryHints(query_fingerprint=query_fingerprint, status="ready", hints=(hint,))


def test_recall_ready_returns_bounded_non_authoritative_hints():
    hints = _hints()
    provider = FakeMemorySidecarProvider(recall_results={hints.query_fingerprint: hints})
    result = MemorySidecarService(_policy()).recall(query="review runtime", provider=provider)

    assert result.status == "ready"
    assert result.hints == hints.hints
    assert result.max_items == 3
    assert result.max_bytes == 6000
    assert provider.recall_calls == 1


def test_recall_timeout_returns_empty_non_blocking_result():
    provider = FakeMemorySidecarProvider(
        raise_error=MemorySidecarProviderError("timeout", "provider timed out")
    )
    result = MemorySidecarService(_policy()).recall(query="review runtime", provider=provider)

    assert result.status == "timeout"
    assert result.hints == ()
    assert provider.recall_calls == 1


def test_recall_degraded_provider_error_returns_empty_result():
    provider = FakeMemorySidecarProvider(
        raise_error=MemorySidecarProviderError("unavailable", "provider unavailable")
    )
    result = MemorySidecarService(_policy()).recall(query="review runtime", provider=provider)

    assert result.status == "degraded"
    assert result.hints == ()


def test_recall_rejects_stale_hints_without_injecting_them():
    hints = _hints(freshness="stale")
    provider = FakeMemorySidecarProvider(recall_results={hints.query_fingerprint: hints})

    result = MemorySidecarService(_policy()).recall(query="review runtime", provider=provider)

    assert result.status == "empty"
    assert result.hints == ()


def test_recall_enforces_reduced_policy_item_cap():
    hints = _hints()
    provider = FakeMemorySidecarProvider(recall_results={hints.query_fingerprint: hints})
    result = MemorySidecarService(_policy_with(max_items=1)).recall(query="review runtime", provider=provider)
    assert len(result.hints) == 1


def test_recall_enforces_reduced_policy_byte_cap():
    hints = _hints()
    provider = FakeMemorySidecarProvider(recall_results={hints.query_fingerprint: hints})
    result = MemorySidecarService(_policy_with(max_bytes=500)).recall(query="review runtime", provider=provider)
    assert result.status == "empty"
    assert result.hints == ()


def test_recall_returns_timeout_when_provider_blocks():
    provider_finished = Event()

    class BlockingProvider:
        def recall(self, *, query, query_fingerprint, limits):
            sleep(0.2)
            provider_finished.set()
            return _hints()

    result = MemorySidecarService(_policy_with(timeout_ms=20)).recall(query="review runtime", provider=BlockingProvider())
    assert result.status == "timeout"
    assert not provider_finished.is_set()


def test_recall_caps_concurrent_blocked_provider_calls():
    class BlockingProvider:
        def recall(self, *, query, query_fingerprint, limits):
            sleep(0.2)
            return _hints()

    service = MemorySidecarService(_policy_with(timeout_ms=20))
    first = service.recall(query="review runtime", provider=BlockingProvider())
    second = service.recall(query="review runtime", provider=BlockingProvider())
    assert first.status == "timeout"
    assert second.status == "timeout"


def test_recall_invalid_provider_payload_fails_open_as_degraded():
    class InvalidProvider:
        def recall(self, *, query, query_fingerprint, limits):
            return {"schemaVersion": "memory-hints-v1", "hints": "not-a-list"}

    result = MemorySidecarService(_policy()).recall(query="review runtime", provider=InvalidProvider())

    assert result.status == "degraded"
    assert result.hints == ()


def test_recall_query_fingerprint_mismatch_is_not_injected():
    hints = _hints()
    class MismatchProvider:
        def recall(self, *, query, query_fingerprint, limits):
            return hints

    result = MemorySidecarService(_policy()).recall(query="different query", provider=MismatchProvider())

    assert result.status == "degraded"
    assert result.hints == ()
