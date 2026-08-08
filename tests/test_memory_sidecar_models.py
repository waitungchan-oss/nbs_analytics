from __future__ import annotations

import pytest

from backend.agents.memory_sidecar_models import (
    MemoryCandidate,
    MemorySidecarProviderMetadata,
    MemorySidecarSchemaError,
    MemorySourceRef,
)
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints


def _source_ref(path: str = "verification.json", *, run_id: str = "run-1") -> MemorySourceRef:
    return MemorySourceRef.from_dict({
        "runId": run_id,
        "artifactPath": path,
        "artifactSha256": "a" * 64,
        "commit": "b" * 40,
    })


def test_candidate_fingerprint_is_deterministic_and_source_bound():
    first = MemoryCandidate.from_parts(
        kind="verification_pattern",
        summary="Use the focused Hermes pack before full acceptance.",
        source_refs=(_source_ref(),),
        source_status="completed",
        generated_at="2026-08-05T00:00:00+00:00",
        expires_at="2026-11-03T00:00:00+00:00",
        confidence="high",
        policy_version="memory-freshness-v1",
    )
    second = MemoryCandidate.from_parts(
        kind="verification_pattern",
        summary="Use the focused Hermes pack before full acceptance.",
        source_refs=(_source_ref(),),
        source_status="completed",
        generated_at="2026-08-05T00:00:00+00:00",
        expires_at="2026-11-03T00:00:00+00:00",
        confidence="high",
        policy_version="memory-freshness-v1",
    )
    assert len(first.memory_id) == 64
    assert first.memory_id == second.memory_id
    assert first.memory_fingerprint == second.memory_fingerprint
    assert first.memory_fingerprint == first.recompute_fingerprint()
    changed_source = MemoryCandidate.from_parts(
        kind="verification_pattern", summary="Use the focused Hermes pack before full acceptance.",
        source_refs=(_source_ref(run_id="run-2"),), source_status="completed",
        generated_at="2026-08-05T00:00:00+00:00", expires_at="2026-11-03T00:00:00+00:00",
        confidence="high", policy_version="memory-freshness-v1",
    )
    assert changed_source.memory_id != first.memory_id
    assert changed_source.memory_fingerprint != first.memory_fingerprint


def test_candidate_round_trip_preserves_strict_public_envelope():
    candidate = MemoryCandidate.from_parts(
        kind="sop",
        summary="Run targeted tests before full verification.",
        source_refs=(_source_ref("tests.json"),),
        source_status="completed",
        generated_at="2026-08-05T00:00:00+00:00",
        expires_at="2026-11-03T00:00:00+00:00",
        confidence="medium",
        policy_version="memory-freshness-v1",
    )
    assert MemoryCandidate.from_dict(candidate.to_dict()).to_dict() == candidate.to_dict()


def test_source_ref_rejects_absolute_traversal_and_unsafe_commit():
    for path in ("/private/verification.json", "../verification.json", "a\\b.json", "C:/Users/secret.txt", "https://example.com/evidence.json", "file:/tmp/evidence.json", ".env", "nested/.env", "exports/data.sqlite", "credentials/token.json", "artifacts/credentials/api_key", "runs/x/Secrets/token"):
        with pytest.raises(MemorySidecarSchemaError):
            _source_ref(path)
    with pytest.raises(MemorySidecarSchemaError):
        MemorySourceRef.from_dict({
            "runId": "run-1",
            "artifactPath": "verification.json",
            "artifactSha256": "a" * 64,
            "commit": "not-a-commit",
        })
    with pytest.raises(MemorySidecarSchemaError):
        MemorySourceRef("run-1", "/private/verification.json", "a" * 64, "b" * 40)
    with pytest.raises(MemorySidecarSchemaError):
        MemorySourceRef("invalid", "verification.json", "a" * 64, "b" * 40)


def test_candidate_rejects_extra_keys_and_unbounded_summary():
    candidate = MemoryCandidate.from_parts(
        kind="decision",
        summary="A bounded decision.",
        source_refs=(_source_ref(),),
        source_status="completed",
        generated_at="2026-08-05T00:00:00+00:00",
        expires_at="2026-11-03T00:00:00+00:00",
        confidence="high",
        policy_version="memory-freshness-v1",
    ).to_dict()
    candidate["unexpected"] = "not allowed"
    with pytest.raises(MemorySidecarSchemaError):
        MemoryCandidate.from_dict(candidate)
    with pytest.raises(MemorySidecarSchemaError):
        MemoryCandidate.from_parts(
            kind="decision",
            summary="x" * 5000,
            source_refs=(_source_ref(),),
            source_status="completed",
            generated_at="2026-08-05T00:00:00+00:00",
            expires_at="2026-11-03T00:00:00+00:00",
            confidence="high",
            policy_version="memory-freshness-v1",
        )


def test_hints_empty_is_bounded_and_status_is_explicit():
    hints = MemoryHints.empty(query_fingerprint="c" * 64, status="timeout")
    assert hints.to_dict() == {
        "schemaVersion": "memory-hints-v1",
        "queryFingerprint": "c" * 64,
        "status": "timeout",
        "hints": [],
        "limits": {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800},
        "hintsFingerprint": hints.hints_fingerprint,
    }


def test_hints_rejects_tampered_fingerprint():
    hints = MemoryHints.empty(query_fingerprint="c" * 64)
    payload = hints.to_dict()
    payload["hintsFingerprint"] = "d" * 64
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHints.from_dict(payload)


def test_hints_enforce_complete_utf8_byte_cap():
    small = MemoryHints(
        query_fingerprint="c" * 64,
        status="ready",
        hints=(MemoryHint("a" * 64, "short", ("review.json",), "fresh", "high", ("b" * 64,)),),
    )
    assert small.serialized_size_bytes() <= 6000
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHints(
            query_fingerprint="c" * 64,
            status="ready",
            hints=tuple(
                MemoryHint("a" * 64, "x" * 2048, ("review.json",), "fresh", "high", ("b" * 64,))
                for _ in range(3)
            ),
        )


def test_candidate_rejects_malformed_reversed_and_over_ttl_freshness():
    values = (
        ("not-a-timestamp", "2026-08-06T00:00:00+00:00"),
        ("2026-08-06T00:00:00", "2026-08-07T00:00:00+00:00"),
        ("2026-08-06T00:00:00+00:00", "2026-08-05T00:00:00+00:00"),
        ("2026-08-05T00:00:00+00:00", "2026-11-04T00:00:00+00:00"),
    )
    for generated_at, expires_at in values:
        with pytest.raises(MemorySidecarSchemaError):
            MemoryCandidate.from_parts(
                kind="decision", summary="bounded", source_refs=(_source_ref(),),
                source_status="completed", generated_at=generated_at, expires_at=expires_at,
                confidence="high", policy_version="memory-freshness-v1",
            )


def test_hint_module_is_directly_importable_and_validates_direct_construction():
    import importlib

    module = importlib.import_module("backend.agents.memory_sidecar_hint_models")
    assert module.MemoryHints is MemoryHints
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHint("not-a-fingerprint", "summary", ("review.json",), "fresh", "high", ("b" * 64,))
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHints(
            query_fingerprint="c" * 64,
            status="timeout",
            hints=(MemoryHint("a" * 64, "summary", ("review.json",), "fresh", "high", ("b" * 64,)),),
        )
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHint("a" * 64, "summary", (), "fresh", "high", ())


def test_hints_reject_invalid_direct_query_fingerprint():
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHints(query_fingerprint="invalid", status="empty", hints=())
    payload = MemoryHints.empty(query_fingerprint="c" * 64).to_dict()
    payload["status"] = "ready"
    payload["hints"] = [{"memoryId": "a" * 64, "summary": "summary", "sourceRefs": [], "freshness": "fresh", "confidence": "high"}]
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHints.from_dict(payload)


def test_candidate_direct_construction_cannot_forge_identity():
    with pytest.raises(MemorySidecarSchemaError):
        MemoryCandidate(
            "decision", "summary", (_source_ref(),), "completed",
            "2026-08-05T00:00:00+00:00", "2026-11-03T00:00:00+00:00", "high",
            "memory-freshness-v1", "a" * 64, "b" * 64,
        )


def test_direct_collection_inputs_are_normalized_immutable():
    candidate = MemoryCandidate.from_parts(
        kind="decision", summary="summary", source_refs=(_source_ref(),), source_status="completed",
        generated_at="2026-08-05T00:00:00+00:00", expires_at="2026-11-03T00:00:00+00:00",
        confidence="high", policy_version="memory-freshness-v1",
    )
    rebuilt = MemoryCandidate(
        candidate.kind, candidate.summary, list(candidate.source_refs), candidate.source_status,
        candidate.generated_at, candidate.expires_at, candidate.confidence, candidate.policy_version,
        candidate.memory_id, candidate.memory_fingerprint,
    )
    assert isinstance(rebuilt.source_refs, tuple)
    hint = MemoryHint("a" * 64, "summary", ["review.json"], "fresh", "high", ["b" * 64])
    hints = MemoryHints(query_fingerprint="c" * 64, status="ready", hints=[hint])
    assert isinstance(hints.hints, tuple)
    assert isinstance(hint.source_refs, tuple)
    paired = MemoryHint(
        "a" * 64, "summary", ["z.json", "a.json"], "fresh", "high", ["b" * 64, "c" * 64]
    )
    assert paired.source_refs == ("a.json", "z.json")
    assert paired.source_fingerprints == ("c" * 64, "b" * 64)
    with pytest.raises(MemorySidecarSchemaError):
        MemoryHint("a" * 64, "summary", ["z.json", "a.json"], "fresh", "high", [None, "b" * 64])


# --- Hermes + DeepSeek integration: provider metadata contract (Plan Task 1) ---


def test_provider_metadata_defaults_are_safe_immutable_and_identified():
    meta = MemorySidecarProviderMetadata()
    assert meta.provider == "hermes"
    assert meta.model == "deepseek-v4-flash"
    assert meta.schema_version == "memory-hints-v1"
    assert meta.recall_enabled is False
    assert meta.writer_enabled is False
    assert meta.shadow_mode is True
    assert meta.identity == "hermes/deepseek-v4-flash"
    with pytest.raises(AttributeError):
        meta.writer_enabled = True


def test_provider_metadata_rejects_unknown_provider_model_and_unsafe_flags():
    for provider in ("openai", "", "hermes "):
        with pytest.raises(MemorySidecarSchemaError):
            MemorySidecarProviderMetadata(provider=provider)
    for model in ("gpt-5.5", "", "deepseek-v4-flash-2"):
        with pytest.raises(MemorySidecarSchemaError):
            MemorySidecarProviderMetadata(model=model)
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata(schema_version="memory-hints-v2")
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata(recall_enabled="yes")
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata(shadow_mode=1)
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata(writer_enabled=True)


def test_provider_metadata_rejects_non_string_unhashable_provider_and_model():
    for provider in ([], {}, None, 123, True):
        with pytest.raises(MemorySidecarSchemaError):
            MemorySidecarProviderMetadata(provider=provider)
    for model in ([], {}, None, 123, False):
        with pytest.raises(MemorySidecarSchemaError):
            MemorySidecarProviderMetadata(model=model)


def test_provider_metadata_from_dict_rejects_non_string_provider_and_model():
    payload = MemorySidecarProviderMetadata().to_dict()
    payload["provider"] = []
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata.from_dict(payload)
    payload = MemorySidecarProviderMetadata().to_dict()
    payload["model"] = {}
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata.from_dict(payload)


def test_provider_metadata_round_trip_preserves_strict_envelope():
    meta = MemorySidecarProviderMetadata()
    assert MemorySidecarProviderMetadata.from_dict(meta.to_dict()).to_dict() == meta.to_dict()
    payload = meta.to_dict()
    payload["unexpected"] = True
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata.from_dict(payload)
    payload = meta.to_dict()
    payload["model"] = "gpt-5.5"
    with pytest.raises(MemorySidecarSchemaError):
        MemorySidecarProviderMetadata.from_dict(payload)
