from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .evidence_models import canonical_fingerprint


MEMORY_SOURCE_SCHEMA = "memory-source-v1"
MEMORY_RECORD_SCHEMA = "memory-record-v1"
MEMORY_QUERY_SCHEMA = "memory-query-v1"
MEMORY_QUERY_RESULT_SCHEMA = "memory-query-result-v1"
MEMORY_ACL_SCHEMA = "memory-acl-decision-v1"
SOURCE_KINDS = frozenset({"governance_document", "verified_evidence", "approved_skill"})
MEMORY_KINDS = frozenset({"governance", "evidence", "skill"})
SCOPES = frozenset({"project", "agent", "team"})
SOURCE_STATUSES = frozenset({"verified", "stale", "blocked"})
RECORD_FRESHNESS = frozenset({"fresh", "stale", "unknown"})
RECORD_STATUSES = frozenset({"ready", "empty", "blocked"})
QUERY_STATUSES = frozenset({"ready", "empty", "timeout", "degraded", "blocked"})
ACL_DECISIONS = frozenset({"allow", "deny", "blocked"})
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_DENIED_PARTS = {".env", "credentials", "secrets", "secret", "token", "private_key"}
_DENIED_SUFFIXES = (".sqlite", ".db", ".csv", ".xlsx", ".xls", ".log")


class MemoryHubSchemaError(ValueError):
    """Raised when a Memory Hub contract is malformed or unsafe."""


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise MemoryHubSchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _safe_id(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise MemoryHubSchemaError(f"{key} is invalid")
    return value


def _safe_owner(value: Any, key: str = "owner") -> str:
    if not isinstance(value, str) or not _SAFE_OWNER.fullmatch(value):
        raise MemoryHubSchemaError(f"{key} is invalid")
    return value


def _timestamp(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise MemoryHubSchemaError(f"{key} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MemoryHubSchemaError(f"{key} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemoryHubSchemaError(f"{key} must include timezone")
    return value


def _freshness(generated_at: str, expires_at: str) -> None:
    generated = datetime.fromisoformat(generated_at)
    expires = datetime.fromisoformat(expires_at)
    if expires <= generated or (expires - generated).days > 90:
        raise MemoryHubSchemaError("freshness interval must be positive and no longer than 90 days")


def _artifact_ref(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise MemoryHubSchemaError("artifactRef is invalid")
    if value.startswith("/") or "\\" in value or ".." in value or "://" in value:
        raise MemoryHubSchemaError("artifactRef must be a relative path")
    parts = tuple(part.lower() for part in value.split("/"))
    if any(part in _DENIED_PARTS for part in parts) or value.lower().endswith(_DENIED_SUFFIXES):
        raise MemoryHubSchemaError("artifactRef is denied by policy")
    return value


def _summary(value: Any) -> str:
    if not isinstance(value, str) or not value or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise MemoryHubSchemaError("summary is invalid")
    if len(value.encode("utf-8")) > 2048 or value.startswith("/") or "\\" in value:
        raise MemoryHubSchemaError("summary exceeds bound or exposes a path")
    return value


def _exact(payload: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise MemoryHubSchemaError(f"{label} keys are invalid")


@dataclass(frozen=True)
class MemorySource:
    source_kind: str
    artifact_ref: str
    artifact_sha256: str
    run_id: str | None
    git_head: str | None
    scope: str
    owner: str
    status: str
    generated_at: str
    expires_at: str
    policy_version: str
    source_id: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        if self.source_kind not in SOURCE_KINDS or self.scope not in SCOPES or self.status not in SOURCE_STATUSES:
            raise MemoryHubSchemaError("source kind, scope or status is invalid")
        _artifact_ref(self.artifact_ref)
        _sha(self.artifact_sha256, "artifactSha256")
        _safe_owner(self.owner)
        if self.run_id is not None:
            _safe_id(self.run_id, "runId")
        if self.git_head is not None and not _HEAD.fullmatch(self.git_head):
            raise MemoryHubSchemaError("gitHead is invalid")
        if self.source_kind in {"verified_evidence", "approved_skill"} and (self.run_id is None or self.git_head is None or self.status != "verified"):
            raise MemoryHubSchemaError("verified sources require a completed-run identity")
        _timestamp(self.generated_at, "freshness.generatedAt")
        _timestamp(self.expires_at, "freshness.expiresAt")
        _freshness(self.generated_at, self.expires_at)
        _safe_owner(self.policy_version, "freshness.policyVersion")
        _sha(self.source_id, "sourceId")
        _sha(self.source_fingerprint, "sourceFingerprint")
        unsigned = self._unsigned()
        if self.source_id != canonical_fingerprint(unsigned):
            raise MemoryHubSchemaError("sourceId does not match canonical fields")
        if self.source_fingerprint != canonical_fingerprint({**unsigned, "sourceId": self.source_id}):
            raise MemoryHubSchemaError("sourceFingerprint does not match canonical fields")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": MEMORY_SOURCE_SCHEMA,
            "sourceKind": self.source_kind,
            "artifactRef": self.artifact_ref,
            "artifactSha256": self.artifact_sha256,
            "runId": self.run_id,
            "gitHead": self.git_head,
            "scope": self.scope,
            "owner": self.owner,
            "status": self.status,
            "freshness": {"generatedAt": self.generated_at, "expiresAt": self.expires_at, "policyVersion": self.policy_version},
        }

    @classmethod
    def from_parts(cls, **values: Any) -> "MemorySource":
        normalized = {
            "sourceKind": values["source_kind"], "artifactRef": _artifact_ref(values["artifact_ref"]),
            "artifactSha256": _sha(values["artifact_sha256"], "artifactSha256"), "runId": values.get("run_id"),
            "gitHead": values.get("git_head"), "scope": values["scope"], "owner": values["owner"],
            "status": values["status"], "freshness": {"generatedAt": values["generated_at"], "expiresAt": values["expires_at"], "policyVersion": values["policy_version"]},
        }
        generated = _timestamp(normalized["freshness"]["generatedAt"], "freshness.generatedAt")
        expires = _timestamp(normalized["freshness"]["expiresAt"], "freshness.expiresAt")
        unsigned = {**normalized, "schemaVersion": MEMORY_SOURCE_SCHEMA, "freshness": {**normalized["freshness"], "generatedAt": generated, "expiresAt": expires}}
        source_id = canonical_fingerprint(unsigned)
        return cls(values["source_kind"], values["artifact_ref"], values["artifact_sha256"], values.get("run_id"), values.get("git_head"), values["scope"], values["owner"], values["status"], generated, expires, values["policy_version"], source_id, canonical_fingerprint({**unsigned, "sourceId": source_id}))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemorySource":
        _exact(payload, {"schemaVersion", "sourceId", "sourceKind", "artifactRef", "artifactSha256", "runId", "gitHead", "scope", "owner", "status", "freshness", "sourceFingerprint"}, "memory source")
        if payload["schemaVersion"] != MEMORY_SOURCE_SCHEMA or not isinstance(payload["freshness"], Mapping):
            raise MemoryHubSchemaError("memory source envelope is invalid")
        _exact(payload["freshness"], {"generatedAt", "expiresAt", "policyVersion"}, "source freshness")
        result = cls.from_parts(source_kind=payload["sourceKind"], artifact_ref=payload["artifactRef"], artifact_sha256=payload["artifactSha256"], run_id=payload["runId"], git_head=payload["gitHead"], scope=payload["scope"], owner=payload["owner"], status=payload["status"], generated_at=payload["freshness"]["generatedAt"], expires_at=payload["freshness"]["expiresAt"], policy_version=payload["freshness"]["policyVersion"])
        if payload["sourceId"] != result.source_id or payload["sourceFingerprint"] != result.source_fingerprint:
            raise MemoryHubSchemaError("source identity fingerprint mismatch")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "sourceId": self.source_id, "sourceFingerprint": self.source_fingerprint}


@dataclass(frozen=True)
class MemoryRecord:
    memory_kind: str
    summary: str
    source_refs: tuple[MemorySource, ...]
    scope: str
    owner: str
    freshness: str
    status: str
    memory_id: str
    record_fingerprint: str

    def __post_init__(self) -> None:
        if self.memory_kind not in MEMORY_KINDS or self.scope not in SCOPES or self.freshness not in RECORD_FRESHNESS or self.status not in RECORD_STATUSES:
            raise MemoryHubSchemaError("memory record fields are invalid")
        _summary(self.summary)
        _safe_owner(self.owner)
        refs = tuple(sorted(self.source_refs, key=lambda item: item.source_id))
        if not refs or len(refs) > 16 or len({item.source_id for item in refs}) != len(refs):
            raise MemoryHubSchemaError("sourceRefs must contain 1..16 unique sources")
        if any(item.status != "verified" or item.scope != self.scope for item in refs):
            raise MemoryHubSchemaError("record sources must be verified and scope-matching")
        object.__setattr__(self, "source_refs", refs)
        _sha(self.memory_id, "memoryId")
        _sha(self.record_fingerprint, "recordFingerprint")
        unsigned = self._unsigned()
        if self.memory_id != canonical_fingerprint(unsigned) or self.record_fingerprint != canonical_fingerprint({**unsigned, "memoryId": self.memory_id}):
            raise MemoryHubSchemaError("memory record fingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {"schemaVersion": MEMORY_RECORD_SCHEMA, "memoryKind": self.memory_kind, "summary": self.summary, "sourceRefs": [item.source_id for item in self.source_refs], "scope": self.scope, "owner": self.owner, "freshness": self.freshness, "status": self.status}

    @classmethod
    def from_parts(cls, *, memory_kind: str, summary: str, source_refs: Sequence[MemorySource], scope: str, owner: str, freshness: str, status: str) -> "MemoryRecord":
        refs = tuple(sorted(source_refs, key=lambda item: item.source_id))
        unsigned = {"schemaVersion": MEMORY_RECORD_SCHEMA, "memoryKind": memory_kind, "summary": _summary(summary), "sourceRefs": [item.source_id for item in refs], "scope": scope, "owner": owner, "freshness": freshness, "status": status}
        memory_id = canonical_fingerprint(unsigned)
        return cls(memory_kind, unsigned["summary"], refs, scope, owner, freshness, status, memory_id, canonical_fingerprint({**unsigned, "memoryId": memory_id}))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], source_index: Mapping[str, MemorySource] | None = None) -> "MemoryRecord":
        _exact(payload, {"schemaVersion", "memoryId", "memoryKind", "summary", "sourceRefs", "scope", "owner", "freshness", "status", "recordFingerprint"}, "memory record")
        if payload["schemaVersion"] != MEMORY_RECORD_SCHEMA or not isinstance(payload["sourceRefs"], list) or source_index is None:
            raise MemoryHubSchemaError("memory record requires catalog source resolution")
        refs = tuple(source_index.get(source_id) for source_id in payload["sourceRefs"])
        if any(ref is None for ref in refs):
            raise MemoryHubSchemaError("memory record source reference is missing")
        result = cls.from_parts(memory_kind=payload["memoryKind"], summary=payload["summary"], source_refs=refs, scope=payload["scope"], owner=payload["owner"], freshness=payload["freshness"], status=payload["status"])
        if payload["memoryId"] != result.memory_id or payload["recordFingerprint"] != result.record_fingerprint:
            raise MemoryHubSchemaError("memory record fingerprint mismatch")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "memoryId": self.memory_id, "recordFingerprint": self.record_fingerprint}


@dataclass(frozen=True)
class RuntimeIdentity:
    project_id: str
    consumer_id: str
    team_id: str | None

    @classmethod
    def from_parts(cls, *, project_id: str, consumer_id: str, team_id: str | None = None) -> "RuntimeIdentity":
        _safe_id(project_id, "projectId")
        _safe_id(consumer_id, "consumerId")
        if team_id is not None:
            _safe_id(team_id, "teamId")
        return cls(project_id, consumer_id, team_id)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuntimeIdentity":
        _exact(payload, {"projectId", "consumerId", "teamId"}, "runtime identity")
        return cls.from_parts(project_id=payload["projectId"], consumer_id=payload["consumerId"], team_id=payload["teamId"])

    def to_dict(self) -> dict[str, Any]:
        return {"projectId": self.project_id, "consumerId": self.consumer_id, "teamId": self.team_id}


@dataclass(frozen=True)
class MemoryQuery:
    query: str
    consumer_id: str
    scope: str
    memory_kinds: tuple[str, ...]
    max_items: int
    max_bytes: int
    timeout_ms: int
    query_fingerprint: str

    @classmethod
    def from_parts(cls, *, query: str, consumer_id: str, scope: str, memory_kinds: Sequence[str], max_items: int = 3, max_bytes: int = 6000, timeout_ms: int = 800) -> "MemoryQuery":
        if not isinstance(query, str) or not query.strip() or len(query) > 512 or any(ord(ch) < 32 for ch in query):
            raise MemoryHubSchemaError("query is invalid or unbounded")
        _safe_id(consumer_id, "consumerId")
        if scope not in SCOPES or not memory_kinds or any(kind not in MEMORY_KINDS for kind in memory_kinds) or len(set(memory_kinds)) != len(tuple(memory_kinds)):
            raise MemoryHubSchemaError("query scope or memoryKinds is invalid")
        if (max_items, max_bytes, timeout_ms) != (3, 6000, 800):
            raise MemoryHubSchemaError("query limits are fixed at 3/6000/800")
        unsigned = {"schemaVersion": MEMORY_QUERY_SCHEMA, "query": query.strip(), "consumerId": consumer_id, "scope": scope, "memoryKinds": sorted(memory_kinds), "maxItems": max_items, "maxBytes": max_bytes, "timeoutMs": timeout_ms}
        return cls(unsigned["query"], consumer_id, scope, tuple(unsigned["memoryKinds"]), max_items, max_bytes, timeout_ms, canonical_fingerprint(unsigned))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryQuery":
        _exact(payload, {"schemaVersion", "query", "consumerId", "scope", "memoryKinds", "maxItems", "maxBytes", "timeoutMs", "queryFingerprint"}, "memory query")
        if payload["schemaVersion"] != MEMORY_QUERY_SCHEMA:
            raise MemoryHubSchemaError("query schemaVersion is invalid")
        result = cls.from_parts(query=payload["query"], consumer_id=payload["consumerId"], scope=payload["scope"], memory_kinds=payload["memoryKinds"], max_items=payload["maxItems"], max_bytes=payload["maxBytes"], timeout_ms=payload["timeoutMs"])
        if payload["queryFingerprint"] != result.query_fingerprint:
            raise MemoryHubSchemaError("queryFingerprint mismatch")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"schemaVersion": MEMORY_QUERY_SCHEMA, "query": self.query, "consumerId": self.consumer_id, "scope": self.scope, "memoryKinds": list(self.memory_kinds), "maxItems": self.max_items, "maxBytes": self.max_bytes, "timeoutMs": self.timeout_ms, "queryFingerprint": self.query_fingerprint}


@dataclass(frozen=True)
class MemoryACLDecision:
    consumer_id: str
    requested_scope: str
    record_scope: str
    decision: str
    reason: str
    decision_fingerprint: str

    def __post_init__(self) -> None:
        _safe_id(self.consumer_id, "consumerId")
        if self.requested_scope not in SCOPES or self.record_scope not in SCOPES or self.decision not in ACL_DECISIONS:
            raise MemoryHubSchemaError("ACL decision fields are invalid")
        if not isinstance(self.reason, str) or not re.fullmatch(r"[a-z_]{3,64}", self.reason):
            raise MemoryHubSchemaError("ACL reason is invalid")
        _sha(self.decision_fingerprint, "decisionFingerprint")
        if self.decision_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubSchemaError("decisionFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {"schemaVersion": MEMORY_ACL_SCHEMA, "consumerId": self.consumer_id, "requestedScope": self.requested_scope, "recordScope": self.record_scope, "decision": self.decision, "reason": self.reason}

    @classmethod
    def from_parts(cls, *, consumer_id: str, requested_scope: str, record_scope: str, decision: str, reason: str) -> "MemoryACLDecision":
        unsigned = {"schemaVersion": MEMORY_ACL_SCHEMA, "consumerId": consumer_id, "requestedScope": requested_scope, "recordScope": record_scope, "decision": decision, "reason": reason}
        return cls(consumer_id, requested_scope, record_scope, decision, reason, canonical_fingerprint(unsigned))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryACLDecision":
        _exact(payload, {"schemaVersion", "consumerId", "requestedScope", "recordScope", "decision", "reason", "decisionFingerprint"}, "ACL decision")
        if payload["schemaVersion"] != MEMORY_ACL_SCHEMA:
            raise MemoryHubSchemaError("ACL schemaVersion is invalid")
        result = cls.from_parts(consumer_id=payload["consumerId"], requested_scope=payload["requestedScope"], record_scope=payload["recordScope"], decision=payload["decision"], reason=payload["reason"])
        if payload["decisionFingerprint"] != result.decision_fingerprint:
            raise MemoryHubSchemaError("decisionFingerprint mismatch")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "decisionFingerprint": self.decision_fingerprint}


@dataclass(frozen=True)
class MemoryQueryResult:
    query_fingerprint: str
    status: str
    records: tuple[MemoryRecord, ...]
    acl_decisions: tuple[MemoryACLDecision, ...]
    result_fingerprint: str

    def __post_init__(self) -> None:
        _sha(self.query_fingerprint, "queryFingerprint")
        if self.status not in QUERY_STATUSES or not isinstance(self.records, tuple) or not isinstance(self.acl_decisions, tuple):
            raise MemoryHubSchemaError("query result fields are invalid")
        if len(self.records) > 3 or len(self.acl_decisions) > 16 or (self.status != "ready" and self.records):
            raise MemoryHubSchemaError("query result is unbounded or inconsistent")
        if not all(isinstance(item, MemoryRecord) for item in self.records) or not all(isinstance(item, MemoryACLDecision) for item in self.acl_decisions):
            raise MemoryHubSchemaError("query result items are invalid")
        _sha(self.result_fingerprint, "resultFingerprint")
        if self.result_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubSchemaError("resultFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {"schemaVersion": MEMORY_QUERY_RESULT_SCHEMA, "queryFingerprint": self.query_fingerprint, "status": self.status, "records": [item.to_dict() for item in self.records], "aclDecisions": [item.to_dict() for item in self.acl_decisions]}

    @classmethod
    def from_parts(cls, *, query_fingerprint: str, status: str, records: Sequence[MemoryRecord], acl_decisions: Sequence[MemoryACLDecision]) -> "MemoryQueryResult":
        records_tuple, decisions_tuple = tuple(records), tuple(acl_decisions)
        unsigned = {"schemaVersion": MEMORY_QUERY_RESULT_SCHEMA, "queryFingerprint": query_fingerprint, "status": status, "records": [item.to_dict() for item in records_tuple], "aclDecisions": [item.to_dict() for item in decisions_tuple]}
        return cls(query_fingerprint, status, records_tuple, decisions_tuple, canonical_fingerprint(unsigned))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], source_index: Mapping[str, MemorySource] | None = None) -> "MemoryQueryResult":
        _exact(payload, {"schemaVersion", "queryFingerprint", "status", "records", "aclDecisions", "resultFingerprint"}, "query result")
        if payload["schemaVersion"] != MEMORY_QUERY_RESULT_SCHEMA or not isinstance(payload["records"], list) or not isinstance(payload["aclDecisions"], list) or (payload["records"] and source_index is None):
            raise MemoryHubSchemaError("query result requires catalog source resolution")
        records = tuple(MemoryRecord.from_dict(item, source_index or {}) for item in payload["records"])
        decisions = tuple(MemoryACLDecision.from_dict(item) for item in payload["aclDecisions"])
        result = cls.from_parts(query_fingerprint=payload["queryFingerprint"], status=payload["status"], records=records, acl_decisions=decisions)
        if payload["resultFingerprint"] != result.result_fingerprint:
            raise MemoryHubSchemaError("resultFingerprint mismatch")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "resultFingerprint": self.result_fingerprint}
