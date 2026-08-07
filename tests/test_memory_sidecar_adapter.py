from __future__ import annotations

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_adapter import (
    FakeMemorySidecarProvider,
    MemorySidecarPayloadPathAllowlist,
    MemorySidecarProviderError,
    MemorySidecarRecallRequest,
    MemorySidecarTaskAllowlist,
    RecallLimits,
)
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from backend.agents.memory_sidecar_models import MemorySidecarProviderMetadata


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


# --- Hermes + DeepSeek integration: controlled recall request contract (Plan Task 1) ---


def _task_fingerprint(*, seed: str) -> str:
    return canonical_fingerprint({"task": seed})


def _allowlist(*seeds: str) -> MemorySidecarTaskAllowlist:
    return MemorySidecarTaskAllowlist(frozenset(_task_fingerprint(seed=seed) for seed in seeds))


def _path_allowlist(*paths: str) -> MemorySidecarPayloadPathAllowlist:
    return MemorySidecarPayloadPathAllowlist(frozenset(paths))


def _request(*, query: str = "review runtime", task: str = "task-a", paths: tuple[str, ...] = (), limits: RecallLimits | None = None, path_allowlist: MemorySidecarPayloadPathAllowlist | None = None) -> MemorySidecarRecallRequest:
    return MemorySidecarRecallRequest(
        query=query,
        query_fingerprint=canonical_fingerprint({"query": query}),
        task_fingerprint=_task_fingerprint(seed=task),
        provider_metadata=MemorySidecarProviderMetadata(),
        task_allowlist=_allowlist("task-a", "task-b"),
        payload_path_allowlist=path_allowlist or _path_allowlist("review.json", "verification.json", "hint.json"),
        payload_paths=paths,
        limits=limits or RecallLimits(),
    )


def test_recall_request_fingerprint_is_deterministic_and_input_bound():
    first = _request()
    second = _request()
    assert len(first.request_fingerprint) == 64
    assert first.request_fingerprint == second.request_fingerprint
    assert first.request_fingerprint == first.recompute_fingerprint()
    assert _request(query="different query").request_fingerprint != first.request_fingerprint
    assert _request(task="task-b").request_fingerprint != first.request_fingerprint
    assert _request(paths=("hint.json",)).request_fingerprint != first.request_fingerprint
    assert _request(limits=RecallLimits(max_items=1)).request_fingerprint != first.request_fingerprint


def test_recall_request_exposes_schema_version_and_bounded_limits():
    request = _request()
    assert request.schema_version == "memory-recall-request-v1"
    assert request.to_dict()["schemaVersion"] == "memory-recall-request-v1"
    assert request.to_dict()["limits"] == {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800}
    with pytest.raises(MemorySidecarProviderError) as exc:
        _request(limits=RecallLimits(max_items=4))
    assert exc.value.code == "limits_unbounded"
    with pytest.raises(MemorySidecarProviderError) as exc:
        _request(limits=RecallLimits(max_bytes=6001))
    assert exc.value.code == "limits_unbounded"
    with pytest.raises(MemorySidecarProviderError) as exc:
        _request(limits=RecallLimits(timeout_ms=801))
    assert exc.value.code == "limits_unbounded"


def test_recall_request_rejects_undeclared_task():
    allowlist = _allowlist("task-a")
    with pytest.raises(MemorySidecarProviderError) as exc:
        MemorySidecarRecallRequest(
            query="q",
            query_fingerprint=canonical_fingerprint({"query": "q"}),
            task_fingerprint=_task_fingerprint(seed="task-b"),
            provider_metadata=MemorySidecarProviderMetadata(),
            task_allowlist=allowlist,
            payload_path_allowlist=_path_allowlist("review.json"),
            payload_paths=(),
            limits=RecallLimits(),
        )
    assert exc.value.code == "undeclared_task"


def test_recall_request_rejects_query_fingerprint_tampering():
    with pytest.raises(MemorySidecarProviderError) as exc:
        MemorySidecarRecallRequest(
            query="review runtime",
            query_fingerprint=canonical_fingerprint({"query": "different query"}),
            task_fingerprint=_task_fingerprint(seed="task-a"),
            provider_metadata=MemorySidecarProviderMetadata(),
            task_allowlist=_allowlist("task-a", "task-b"),
            payload_path_allowlist=_path_allowlist("review.json"),
            payload_paths=(),
            limits=RecallLimits(),
        )
    assert exc.value.code == "schema_mismatch"


def test_recall_request_rejects_absolute_and_traversal_paths():
    for path in ("/private/secret.txt", "../x.json", "C:/Users/secret.txt", "https://example.com/evidence.json", "file:/tmp/evidence.json", "a\\b.json"):
        with pytest.raises(MemorySidecarProviderError) as exc:
            _request(paths=(path,))
        assert exc.value.code == "path_violation"


def test_recall_request_rejects_secret_sqlite_csv_log_payloads():
    for path in (".env", "nested/.env", "credentials/token.json", "Secrets/api_key", "exports/data.sqlite", "data.csv", "logs/api.log"):
        with pytest.raises(MemorySidecarProviderError) as exc:
            _request(paths=(path,))
        assert exc.value.code == "sensitive_capture"


def test_recall_request_rejects_undeclared_safe_looking_payload_path():
    allowlist = _path_allowlist("review.json")
    with pytest.raises(MemorySidecarProviderError) as exc:
        _request(paths=("unrelated/context.json",), path_allowlist=allowlist)
    assert exc.value.code == "path_violation"


def test_recall_limits_reject_non_int_and_bool_values():
    for kwargs in ({"max_items": "3"}, {"max_items": 3.0}, {"max_bytes": 6000.0}, {"timeout_ms": True}, {"max_items": False}, {"timeout_ms": "800"}):
        with pytest.raises(MemorySidecarProviderError) as exc:
            RecallLimits(**kwargs)
        assert exc.value.code == "limits_invalid"


def test_recall_request_from_dict_rejects_non_int_limits():
    request = _request()
    payload = request.to_dict()
    payload["limits"] = {"maxItems": "3", "maxBytes": 6000, "timeoutMs": 800}
    with pytest.raises(MemorySidecarProviderError) as exc:
        MemorySidecarRecallRequest.from_dict(payload)
    assert exc.value.code == "limits_invalid"
    payload = request.to_dict()
    payload["limits"] = {"maxItems": 3, "maxBytes": True, "timeoutMs": 800}
    with pytest.raises(MemorySidecarProviderError) as exc:
        MemorySidecarRecallRequest.from_dict(payload)
    assert exc.value.code == "limits_invalid"


def test_recall_request_round_trip_preserves_envelope_and_fingerprint():
    request = _request(paths=("review.json", "verification.json"))
    payload = request.to_dict()
    assert set(payload["payloadPathAllowlist"]) == {"review.json", "verification.json", "hint.json"}
    restored = MemorySidecarRecallRequest.from_dict(payload)
    assert restored == request
    assert restored.request_fingerprint == request.request_fingerprint
    tampered = dict(payload)
    tampered["requestFingerprint"] = "0" * 64
    with pytest.raises(MemorySidecarProviderError) as exc:
        MemorySidecarRecallRequest.from_dict(tampered)
    assert exc.value.code == "schema_mismatch"


def test_fake_provider_remains_deterministic_and_writer_disabled():
    hints = _hints()
    provider = FakeMemorySidecarProvider(recall_results={hints.query_fingerprint: hints})
    first = provider.recall(query="review runtime", query_fingerprint=hints.query_fingerprint, limits=provider.default_limits)
    second = provider.recall(query="review runtime", query_fingerprint=hints.query_fingerprint, limits=provider.default_limits)
    assert first == second
    assert provider.recall_calls == 2
    assert provider.writer_enabled is False
    assert MemorySidecarProviderMetadata().writer_enabled is False

