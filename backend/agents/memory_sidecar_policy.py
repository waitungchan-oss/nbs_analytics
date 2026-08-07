from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .memory_sidecar_models import MEMORY_DENIED_PATTERNS, MEMORY_KINDS


class MemorySidecarPolicyError(ValueError):
    """Raised when sidecar policy is missing, malformed or unsafe."""


@dataclass(frozen=True)
class MemorySidecarPolicy:
    schema_version: str
    max_items: int
    max_bytes: int
    timeout_ms: int
    summary_max_bytes: int
    ttl_days: int
    allowed_kinds: frozenset[str]
    denied_patterns: tuple[str, ...]

    @classmethod
    def from_file(cls, path: Path) -> "MemorySidecarPolicy":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {"schemaVersion", "maxItems", "maxBytes", "timeoutMs", "summaryMaxBytes", "ttlDays", "allowedKinds", "deniedPatterns"}
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schemaVersion") != "memory-sidecar-policy-v1":
            raise MemorySidecarPolicyError("memory sidecar policy envelope is invalid")
        values = {key: payload[key] for key in ("maxItems", "maxBytes", "timeoutMs", "summaryMaxBytes", "ttlDays")}
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
            raise MemorySidecarPolicyError("memory sidecar policy limits are invalid")
        kinds = payload["allowedKinds"]
        denied = payload["deniedPatterns"]
        if not isinstance(kinds, list) or not kinds or any(
            not isinstance(value, str) or not value for value in kinds
        ):
            raise MemorySidecarPolicyError("allowedKinds is invalid")
        if len(kinds) != len(set(kinds)) or not set(kinds).issubset(MEMORY_KINDS):
            raise MemorySidecarPolicyError("allowedKinds is invalid")
        if not isinstance(denied, list) or any(not isinstance(value, str) or not value for value in denied):
            raise MemorySidecarPolicyError("deniedPatterns is invalid")
        if set(denied) != set(MEMORY_DENIED_PATTERNS):
            raise MemorySidecarPolicyError("deniedPatterns must match the immutable contract set")
        result = cls(
            payload["schemaVersion"], payload["maxItems"], payload["maxBytes"], payload["timeoutMs"],
            payload["summaryMaxBytes"], payload["ttlDays"], frozenset(kinds), tuple(denied),
        )
        result.validate_limits(max_items=result.max_items, max_bytes=result.max_bytes, timeout_ms=result.timeout_ms)
        if not 1 <= result.summary_max_bytes <= 2048 or not 1 <= result.ttl_days <= 365:
            raise MemorySidecarPolicyError("policy exceeds pilot safety caps")
        return result

    def validate_limits(self, *, max_items: int, max_bytes: int, timeout_ms: int) -> None:
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 3:
            raise MemorySidecarPolicyError("maxItems must be between 1 and 3")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 6000:
            raise MemorySidecarPolicyError("maxBytes must be between 1 and 6000")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= 800:
            raise MemorySidecarPolicyError("timeoutMs must be between 1 and 800")

    def is_allowed_kind(self, kind: str) -> bool:
        return kind in self.allowed_kinds

    def is_denied_path(self, path: str) -> bool:
        from fnmatch import fnmatch
        return any(fnmatch(path, pattern) for pattern in self.denied_patterns)
