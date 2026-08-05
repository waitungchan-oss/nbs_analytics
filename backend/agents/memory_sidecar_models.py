from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from fnmatch import fnmatch
from typing import Any, Mapping, Sequence

from .evidence_models import canonical_fingerprint


MEMORY_CANDIDATE_SCHEMA = "memory-candidate-v1"
MEMORY_HINTS_SCHEMA = "memory-hints-v1"
MEMORY_KINDS = frozenset({"decision", "sop", "failure_pattern", "verification_pattern", "preference"})
MEMORY_CANDIDATE_STATUSES = frozenset({"completed"})
MEMORY_HINT_STATUSES = frozenset({"ready", "empty", "timeout", "degraded"})
FRESHNESS_VALUES = frozenset({"fresh", "stale", "unknown"})
CONFIDENCE_VALUES = frozenset({"high", "medium"})
MEMORY_TTL = timedelta(days=90)
MEMORY_DENIED_PATTERNS = (".env", "**/.env", "*.db", "*.sqlite", "*.csv", "*.xlsx", "*.log", "credentials/**", "**/credentials/**", "Secrets/**", "**/Secrets/**")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^run-[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,256}$")


class MemorySidecarSchemaError(ValueError):
    """Raised when a bounded memory sidecar payload is malformed or unsafe."""


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise MemorySidecarSchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _safe_text(value: Any, key: str, *, max_chars: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > max_chars or any(ord(char) < 32 for char in value):
        raise MemorySidecarSchemaError(f"{key} is invalid or unbounded")
    if value.startswith("/") or "\\" in value or ".." in value:
        raise MemorySidecarSchemaError(f"{key} exposes an unsafe path")
    return value


def _summary(value: Any, key: str = "summary") -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise MemorySidecarSchemaError(f"{key} is invalid")
    if len(value.encode("utf-8")) > 2048:
        raise MemorySidecarSchemaError(f"{key} exceeds the 2048-byte cap")
    if value.startswith("/") or "\\" in value:
        raise MemorySidecarSchemaError(f"{key} must not expose a path")
    return value


def _validate_artifact_path(value: Any) -> str:
    if not isinstance(value, str) or not _PATH_RE.fullmatch(value) or value.startswith("/") or ".." in value or re.match(r"^[A-Za-z]:[\\/]", value) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        raise MemorySidecarSchemaError("sourceRef.artifactPath is unsafe")
    if any(fnmatch(value, pattern) for pattern in MEMORY_DENIED_PATTERNS):
        raise MemorySidecarSchemaError("sourceRef.artifactPath is denied by policy")
    return value


def _timestamp(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise MemorySidecarSchemaError(f"{key} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MemorySidecarSchemaError(f"{key} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemorySidecarSchemaError(f"{key} must include a timezone")
    return value


def _freeze_source_refs(source_refs: Sequence["MemorySourceRef"]) -> tuple["MemorySourceRef", ...]:
    values = tuple(sorted(source_refs, key=lambda item: (item.run_id, item.artifact_path, item.artifact_sha256, item.commit or "")))
    if not values or len(values) > 16 or len({item.identity for item in values}) != len(values):
        raise MemorySidecarSchemaError("sourceRefs must be unique and contain 1..16 items")
    return values


@dataclass(frozen=True)
class MemorySourceRef:
    run_id: str
    artifact_path: str
    artifact_sha256: str
    commit: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not _RUN_ID_RE.fullmatch(self.run_id):
            raise MemorySidecarSchemaError("sourceRef.runId is invalid")
        _validate_artifact_path(self.artifact_path)
        _sha(self.artifact_sha256, "sourceRef.artifactSha256")
        if self.commit is not None and (not isinstance(self.commit, str) or not _COMMIT_RE.fullmatch(self.commit)):
            raise MemorySidecarSchemaError("sourceRef.commit is invalid")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemorySourceRef":
        if not isinstance(payload, Mapping) or set(payload) != {"runId", "artifactPath", "artifactSha256", "commit"}:
            raise MemorySidecarSchemaError("sourceRef keys are invalid")
        run_id = payload["runId"]
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise MemorySidecarSchemaError("sourceRef.runId is invalid")
        path = payload["artifactPath"]
        path = _validate_artifact_path(path)
        commit = payload["commit"]
        if commit is not None and (not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit)):
            raise MemorySidecarSchemaError("sourceRef.commit is invalid")
        return cls(run_id, path, _sha(payload["artifactSha256"], "sourceRef.artifactSha256"), commit)

    @property
    def identity(self) -> str:
        return f"{self.run_id}/{self.artifact_path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "artifactPath": self.artifact_path,
            "artifactSha256": self.artifact_sha256,
            "commit": self.commit,
        }


@dataclass(frozen=True)
class MemoryCandidate:
    kind: str
    summary: str
    source_refs: tuple[MemorySourceRef, ...]
    source_status: str
    generated_at: str
    expires_at: str
    confidence: str
    policy_version: str
    memory_id: str
    memory_fingerprint: str

    def __post_init__(self) -> None:
        if self.kind not in MEMORY_KINDS or self.source_status not in MEMORY_CANDIDATE_STATUSES:
            raise MemorySidecarSchemaError("memory candidate fields are invalid")
        _summary(self.summary)
        _timestamp(self.generated_at, "freshness.generatedAt")
        _timestamp(self.expires_at, "freshness.expiresAt")
        generated = datetime.fromisoformat(self.generated_at)
        expires = datetime.fromisoformat(self.expires_at)
        if expires <= generated or expires - generated > MEMORY_TTL:
            raise MemorySidecarSchemaError("freshness interval exceeds the 90-day TTL")
        if self.confidence not in CONFIDENCE_VALUES:
            raise MemorySidecarSchemaError("confidence is invalid")
        _safe_text(self.policy_version, "freshness.policyVersion")
        object.__setattr__(self, "source_refs", _freeze_source_refs(self.source_refs))
        _sha(self.memory_id, "memoryId")
        _sha(self.memory_fingerprint, "memoryFingerprint")
        unsigned = self._unsigned()
        if self.memory_id != canonical_fingerprint(unsigned):
            raise MemorySidecarSchemaError("memoryId does not match candidate fields")
        if self.memory_fingerprint != canonical_fingerprint({**unsigned, "memoryId": self.memory_id}):
            raise MemorySidecarSchemaError("memoryFingerprint does not match candidate fields")

    @classmethod
    def from_parts(
        cls, *, kind: str, summary: str, source_refs: Sequence[MemorySourceRef], source_status: str,
        generated_at: str, expires_at: str, confidence: str, policy_version: str,
    ) -> "MemoryCandidate":
        if kind not in MEMORY_KINDS:
            raise MemorySidecarSchemaError("kind is not allowlisted")
        if source_status not in MEMORY_CANDIDATE_STATUSES:
            raise MemorySidecarSchemaError("sourceStatus is invalid")
        generated_at = _timestamp(generated_at, "freshness.generatedAt")
        expires_at = _timestamp(expires_at, "freshness.expiresAt")
        generated = datetime.fromisoformat(generated_at)
        expires = datetime.fromisoformat(expires_at)
        if expires <= generated or expires - generated > MEMORY_TTL:
            raise MemorySidecarSchemaError("freshness interval exceeds the 90-day TTL")
        if confidence not in CONFIDENCE_VALUES:
            raise MemorySidecarSchemaError("confidence is invalid")
        policy = _safe_text(policy_version, "freshness.policyVersion")
        normalized_refs = _freeze_source_refs(source_refs)
        unsigned = {
            "schemaVersion": MEMORY_CANDIDATE_SCHEMA,
            "kind": kind,
            "summary": _summary(summary),
            "sourceRefs": [item.to_dict() for item in normalized_refs],
            "sourceStatus": source_status,
            "freshness": {"generatedAt": generated_at, "expiresAt": expires_at, "policyVersion": policy},
            "confidence": confidence,
        }
        return cls(
            kind, unsigned["summary"], normalized_refs, source_status, generated_at, expires_at, confidence,
            policy, canonical_fingerprint(unsigned), canonical_fingerprint({**unsigned, "memoryId": canonical_fingerprint(unsigned)}),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryCandidate":
        expected = {"schemaVersion", "memoryId", "kind", "summary", "sourceRefs", "sourceStatus", "freshness", "confidence", "memoryFingerprint"}
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schemaVersion") != MEMORY_CANDIDATE_SCHEMA:
            raise MemorySidecarSchemaError("memory candidate envelope is invalid")
        freshness = payload["freshness"]
        if not isinstance(freshness, Mapping) or set(freshness) != {"generatedAt", "expiresAt", "policyVersion"}:
            raise MemorySidecarSchemaError("freshness is invalid")
        refs = payload["sourceRefs"]
        if not isinstance(refs, (list, tuple)):
            raise MemorySidecarSchemaError("sourceRefs is invalid")
        result = cls.from_parts(
            kind=payload["kind"], summary=payload["summary"],
            source_refs=tuple(MemorySourceRef.from_dict(item) for item in refs),
            source_status=payload["sourceStatus"], generated_at=freshness["generatedAt"], expires_at=freshness["expiresAt"],
            confidence=payload["confidence"], policy_version=freshness["policyVersion"],
        )
        if payload["memoryId"] != result.memory_id or payload["memoryFingerprint"] != result.memory_fingerprint:
            raise MemorySidecarSchemaError("memory fingerprint does not match payload")
        return result

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": MEMORY_CANDIDATE_SCHEMA, "kind": self.kind, "summary": self.summary,
            "sourceRefs": [item.to_dict() for item in self.source_refs], "sourceStatus": self.source_status,
            "freshness": {"generatedAt": self.generated_at, "expiresAt": self.expires_at, "policyVersion": self.policy_version},
            "confidence": self.confidence,
        }

    def recompute_fingerprint(self) -> str:
        unsigned = self._unsigned()
        return canonical_fingerprint({**unsigned, "memoryId": canonical_fingerprint(unsigned)})

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "memoryId": self.memory_id, "memoryFingerprint": self.memory_fingerprint}

__all__ = [
    "MemoryCandidate", "MemorySidecarSchemaError", "MemorySourceRef",
]
