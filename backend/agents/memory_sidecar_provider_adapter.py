from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .memory_sidecar_adapter import (
    MemorySidecarProviderError,
    MemorySidecarRecallRequest,
    RecallLimits,
)
from .memory_sidecar_hint_models import MemoryHint, MemoryHints
from .memory_sidecar_models import (
    MEMORY_DENIED_PATTERNS,
    MemorySidecarSchemaError,
    _validate_artifact_path,
)

MEMORY_HINTS_SCHEMA = "memory-hints-v1"
MEMORY_HINTS_MAX_ITEMS = 3
MEMORY_HINTS_MAX_BYTES = 6000
MEMORY_HINTS_TIMEOUT_MS = 800


class ProviderBoundaryProtocol(Protocol):
    """The minimal transport contract the adapter drives. It MUST NOT perform
    network, shell, SQLite, Git or runtime-state writes.
    """

    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints:
        ...


@dataclass(frozen=True)
class ProviderRecallMetadata:
    """Bounded telemetry metadata: provider/model identity, request fingerprint,
    schema version and the explicit fallback reason. No secret, prompt or raw
    query is captured."""

    provider: str
    model: str
    request_fingerprint: str
    schema_version: str = MEMORY_HINTS_SCHEMA
    fallback_reason: str = ""
    writer_enabled: bool = False


@dataclass(frozen=True)
class ProviderRecallResult:
    """Fail-closed recall result: an explicit status plus bounded hints and
    bounded identity metadata. A non-ready status carries empty hints."""

    status: str
    hints: MemoryHints
    metadata: ProviderRecallMetadata


class MemorySidecarProviderAdapter:
    """Provider-neutral read-only adapter boundary for the controlled Hermes +
    DeepSeek integration.

    The adapter consumes an immutable MemorySidecarRecallRequest and classifies
    an untrusted provider result, failing closed on any of the explicit statuses:
    provider_unavailable, model_unavailable, timeout, schema_mismatch,
    stale_hint, conflict, sensitive_capture, path_violation, evidence_incomplete.
    """

    def __init__(self, request: MemorySidecarRecallRequest):
        if not isinstance(request, MemorySidecarRecallRequest):
            raise MemorySidecarProviderError("schema_mismatch", "recall request is invalid")
        self.request = request

    @property
    def writer_enabled(self) -> bool:
        return bool(self.request.provider_metadata.writer_enabled)

    @property
    def identity_metadata(self) -> ProviderRecallMetadata:
        return ProviderRecallMetadata(
            provider=self.request.provider_metadata.provider,
            model=self.request.provider_metadata.model,
            request_fingerprint=self.request.request_fingerprint,
            schema_version=MEMORY_HINTS_SCHEMA,
            fallback_reason="",
            writer_enabled=self.writer_enabled,
        )

    def _bounded_metadata(self, fallback_reason: str) -> ProviderRecallMetadata:
        return ProviderRecallMetadata(
            provider=self.request.provider_metadata.provider,
            model=self.request.provider_metadata.model,
            request_fingerprint=self.request.request_fingerprint,
            schema_version=MEMORY_HINTS_SCHEMA,
            fallback_reason=fallback_reason,
            writer_enabled=self.writer_enabled,
        )

    def _empty_result(self, status: str, fallback_reason: str) -> ProviderRecallResult:
        return ProviderRecallResult(
            status=status,
            hints=MemoryHints.empty(
                query_fingerprint=self.request.query_fingerprint,
                status="empty",
            ),
            metadata=self._bounded_metadata(fallback_reason),
        )

    def recall(self, provider: ProviderBoundaryProtocol) -> ProviderRecallResult:
        try:
            raw = provider.recall(
                query=self.request.query,
                query_fingerprint=self.request.query_fingerprint,
                limits=self.request.limits,
            )
        except MemorySidecarProviderError as exc:
            return self._unavailable_result(exc)
        except Exception as exc:  # untrusted transport wrapper must fail closed
            return self._empty_result(
                "provider_unavailable", f"unexpected provider error: {type(exc).__name__}"
            )
        return self._classify_and_build(raw)

    def _unavailable_result(self, exc: MemorySidecarProviderError) -> ProviderRecallResult:
        status = {
            "timeout": "timeout",
            "model_unavailable": "model_unavailable",
        }.get(exc.code, "provider_unavailable")
        reason = f"{exc.code}: {exc.summary}" if exc.code else exc.summary
        return self._empty_result(status, reason)

    def _classify_and_build(self, raw: Any) -> ProviderRecallResult:
        if isinstance(raw, MemoryHints):
            raw = raw.to_dict()
        if not isinstance(raw, Mapping):
            return self._empty_result("schema_mismatch", "provider returned a non-object payload")
        if raw.get("schemaVersion") != MEMORY_HINTS_SCHEMA:
            return self._empty_result("schema_mismatch", "unexpected memory hints schema")
        if raw.get("queryFingerprint") != self.request.query_fingerprint:
            return self._empty_result("schema_mismatch", "query fingerprint mismatch")
        hints_raw = raw.get("hints")
        if not isinstance(hints_raw, (list, tuple)):
            return self._empty_result("schema_mismatch", "hints is not a list")
        if len(hints_raw) > MEMORY_HINTS_MAX_ITEMS:
            return self._empty_result("schema_mismatch", "too many memory hints")
        if not hints_raw:
            return self._empty_result("empty", "provider returned no hints")

        validation = self._validate_hints(hints_raw)
        if validation is not None:
            status, reason = validation
            return self._empty_result(status, reason)

        try:
            parsed = MemoryHints.from_dict(dict(raw))
        except MemorySidecarSchemaError as exc:
            return self._empty_result("schema_mismatch", str(exc))
        except Exception as exc:
            return self._empty_result("schema_mismatch", f"{type(exc).__name__}: malformed hints")

        if parsed.serialized_size_bytes() > MEMORY_HINTS_MAX_BYTES:
            return self._empty_result("schema_mismatch", "memory hints exceed byte cap")

        return ProviderRecallResult(
            status="ready",
            hints=parsed,
            metadata=self._bounded_metadata(""),
        )

    def _validate_hints(self, hints_raw: list | tuple) -> tuple[str, str] | None:
        seen_paths: set[str] = set()
        for hint in hints_raw:
            if not isinstance(hint, Mapping):
                return "schema_mismatch", "hint is not an object"
            refs = hint.get("sourceRefs")
            if not isinstance(refs, (list, tuple)) or not refs:
                return "evidence_incomplete", "hint has no sourceRefs"
            if len(refs) != len(hint.get("sourceFingerprints", ()) or ()):
                return "schema_mismatch", "hint source fingerprints do not align"
            for ref in refs:
                if not isinstance(ref, str) or not ref:
                    return "schema_mismatch", "hint sourceRef is invalid"
                if any(fnmatch.fnmatch(ref, pattern) for pattern in MEMORY_DENIED_PATTERNS):
                    return "sensitive_capture", "hint sourceRef is denied by policy"
                try:
                    _validate_artifact_path(ref)
                except MemorySidecarSchemaError:
                    return "path_violation", "hint sourceRef is unsafe"
                if ref not in self.request.payload_path_allowlist.paths:
                    return "path_violation", "hint sourceRef is not declared"
                if ref in seen_paths:
                    return "conflict", "duplicate hint source identity"
                seen_paths.add(ref)
            freshness = hint.get("freshness")
            if freshness != "fresh":
                return "stale_hint", f"hint freshness is {freshness!r}"
        return None

    def write_candidate(self, candidate: Any) -> None:
        raise MemorySidecarProviderError("writer_disabled", "writer remains disabled in this phase")
