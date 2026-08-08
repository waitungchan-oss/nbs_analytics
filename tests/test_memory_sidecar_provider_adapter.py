from __future__ import annotations

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_adapter import (
    MemorySidecarPayloadPathAllowlist,
    MemorySidecarProviderError,
    MemorySidecarRecallRequest,
    MemorySidecarTaskAllowlist,
    RecallLimits,
)
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from backend.agents.memory_sidecar_models import MemorySidecarProviderMetadata
from backend.agents.memory_sidecar_provider_adapter import (
    MemorySidecarProviderAdapter,
    ProviderRecallMetadata,
    ProviderRecallResult,
)

QUERY = "review runtime"
HINT_FINGERPRINT = "b" * 64


def _task_fingerprint(seed: str = "task-a") -> str:
    return canonical_fingerprint({"task": seed})


def _request(path_allowlist: frozenset[str] | None = None) -> MemorySidecarRecallRequest:
    allowed = path_allowlist or frozenset({"review.json", "verification.json"})
    return MemorySidecarRecallRequest(
        query=QUERY,
        query_fingerprint=canonical_fingerprint({"query": QUERY}),
        task_fingerprint=_task_fingerprint(),
        provider_metadata=MemorySidecarProviderMetadata(),
        task_allowlist=MemorySidecarTaskAllowlist(frozenset({_task_fingerprint()})),
        payload_path_allowlist=MemorySidecarPayloadPathAllowlist(allowed),
        payload_paths=("review.json",),
        limits=RecallLimits(),
    )


def _request_with_limits(limits: RecallLimits) -> MemorySidecarRecallRequest:
    base = _request()
    return MemorySidecarRecallRequest(
        query=QUERY,
        query_fingerprint=canonical_fingerprint({"query": QUERY}),
        task_fingerprint=base.task_fingerprint,
        provider_metadata=MemorySidecarProviderMetadata(),
        task_allowlist=base.task_allowlist,
        payload_path_allowlist=base.payload_path_allowlist,
        payload_paths=("review.json",),
        limits=limits,
    )


def _hint_dict(*, path: str = "review.json", freshness: str = "fresh", fp: str = HINT_FINGERPRINT) -> dict:
    return {
        "memoryId": "a" * 64,
        "summary": "Use the focused Hermes pack.",
        "sourceRefs": [path],
        "sourceFingerprints": [fp],
        "freshness": freshness,
        "confidence": "high",
    }


def _ready_hint() -> MemoryHint:
    return MemoryHint("a" * 64, "Use the focused Hermes pack.", ("review.json",), "fresh", "high", (HINT_FINGERPRINT,))


def _hints(*, query_fingerprint: str | None = None, hints: tuple[MemoryHint, ...] = ()) -> MemoryHints:
    return MemoryHints(
        query_fingerprint=query_fingerprint or canonical_fingerprint({"query": QUERY}),
        status="ready",
        hints=hints if hints else (_ready_hint(),),
    )


class _StubProvider:
    """Returns an untrusted raw payload so the adapter owns all classification."""

    def __init__(self, *, result=None, error: MemorySidecarProviderError | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, RecallLimits]] = []

    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits):
        self.calls.append((query, query_fingerprint, limits))
        if self.error is not None:
            raise self.error
        return self.result


def test_adapter_ready_returns_bounded_identity_metadata():
    request = _request()
    adapter = MemorySidecarProviderAdapter(request)

    result = adapter.recall(_StubProvider(result=_hints()))

    assert isinstance(result, ProviderRecallResult)
    assert result.status == "ready"
    assert result.hints.hints == (_ready_hint(),)
    assert isinstance(result.metadata, ProviderRecallMetadata)
    assert result.metadata.provider == "hermes"
    assert result.metadata.model == "deepseek-v4-flash"
    assert result.metadata.request_fingerprint == request.request_fingerprint
    assert result.metadata.schema_version == "memory-hints-v1"
    assert result.metadata.fallback_reason == ""


def test_adapter_identity_metadata_is_bound_to_request():
    request = _request()
    adapter = MemorySidecarProviderAdapter(request)
    metadata = adapter.identity_metadata

    assert metadata.provider == request.provider_metadata.provider
    assert metadata.model == request.provider_metadata.model
    assert metadata.request_fingerprint == request.request_fingerprint
    assert metadata.schema_version == "memory-hints-v1"


def test_adapter_fails_closed_on_provider_unavailable():
    adapter = MemorySidecarProviderAdapter(_request())
    result = adapter.recall(
        _StubProvider(error=MemorySidecarProviderError("unavailable", "provider unavailable"))
    )
    assert result.status == "provider_unavailable"
    assert result.hints.hints == ()
    assert "unavailable" in result.metadata.fallback_reason


def test_adapter_fails_closed_on_model_unavailable():
    adapter = MemorySidecarProviderAdapter(_request())
    result = adapter.recall(
        _StubProvider(error=MemorySidecarProviderError("model_unavailable", "model unreachable"))
    )
    assert result.status == "model_unavailable"
    # Adapter-only failure status is not a memory-hints-v1 status, so the
    # embedded hints object maps safely to the generic "empty" status.
    assert result.hints.status == "empty"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_timeout():
    adapter = MemorySidecarProviderAdapter(_request())
    result = adapter.recall(
        _StubProvider(error=MemorySidecarProviderError("timeout", "provider timed out"))
    )
    assert result.status == "timeout"
    assert result.hints.status == "timeout"
    assert result.hints.hints == ()
    assert "timeout" in result.metadata.fallback_reason


def test_adapter_fails_closed_on_malformed_response():
    adapter = MemorySidecarProviderAdapter(_request())

    result = adapter.recall(_StubProvider(result={"schemaVersion": "memory-hints-v1", "hints": "not-a-list"}))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_wrong_schema_version():
    adapter = MemorySidecarProviderAdapter(_request())
    bad = _hints().to_dict()
    bad["schemaVersion"] = "memory-hints-v2"
    result = adapter.recall(_StubProvider(result=bad))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_stale_hint():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["hints"][0]["freshness"] = "stale"
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "stale_hint"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_malformed_source_fingerprints():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["hints"][0]["sourceFingerprints"] = 123
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_when_source_fingerprints_are_not_a_list():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["hints"][0]["sourceFingerprints"] = "b" * 64
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_when_source_fingerprint_count_misaligns():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["hints"][0]["sourceFingerprints"] = ["b" * 64, "c" * 64]
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def _non_ready_payload(*, status: str) -> dict:
    base = _hints().to_dict()
    base["status"] = status
    base["hints"] = []
    return base


def test_adapter_preserves_explicit_timeout_non_ready_status():
    adapter = MemorySidecarProviderAdapter(_request())
    result = adapter.recall(_StubProvider(result=_non_ready_payload(status="timeout")))
    assert result.status == "timeout"
    assert result.hints.status == "timeout"
    assert result.hints.hints == ()


def test_adapter_preserves_explicit_degraded_non_ready_status():
    adapter = MemorySidecarProviderAdapter(_request())
    result = adapter.recall(_StubProvider(result=_non_ready_payload(status="degraded")))
    assert result.status == "degraded"
    assert result.hints.status == "degraded"
    assert result.hints.hints == ()


def test_adapter_preserves_explicit_empty_non_ready_status():
    adapter = MemorySidecarProviderAdapter(_request())
    result = adapter.recall(_StubProvider(result=_non_ready_payload(status="empty")))
    assert result.status == "empty"
    assert result.hints.status == "empty"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_unknown_non_ready_status():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _non_ready_payload(status="pending")
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_when_non_ready_status_carries_items():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["status"] = "degraded"  # hints remain populated
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_when_ready_status_carries_no_hints():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _non_ready_payload(status="ready")
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_conflict_duplicate_path_identity():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = {
        "schemaVersion": "memory-hints-v1",
        "queryFingerprint": canonical_fingerprint({"query": QUERY}),
        "status": "ready",
        "hints": [
            _hint_dict(path="review.json", fp="b" * 64),
            {**_hint_dict(path="review.json", fp="c" * 64), "memoryId": "d" * 64, "summary": "two"},
        ],
        "limits": {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800},
        "hintsFingerprint": "x" * 64,
    }
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "conflict"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_sensitive_capture_path():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["hints"][0]["sourceRefs"] = [".env"]
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "sensitive_capture"
    assert result.hints.hints == ()


def test_adapter_fails_closed_on_path_violation_undeclared_path():
    adapter = MemorySidecarProviderAdapter(_request())
    raw = _hints().to_dict()
    raw["hints"][0]["sourceRefs"] = ["../unrelated/context.json"]
    result = adapter.recall(_StubProvider(result=raw))
    assert result.status == "path_violation"
    assert result.hints.hints == ()


def test_adapter_enforces_bounded_memory_hints_v1_caps():
    request = _request()
    adapter = MemorySidecarProviderAdapter(request)
    result = adapter.recall(_StubProvider(result=_hints()))
    assert result.status == "ready"
    assert result.hints.max_items == 3
    assert result.hints.max_bytes == 6000
    assert result.hints.timeout_ms == 800


def test_adapter_enforces_request_specific_recall_limits_on_item_count():
    # A request that narrows the recall budget to a single hint must reject a
    # ready response carrying two hints: the response exceeds the approved,
    # request-specific max_items rather than just the global cap.
    request = _request_with_limits(RecallLimits(max_items=1, max_bytes=3000, timeout_ms=800))
    adapter = MemorySidecarProviderAdapter(request)
    hint_two = MemoryHint("d" * 64, "two", ("verification.json",), "fresh", "high", ("c" * 64,))
    oversized = MemoryHints(
        query_fingerprint=canonical_fingerprint({"query": QUERY}),
        status="ready",
        hints=(_ready_hint(), hint_two),
    )
    result = adapter.recall(_StubProvider(result=oversized.to_dict()))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_rejects_response_that_widens_the_request_budget():
    # A request that narrows max_items to one must not accept a ready response
    # whose embedded memory-hints-v1 limits still declare the full three-item
    # cap: the provider's declared limits would widen the approved request
    # budget and must fail closed as a schema mismatch.
    request = _request_with_limits(RecallLimits(max_items=1, max_bytes=6000, timeout_ms=800))
    adapter = MemorySidecarProviderAdapter(request)
    # A single, otherwise-valid hint is within the global caps but embeds
    # maxItems=3, which exceeds the request's maxItems=1.
    result = adapter.recall(_StubProvider(result=_hints()))
    assert result.status == "schema_mismatch"
    assert result.hints.hints == ()


def test_adapter_accepts_ready_response_with_narrowed_request_limits():
    request = _request_with_limits(RecallLimits(max_items=1, max_bytes=6000, timeout_ms=800))
    raw = _hints().to_dict()
    raw["limits"] = {"maxItems": 1, "maxBytes": 6000, "timeoutMs": 800}
    raw["hintsFingerprint"] = canonical_fingerprint({
        "schemaVersion": "memory-hints-v1",
        "queryFingerprint": raw["queryFingerprint"],
        "status": "ready",
        "hints": raw["hints"],
        "limits": raw["limits"],
    })
    result = MemorySidecarProviderAdapter(request).recall(_StubProvider(result=raw))
    assert result.status == "ready"
    assert result.hints.hints == (_ready_hint(),)


def test_adapter_accepts_response_within_default_request_limits():
    # The default request budget (3 items / 6000 bytes / 800 ms) must keep
    # accepting an in-budget ready response: the request-specific enforcement
    # must not reject responses that fit the default budget.
    request = _request()
    adapter = MemorySidecarProviderAdapter(request)
    result = adapter.recall(_StubProvider(result=_hints()))
    assert result.status == "ready"
    assert result.hints.hints == (_ready_hint(),)


def test_adapter_explicitly_rejects_write_candidate():
    adapter = MemorySidecarProviderAdapter(_request())
    with pytest.raises(MemorySidecarProviderError) as exc:
        adapter.write_candidate(None)
    assert exc.value.code == "writer_disabled"


def test_adapter_keeps_writer_enabled_disabled():
    adapter = MemorySidecarProviderAdapter(_request())
    assert adapter.writer_enabled is False
    assert adapter.request.provider_metadata.writer_enabled is False
    result = adapter.recall(_StubProvider(result=_hints()))
    assert result.metadata.writer_enabled is False
