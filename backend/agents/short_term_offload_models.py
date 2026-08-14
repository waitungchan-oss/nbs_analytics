from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import re
from typing import Mapping

from .short_term_offload_policy import ShortTermOffloadPolicy


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset({"schemaVersion", "refId", "runId", "sessionId", "sourceKind", "summary", "content",
                     "contentSha256", "createdAt", "expiresAt", "sourceFingerprint", "redactionStatus", "status"})
_REDACTION = frozenset({"clean", "redacted", "blocked"})
_STATUS = frozenset({"ready", "expired", "blocked", "missing"})


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed


@dataclass(frozen=True)
class ShortTermOffloadArtifact:
    schema_version: str
    ref_id: str
    run_id: str
    session_id: str
    source_kind: str
    summary: str
    content: str
    content_sha256: str
    created_at: datetime
    expires_at: datetime
    source_fingerprint: str
    redaction_status: str
    status: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ShortTermOffloadArtifact":
        if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
            raise ValueError("artifact keys mismatch")
        policy = ShortTermOffloadPolicy()
        for key in ("refId", "runId", "sessionId"):
            policy.validate_ref_id(payload[key])
        if payload["schemaVersion"] != "short-term-offload-v1" or payload["sourceKind"] != "tool_output":
            raise ValueError("artifact schema mismatch")
        if not isinstance(payload["summary"], str) or len(payload["summary"].encode()) > policy.max_summary_bytes:
            raise ValueError("summary cap")
        if not isinstance(payload["content"], str) or len(payload["content"].encode()) > policy.max_content_bytes:
            raise ValueError("content cap")
        expected = sha256(payload["content"].encode()).hexdigest()
        if payload["contentSha256"] != expected or not _SHA256.fullmatch(str(payload["contentSha256"])):
            raise ValueError("content fingerprint mismatch")
        if not _SHA256.fullmatch(str(payload["sourceFingerprint"])):
            raise ValueError("source fingerprint")
        if payload["redactionStatus"] not in _REDACTION or payload["status"] not in _STATUS:
            raise ValueError("artifact status")
        created, expires = _timestamp(payload["createdAt"]), _timestamp(payload["expiresAt"])
        policy.validate_ttl(created, expires)
        if payload["status"] == "ready" and payload["redactionStatus"] == "blocked":
            raise ValueError("blocked artifact cannot be ready")
        if payload["status"] == "blocked" and (payload["redactionStatus"] != "blocked" or payload["content"]):
            raise ValueError("blocked artifact cannot contain content")
        return cls(payload["schemaVersion"], payload["refId"], payload["runId"], payload["sessionId"],
                   payload["sourceKind"], payload["summary"], payload["content"], payload["contentSha256"],
                   created, expires, payload["sourceFingerprint"], payload["redactionStatus"], payload["status"])

    def to_dict(self) -> dict[str, object]:
        return {"schemaVersion": self.schema_version, "refId": self.ref_id, "runId": self.run_id,
                "sessionId": self.session_id, "sourceKind": self.source_kind, "summary": self.summary,
                "content": self.content, "contentSha256": self.content_sha256, "createdAt": self.created_at.isoformat(),
                "expiresAt": self.expires_at.isoformat(), "sourceFingerprint": self.source_fingerprint,
                "redactionStatus": self.redaction_status, "status": self.status}


@dataclass(frozen=True)
class ShortTermOffloadReference:
    ref_id: str
    run_id: str
    session_id: str
    summary: str
    content_sha256: str
    expires_at: datetime
    node_id: str

    @classmethod
    def from_artifact(cls, artifact: ShortTermOffloadArtifact) -> "ShortTermOffloadReference":
        return cls(artifact.ref_id, artifact.run_id, artifact.session_id, artifact.summary,
                   artifact.content_sha256, artifact.expires_at, f"tool-output-{artifact.ref_id.rsplit('_', 1)[-1]}")
