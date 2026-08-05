from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .evidence_models import canonical_fingerprint
from .memory_sidecar_models import (
    CONFIDENCE_VALUES,
    FRESHNESS_VALUES,
    MEMORY_HINTS_SCHEMA,
    MEMORY_HINT_STATUSES,
    MemorySidecarSchemaError,
    _sha,
    _summary,
    _validate_artifact_path,
)


@dataclass(frozen=True)
class MemoryHint:
    memory_id: str
    summary: str
    source_refs: tuple[str, ...]
    freshness: str
    confidence: str
    source_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        _sha(self.memory_id, "memoryId")
        _summary(self.summary)
        if not isinstance(self.source_refs, (tuple, list)) or not self.source_refs or len(self.source_refs) > 16:
            raise MemorySidecarSchemaError("memory hint sourceRefs are unsafe")
        if not isinstance(self.source_fingerprints, (tuple, list)) or len(self.source_fingerprints) != len(self.source_refs):
            raise MemorySidecarSchemaError("memory hint source fingerprints are invalid")
        validated_fingerprints = tuple(_sha(fingerprint, "sourceFingerprints") for fingerprint in self.source_fingerprints)
        pairs = sorted((_validate_artifact_path(ref), fingerprint) for ref, fingerprint in zip(self.source_refs, validated_fingerprints))
        normalized_refs = tuple(ref for ref, _ in pairs)
        if len(set(normalized_refs)) != len(normalized_refs):
            raise MemorySidecarSchemaError("memory hint sourceRefs must be unique")
        normalized_fingerprints = tuple(fingerprint for _, fingerprint in pairs)
        object.__setattr__(self, "source_refs", normalized_refs)
        object.__setattr__(self, "source_fingerprints", normalized_fingerprints)
        if self.freshness not in FRESHNESS_VALUES:
            raise MemorySidecarSchemaError("memory hint freshness is invalid")
        if self.confidence not in CONFIDENCE_VALUES:
            raise MemorySidecarSchemaError("memory hint confidence is invalid")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryHint":
        expected = {"memoryId", "summary", "sourceRefs", "sourceFingerprints", "freshness", "confidence"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise MemorySidecarSchemaError("memory hint keys are invalid")
        refs = payload["sourceRefs"]
        fingerprints = payload["sourceFingerprints"]
        if not isinstance(refs, (list, tuple)) or len(refs) > 16:
            raise MemorySidecarSchemaError("memory hint sourceRefs are invalid")
        if not isinstance(fingerprints, (list, tuple)) or len(fingerprints) != len(refs):
            raise MemorySidecarSchemaError("memory hint source fingerprints are invalid")
        validated_fingerprints = tuple(_sha(fingerprint, "sourceFingerprints") for fingerprint in fingerprints)
        pairs = sorted((_validate_artifact_path(ref), fingerprint) for ref, fingerprint in zip(refs, validated_fingerprints))
        normalized_refs = tuple(ref for ref, _ in pairs)
        fingerprints = tuple(fingerprint for _, fingerprint in pairs)
        freshness = payload["freshness"]
        if freshness not in FRESHNESS_VALUES:
            raise MemorySidecarSchemaError("memory hint freshness is invalid")
        confidence = payload["confidence"]
        if confidence not in CONFIDENCE_VALUES:
            raise MemorySidecarSchemaError("memory hint confidence is invalid")
        return cls(_sha(payload["memoryId"], "memoryId"), _summary(payload["summary"]), normalized_refs, freshness, confidence, fingerprints)

    def to_dict(self) -> dict[str, Any]:
        return {"memoryId": self.memory_id, "summary": self.summary, "sourceRefs": list(self.source_refs), "sourceFingerprints": list(self.source_fingerprints), "freshness": self.freshness, "confidence": self.confidence}


@dataclass(frozen=True)
class MemoryHints:
    query_fingerprint: str
    status: str
    hints: tuple[MemoryHint, ...]
    max_items: int = 3
    max_bytes: int = 6000
    timeout_ms: int = 800

    def __post_init__(self) -> None:
        if not isinstance(self.hints, (tuple, list)):
            raise MemorySidecarSchemaError("memory hints items are invalid")
        object.__setattr__(self, "hints", tuple(self.hints))
        _sha(self.query_fingerprint, "queryFingerprint")
        if self.max_items != 3 or self.max_bytes != 6000 or self.timeout_ms != 800:
            raise MemorySidecarSchemaError("memory hints limits are invalid")
        if len(self.hints) > self.max_items:
            raise MemorySidecarSchemaError("memory hints are over the item cap")
        if self.status not in MEMORY_HINT_STATUSES:
            raise MemorySidecarSchemaError("memory hints status is invalid")
        if self.status != "ready" and self.hints:
            raise MemorySidecarSchemaError("non-ready hints cannot contain items")
        if not all(isinstance(item, MemoryHint) for item in self.hints):
            raise MemorySidecarSchemaError("memory hints items are invalid")
        if self.serialized_size_bytes() > self.max_bytes:
            raise MemorySidecarSchemaError("memory hints exceed the 6000-byte cap")

    @classmethod
    def empty(cls, *, query_fingerprint: str, status: str = "empty") -> "MemoryHints":
        if status not in MEMORY_HINT_STATUSES:
            raise MemorySidecarSchemaError("memory hints status is invalid")
        return cls(_sha(query_fingerprint, "queryFingerprint"), status, ())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryHints":
        expected = {"schemaVersion", "queryFingerprint", "status", "hints", "limits", "hintsFingerprint"}
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schemaVersion") != MEMORY_HINTS_SCHEMA:
            raise MemorySidecarSchemaError("memory hints envelope is invalid")
        status = payload["status"]
        if status not in MEMORY_HINT_STATUSES:
            raise MemorySidecarSchemaError("memory hints status is invalid")
        limits = payload["limits"]
        if not isinstance(limits, Mapping) or set(limits) != {"maxItems", "maxBytes", "timeoutMs"} or limits != {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800}:
            raise MemorySidecarSchemaError("memory hints limits are invalid")
        raw_hints = payload["hints"]
        if not isinstance(raw_hints, (list, tuple)) or len(raw_hints) > 3:
            raise MemorySidecarSchemaError("memory hints are over the item cap")
        result = cls(_sha(payload["queryFingerprint"], "queryFingerprint"), status, tuple(MemoryHint.from_dict(item) for item in raw_hints))
        if payload["hintsFingerprint"] != result.hints_fingerprint:
            raise MemorySidecarSchemaError("hints fingerprint does not match payload")
        if status != "ready" and result.hints:
            raise MemorySidecarSchemaError("non-ready hints cannot contain items")
        return result

    @property
    def hints_fingerprint(self) -> str:
        unsigned = {
            "schemaVersion": MEMORY_HINTS_SCHEMA, "queryFingerprint": self.query_fingerprint, "status": self.status,
            "hints": [item.to_dict() for item in self.hints],
            "limits": {"maxItems": self.max_items, "maxBytes": self.max_bytes, "timeoutMs": self.timeout_ms},
        }
        return canonical_fingerprint(unsigned)

    def serialized_size_bytes(self) -> int:
        unsigned = {
            "schemaVersion": MEMORY_HINTS_SCHEMA, "queryFingerprint": self.query_fingerprint, "status": self.status,
            "hints": [item.to_dict() for item in self.hints],
            "limits": {"maxItems": self.max_items, "maxBytes": self.max_bytes, "timeoutMs": self.timeout_ms},
            "hintsFingerprint": "0" * 64,
        }
        return len(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": MEMORY_HINTS_SCHEMA, "queryFingerprint": self.query_fingerprint, "status": self.status,
            "hints": [item.to_dict() for item in self.hints],
            "limits": {"maxItems": self.max_items, "maxBytes": self.max_bytes, "timeoutMs": self.timeout_ms},
            "hintsFingerprint": self.hints_fingerprint,
        }
