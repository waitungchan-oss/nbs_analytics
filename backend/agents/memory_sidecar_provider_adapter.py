from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .memory_sidecar_adapter import (
    MemorySidecarProviderError,
    MemorySidecarRecallRequest,
    RecallLimits,
)
from .evidence_models import canonical_fingerprint
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
# Explicit non-ready, empty-hints statuses that the adapter preserves verbatim
# instead of collapsing them to a generic empty result.
_NON_READY_EMPTY_STATUSES = frozenset({"empty", "timeout", "degraded"})


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
        # Preserve the embedded memory-hints-v1 status for the supported non-ready
        # statuses (empty/timeout/degraded). Adapter-only failure statuses
        # (provider_unavailable, schema_mismatch, stale_hint, conflict, ...) are
        # not valid memory-hints-v1 statuses and cannot be embedded in the hints
        # object, so they map safely to the generic "empty" hints status.
        hints_status = status if status in _NON_READY_EMPTY_STATUSES else "empty"
        return ProviderRecallResult(
            status=status,
            hints=MemoryHints.empty(
                query_fingerprint=self.request.query_fingerprint,
                status=hints_status,
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
        status = raw.get("status")
        hints_raw = raw.get("hints")
        if not isinstance(hints_raw, (list, tuple)):
            return self._empty_result("schema_mismatch", "hints is not a list")
        # The effective item cap is the stricter of the global schema cap and
        # the approved, request-specific RecallLimits.max_items. A request that
        # narrows the budget below the global cap must still fail closed on any
        # response that exceeds the narrower per-request ceiling.
        effective_item_cap = min(MEMORY_HINTS_MAX_ITEMS, self.request.limits.max_items)
        if len(hints_raw) > effective_item_cap:
            return self._empty_result(
                "schema_mismatch",
                f"too many memory hints for the approved request limit ({effective_item_cap})",
            )
        # Non-ready memory-hints-v1 statuses (empty, timeout, degraded) must be
        # preserved as explicit non-ready, empty-hints results. They cannot carry
        # hint items; a ready status cannot be empty; any other status is invalid.
        if isinstance(status, str) and status in _NON_READY_EMPTY_STATUSES:
            if hints_raw:
                return self._empty_result("schema_mismatch", "non-ready status cannot carry hint items")
            return self._empty_result(status, f"provider returned {status} hints")
        if status != "ready":
            return self._empty_result("schema_mismatch", f"unexpected memory hints status: {status!r}")
        if not hints_raw:
            return self._empty_result("schema_mismatch", "ready status cannot carry no hints")

        validation = self._validate_hints(hints_raw)
        if validation is not None:
            status, reason = validation
            return self._empty_result(status, reason)

        try:
            parsed = self._parse_bounded_hints(raw, hints_raw)
        except MemorySidecarSchemaError as exc:
            return self._empty_result("schema_mismatch", str(exc))
        except Exception as exc:
            return self._empty_result("schema_mismatch", f"{type(exc).__name__}: malformed hints")

        effective_byte_cap = min(MEMORY_HINTS_MAX_BYTES, self.request.limits.max_bytes)
        if parsed.serialized_size_bytes() > effective_byte_cap:
            return self._empty_result(
                "schema_mismatch",
                f"memory hints exceed the approved request byte limit ({effective_byte_cap})",
            )

        return ProviderRecallResult(
            status="ready",
            hints=parsed,
            metadata=self._bounded_metadata(""),
        )

    def _parse_bounded_hints(self, raw: Mapping[str, Any], hints_raw: list | tuple) -> MemoryHints:
        """Validate request-scoped limits before constructing the fixed schema model."""
        limits = raw.get("limits")
        if not isinstance(limits, Mapping) or set(limits) != {"maxItems", "maxBytes", "timeoutMs"}:
            raise MemorySidecarSchemaError("memory hints limits are invalid")
        values = (limits["maxItems"], limits["maxBytes"], limits["timeoutMs"])
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise MemorySidecarSchemaError("memory hints limits are invalid")
        global_limits = (MEMORY_HINTS_MAX_ITEMS, MEMORY_HINTS_MAX_BYTES, MEMORY_HINTS_TIMEOUT_MS)
        request_limits = (self.request.limits.max_items, self.request.limits.max_bytes, self.request.limits.timeout_ms)
        if any(value > global_cap or value > request_cap for value, global_cap, request_cap in zip(values, global_limits, request_limits)):
            raise MemorySidecarSchemaError("memory hints limits widen the approved request budget")
        raw_hints = tuple(MemoryHint.from_dict(item) for item in hints_raw)
        unsigned = {
            "schemaVersion": MEMORY_HINTS_SCHEMA,
            "queryFingerprint": raw["queryFingerprint"],
            "status": raw["status"],
            "hints": [item.to_dict() for item in raw_hints],
            "limits": dict(limits),
        }
        if raw.get("hintsFingerprint") != canonical_fingerprint(unsigned):
            raise MemorySidecarSchemaError("hints fingerprint does not match payload")
        # MemoryHints is the existing fixed-cap public model; request-specific
        # limits are enforced above and on serialized size before acceptance.
        return MemoryHints(raw["queryFingerprint"], raw["status"], raw_hints)

    def _validate_hints(self, hints_raw: list | tuple) -> tuple[str, str] | None:
        seen_paths: set[str] = set()
        for hint in hints_raw:
            if not isinstance(hint, Mapping):
                return "schema_mismatch", "hint is not an object"
            refs = hint.get("sourceRefs")
            if not isinstance(refs, (list, tuple)) or not refs:
                return "evidence_incomplete", "hint has no sourceRefs"
            fingerprints = hint.get("sourceFingerprints")
            if not isinstance(fingerprints, (list, tuple)):
                return "schema_mismatch", "hint source fingerprints are not a list"
            if len(refs) != len(fingerprints):
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
