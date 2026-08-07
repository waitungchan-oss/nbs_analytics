from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .evidence_models import canonical_fingerprint
from .memory_sidecar_hint_models import MemoryHints
from .memory_sidecar_models import (
    MEMORY_DENIED_PATTERNS,
    MemoryCandidate,
    MemorySidecarProviderMetadata,
)

MEMORY_RECALL_REQUEST_SCHEMA = "memory-recall-request-v1"
TASK_ALLOWLIST_MAX_ITEMS = 64
PAYLOAD_PATH_ALLOWLIST_MAX_ITEMS = 64
PAYLOAD_PATHS_MAX_ITEMS = 16
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABS_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class RecallLimits:
    max_items: int = 3
    max_bytes: int = 6000
    timeout_ms: int = 800

    def __post_init__(self) -> None:
        for key, value in (("max_items", self.max_items), ("max_bytes", self.max_bytes), ("timeout_ms", self.timeout_ms)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MemorySidecarProviderError("limits_invalid", f"{key} must be an integer")
        if not (0 < self.max_items <= 3):
            raise MemorySidecarProviderError("limits_unbounded", "max_items must stay within the 3-item cap")
        if not (0 < self.max_bytes <= 6000):
            raise MemorySidecarProviderError("limits_unbounded", "max_bytes must stay within the 6000-byte cap")
        if not (0 < self.timeout_ms <= 800):
            raise MemorySidecarProviderError("limits_unbounded", "timeout_ms must stay within the 800 ms budget")


@dataclass(frozen=True)
class WriteResult:
    status: str
    memory_id: str | None = None
    error_code: str | None = None


class MemorySidecarProviderError(RuntimeError):
    def __init__(self, code: str, summary: str) -> None:
        self.code = str(code)
        self.summary = str(summary)
        super().__init__(f"{self.code}: {self.summary}")


class MemorySidecarProvider(Protocol):
    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints:
        ...

    def write_candidate(self, candidate: MemoryCandidate) -> WriteResult:
        ...


def _sha256_fingerprint(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise MemorySidecarProviderError("schema_mismatch", f"{key} must be a lowercase SHA-256")
    return value


def _validate_payload_path(path: Any) -> str:
    if not isinstance(path, str) or not path or len(path) > 256:
        raise MemorySidecarProviderError("path_violation", "payload path is invalid or unbounded")
    if (
        path.startswith("/")
        or "\\" in path
        or ".." in path
        or _ABS_WINDOWS_PATH_RE.match(path)
        or _URL_SCHEME_RE.match(path)
    ):
        raise MemorySidecarProviderError("path_violation", "payload path is unsafe")
    if any(fnmatch.fnmatch(path, pattern) for pattern in MEMORY_DENIED_PATTERNS):
        raise MemorySidecarProviderError("sensitive_capture", "payload path is denied by policy")
    return path


@dataclass(frozen=True)
class MemorySidecarTaskAllowlist:
    """Bounded registry of declared task fingerprints.

    A recall request may only reference a task whose fingerprint was declared
    here; anything else is an undeclared task and fails closed.
    """

    fingerprints: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.fingerprints, frozenset):
            object.__setattr__(self, "fingerprints", frozenset(self.fingerprints))
        if not self.fingerprints or len(self.fingerprints) > TASK_ALLOWLIST_MAX_ITEMS:
            raise MemorySidecarProviderError("allowlist_unbounded", "task allowlist must contain 1..64 fingerprints")
        for fingerprint in self.fingerprints:
            _sha256_fingerprint(fingerprint, "taskFingerprint")

    def contains(self, task_fingerprint: str) -> bool:
        return _sha256_fingerprint(task_fingerprint, "taskFingerprint") in self.fingerprints

    def validate_task(self, task_fingerprint: str) -> None:
        if not self.contains(task_fingerprint):
            raise MemorySidecarProviderError("undeclared_task", "task fingerprint is not allowlisted")


@dataclass(frozen=True)
class MemorySidecarPayloadPathAllowlist:
    """Bounded registry of declared payload paths.

    Every payload path on a recall request must be declared here in addition
    to passing the denylist/safety checks; anything else fails closed as a
    path violation.
    """

    paths: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.paths, frozenset):
            object.__setattr__(self, "paths", frozenset(self.paths))
        if not self.paths or len(self.paths) > PAYLOAD_PATH_ALLOWLIST_MAX_ITEMS:
            raise MemorySidecarProviderError("allowlist_unbounded", "payload path allowlist must contain 1..64 paths")
        validated = frozenset(_validate_payload_path(path) for path in self.paths)
        object.__setattr__(self, "paths", validated)

    def contains(self, path: str) -> bool:
        return _validate_payload_path(path) in self.paths

    def validate_path(self, path: str) -> None:
        if not self.contains(path):
            raise MemorySidecarProviderError("path_violation", "payload path is not declared in the allowlist")


@dataclass(frozen=True)
class MemorySidecarRecallRequest:
    """Immutable controlled recall request with a deterministic fingerprint.

    Binds the query, the declared task, the provider/model identity, bounded
    limits, a declared payload path allowlist and the requested payload paths.
    Absolute paths, secret/SQLite/CSV/log payloads, undeclared paths and
    undeclared tasks are rejected at construction.
    """

    query: str
    query_fingerprint: str
    task_fingerprint: str
    provider_metadata: MemorySidecarProviderMetadata
    task_allowlist: MemorySidecarTaskAllowlist
    payload_path_allowlist: MemorySidecarPayloadPathAllowlist
    limits: RecallLimits
    payload_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip() or len(self.query.encode("utf-8")) > 2048:
            raise MemorySidecarProviderError("schema_mismatch", "query is invalid or unbounded")
        _sha256_fingerprint(self.query_fingerprint, "queryFingerprint")
        if self.query_fingerprint != canonical_fingerprint({"query": self.query}):
            raise MemorySidecarProviderError("schema_mismatch", "queryFingerprint does not match query")
        _sha256_fingerprint(self.task_fingerprint, "taskFingerprint")
        if not isinstance(self.provider_metadata, MemorySidecarProviderMetadata):
            raise MemorySidecarProviderError("schema_mismatch", "provider metadata is invalid")
        if self.provider_metadata.writer_enabled:
            raise MemorySidecarProviderError("writer_disabled", "writer must stay disabled in this phase")
        if not isinstance(self.task_allowlist, MemorySidecarTaskAllowlist):
            raise MemorySidecarProviderError("schema_mismatch", "task allowlist is invalid")
        self.task_allowlist.validate_task(self.task_fingerprint)
        if not isinstance(self.payload_path_allowlist, MemorySidecarPayloadPathAllowlist):
            raise MemorySidecarProviderError("schema_mismatch", "payload path allowlist is invalid")
        if not isinstance(self.limits, RecallLimits):
            raise MemorySidecarProviderError("schema_mismatch", "limits are invalid")
        if not isinstance(self.payload_paths, (tuple, list)) or len(self.payload_paths) > PAYLOAD_PATHS_MAX_ITEMS:
            raise MemorySidecarProviderError("schema_mismatch", "payload paths are invalid or over the item cap")
        validated_paths = tuple(_validate_payload_path(path) for path in self.payload_paths)
        for path in validated_paths:
            self.payload_path_allowlist.validate_path(path)
        object.__setattr__(self, "payload_paths", validated_paths)

    @property
    def schema_version(self) -> str:
        return MEMORY_RECALL_REQUEST_SCHEMA

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": MEMORY_RECALL_REQUEST_SCHEMA,
            "query": self.query,
            "queryFingerprint": self.query_fingerprint,
            "taskFingerprint": self.task_fingerprint,
            "providerMetadata": self.provider_metadata.to_dict(),
            "taskAllowlist": sorted(self.task_allowlist.fingerprints),
            "payloadPathAllowlist": sorted(self.payload_path_allowlist.paths),
            "payloadPaths": sorted(self.payload_paths),
            "limits": {
                "maxItems": self.limits.max_items,
                "maxBytes": self.limits.max_bytes,
                "timeoutMs": self.limits.timeout_ms,
            },
        }

    def recompute_fingerprint(self) -> str:
        return canonical_fingerprint(self._unsigned())

    @property
    def request_fingerprint(self) -> str:
        return self.recompute_fingerprint()

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "requestFingerprint": self.request_fingerprint}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemorySidecarRecallRequest":
        expected = {
            "schemaVersion", "query", "queryFingerprint", "taskFingerprint",
            "providerMetadata", "taskAllowlist", "payloadPathAllowlist",
            "payloadPaths", "limits", "requestFingerprint",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schemaVersion") != MEMORY_RECALL_REQUEST_SCHEMA:
            raise MemorySidecarProviderError("schema_mismatch", "recall request envelope is invalid")
        limits_payload = payload["limits"]
        if not isinstance(limits_payload, Mapping) or set(limits_payload) != {"maxItems", "maxBytes", "timeoutMs"}:
            raise MemorySidecarProviderError("schema_mismatch", "limits are invalid")
        result = cls(
            query=payload["query"],
            query_fingerprint=payload["queryFingerprint"],
            task_fingerprint=payload["taskFingerprint"],
            provider_metadata=MemorySidecarProviderMetadata.from_dict(payload["providerMetadata"]),
            task_allowlist=MemorySidecarTaskAllowlist(frozenset(payload["taskAllowlist"])),
            payload_path_allowlist=MemorySidecarPayloadPathAllowlist(frozenset(payload["payloadPathAllowlist"])),
            limits=RecallLimits(
                max_items=limits_payload["maxItems"],
                max_bytes=limits_payload["maxBytes"],
                timeout_ms=limits_payload["timeoutMs"],
            ),
            payload_paths=tuple(payload["payloadPaths"]),
        )
        if payload["requestFingerprint"] != result.request_fingerprint:
            raise MemorySidecarProviderError("schema_mismatch", "request fingerprint does not match payload")
        return result


class FakeMemorySidecarProvider:
    def __init__(
        self,
        recall_results: Mapping[str, MemoryHints] | None = None,
        write_results: Mapping[str, WriteResult] | None = None,
        raise_error: MemorySidecarProviderError | None = None,
        writer_enabled: bool = False,
    ) -> None:
        self.recall_results = dict(recall_results or {})
        self.write_results = dict(write_results or {})
        self.raise_error = raise_error
        self.writer_enabled = bool(writer_enabled)
        self.recall_calls = 0
        self.write_calls = 0
        self.default_limits = RecallLimits()

    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints:
        self.recall_calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.recall_results.get(
            query_fingerprint,
            MemoryHints.empty(query_fingerprint=query_fingerprint, status="empty"),
        )

    def write_candidate(self, candidate: MemoryCandidate) -> WriteResult:
        self.write_calls += 1
        if not self.writer_enabled:
            return WriteResult(status="disabled", error_code="writer_disabled")
        return self.write_results.get(candidate.memory_id, WriteResult(status="written", memory_id=candidate.memory_id))

