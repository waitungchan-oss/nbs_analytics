from __future__ import annotations

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_adapter import (
    FakeMemorySidecarProvider,
    MemorySidecarProviderError,
)
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints


def _hints(*, freshness: str = "fresh") -> MemoryHints:
    query_fingerprint = canonical_fingerprint({"query": "review runtime"})
    hint = MemoryHint(
        "a" * 64, "Use the focused Hermes pack.", ("review.json",), freshness, "high", ("b" * 64,)
    )
    return MemoryHints(query_fingerprint=query_fingerprint, status="ready", hints=(hint,))


def test_fake_provider_returns_deterministic_recall_result():
    hints = _hints()
    provider = FakeMemorySidecarProvider(recall_results={hints.query_fingerprint: hints})

    result = provider.recall(
        query="review runtime", query_fingerprint=hints.query_fingerprint,
        limits=provider.default_limits,
    )

    assert result == hints
    assert provider.recall_calls == 1


def test_fake_provider_exposes_timeout_without_retrying():
    provider = FakeMemorySidecarProvider(
        raise_error=MemorySidecarProviderError("timeout", "provider timed out")
    )

    try:
        provider.recall(
            query="review runtime", query_fingerprint="c" * 64,
            limits=provider.default_limits,
        )
    except MemorySidecarProviderError as exc:
        assert exc.code == "timeout"
    else:
        raise AssertionError("provider error was not raised")
    assert provider.recall_calls == 1

