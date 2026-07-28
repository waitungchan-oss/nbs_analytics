from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .governance_graph_models import (
    COMPACT_EVIDENCE_NODE_STATUSES,
    NODE_STATUSES,
    GovernanceGraphSchemaError,
)
from .workflow_models import canonical_sha256


QUERY_SCHEMA = "governance-graph-query-v1"
QUERY_STATUSES = frozenset({"available", "unavailable", "unknown", "invalid", "blocked"})
QUERY_FILTER_KEYS = (
    "runId", "nodeType", "nodeStatus", "nodeId", "edgeType",
    "artifactKind", "evidenceStatus", "snapshotFingerprint",
)
QUERY_NODE_TYPES = frozenset({
    "risk", "spec_gate", "plan_gate", "implementation", "targeted_verification",
    "review", "full_verification", "hermes", "documentation", "git_integration",
    "task_gate", "terra_diagnosis", "protected_incident",
})
QUERY_EDGE_TYPES = frozenset({
    "requires", "produces", "implements", "reviews", "verifies", "blocks",
    "derived_from", "committed_as", "documented_by",
})
QUERY_ARTIFACT_KINDS = frozenset({
    "risk", "spec_gate", "plan_gate", "implementation", "targeted_verification",
    "review", "full_verification", "hermes", "documentation", "git_integration",
    "task_gate", "terra_diagnosis", "protected_incident",
})
_FILTER_KEY_SET = frozenset(QUERY_FILTER_KEYS)
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+@#%=-]{0,127}$")
_SNAPSHOT_KEYS = frozenset({"runId", "graphFingerprint", "generatedAt", "freshness"})
_NODE_KEYS = frozenset({"nodeId", "nodeType", "status", "reasonCode", "attempt", "maxAttempts", "fingerprint", "evidenceRefs"})
_EDGE_KEYS = frozenset({"source", "target", "type", "status", "reasonCode"})
_REF_KEYS = frozenset({"schemaVersion", "path", "sha256", "status", "generatedAt", "finalizedAt"})
_DIAGNOSTIC_KEYS = frozenset({"code", "summary"})


class GovernanceGraphQuerySchemaError(GovernanceGraphSchemaError):
    """Raised when a query or bounded query result violates its contract."""


def _safe_value(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise GovernanceGraphQuerySchemaError(f"{key} must be a bounded string")
    if value in {".", ".."} or "/" in value or "\\" in value or ".." in value:
        raise GovernanceGraphQuerySchemaError(f"{key} must be a safe exact-match value")
    if not _SAFE_VALUE_RE.fullmatch(value):
        raise GovernanceGraphQuerySchemaError(f"{key} must be a safe exact-match value")
    return value


def _sha256(value: Any, key: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise GovernanceGraphQuerySchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _record(value: Any, key: str, allowed: frozenset[str]) -> MappingProxyType:
    if not isinstance(value, dict) or not set(value) <= allowed:
        raise GovernanceGraphQuerySchemaError(f"{key} contains unsupported metadata")
    result: dict[str, Any] = {}
    for name, item in value.items():
        if isinstance(item, str):
            if name in {"absolutePath", "prompt", "command", "stdout", "stderr", "secret"}:
                raise GovernanceGraphQuerySchemaError(f"{key}.{name} is not public query metadata")
            result[name] = _safe_value(item, f"{key}.{name}")
        elif isinstance(item, (bool, int)) or item is None:
            result[name] = item
        elif isinstance(item, list) and len(item) <= 12:
            result[name] = tuple(_freeze(item))
        else:
            raise GovernanceGraphQuerySchemaError(f"{key}.{name} is not bounded query metadata")
    return MappingProxyType(result)


@dataclass(frozen=True)
class GovernanceGraphQuery:
    filters: Mapping[str, str]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphQuery":
        if not isinstance(payload, Mapping) or not set(payload) <= _FILTER_KEY_SET:
            raise GovernanceGraphQuerySchemaError("query contains an unknown filter")
        normalized: dict[str, str] = {}
        for key, value in payload.items():
            if value is None:
                continue
            normalized[key] = _safe_value(value, key)
        if "snapshotFingerprint" in normalized:
            _sha256(normalized["snapshotFingerprint"], "snapshotFingerprint")
        if "nodeStatus" in normalized and normalized["nodeStatus"] not in NODE_STATUSES | COMPACT_EVIDENCE_NODE_STATUSES:
            raise GovernanceGraphQuerySchemaError("nodeStatus is invalid")
        if "nodeType" in normalized and normalized["nodeType"] not in QUERY_NODE_TYPES:
            raise GovernanceGraphQuerySchemaError("nodeType is invalid")
        if "edgeType" in normalized and normalized["edgeType"] not in QUERY_EDGE_TYPES:
            raise GovernanceGraphQuerySchemaError("edgeType is invalid")
        if "artifactKind" in normalized and normalized["artifactKind"] not in QUERY_ARTIFACT_KINDS:
            raise GovernanceGraphQuerySchemaError("artifactKind is invalid")
        if "evidenceStatus" in normalized and normalized["evidenceStatus"] not in QUERY_STATUSES:
            raise GovernanceGraphQuerySchemaError("evidenceStatus is invalid")
        return cls(MappingProxyType(dict(sorted(normalized.items()))))

    def normalized(self) -> dict[str, str]:
        return dict(self.filters)


@dataclass(frozen=True)
class GovernanceGraphQueryResult:
    status: str
    snapshot_identity: Mapping[str, Any] | None
    filters: Mapping[str, str]
    matched_nodes: tuple[Mapping[str, Any], ...]
    matched_edges: tuple[Mapping[str, Any], ...]
    evidence_refs: tuple[Mapping[str, Any], ...]
    unknown_count: int
    invalid_count: int
    blocked_count: int
    diagnostics: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_parts(cls, *, status: str, snapshot_identity: Mapping[str, Any] | None,
                   filters: Mapping[str, Any] | GovernanceGraphQuery,
                   matched_nodes: Any, matched_edges: Any, evidence_refs: Any,
                   unknown_count: int, invalid_count: int, blocked_count: int,
                   diagnostics: Any) -> "GovernanceGraphQueryResult":
        if status not in QUERY_STATUSES:
            raise GovernanceGraphQuerySchemaError("status is invalid")
        query = filters if isinstance(filters, GovernanceGraphQuery) else GovernanceGraphQuery.from_dict(filters)
        identity = None
        if snapshot_identity is not None:
            if set(snapshot_identity) != _SNAPSHOT_KEYS:
                raise GovernanceGraphQuerySchemaError("snapshotIdentity keys are invalid")
            identity = MappingProxyType({
                "runId": _safe_value(snapshot_identity["runId"], "snapshotIdentity.runId"),
                "graphFingerprint": _sha256(snapshot_identity["graphFingerprint"], "graphFingerprint"),
                "generatedAt": _safe_value(snapshot_identity["generatedAt"], "snapshotIdentity.generatedAt"),
                "freshness": _safe_value(snapshot_identity["freshness"], "snapshotIdentity.freshness"),
            })
        if not all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100000 for value in (unknown_count, invalid_count, blocked_count)):
            raise GovernanceGraphQuerySchemaError("status counts are invalid")
        nodes = tuple(_record(item, "matchedNodes", _NODE_KEYS) for item in matched_nodes)
        edges = tuple(_record(item, "matchedEdges", _EDGE_KEYS) for item in matched_edges)
        refs = tuple(_record(item, "evidenceRefs", _REF_KEYS) for item in evidence_refs)
        diags = tuple(_record(item, "diagnostics", _DIAGNOSTIC_KEYS) for item in diagnostics)
        return cls(status, identity, MappingProxyType(query.normalized()), nodes, edges, refs,
                   unknown_count, invalid_count, blocked_count, diags)

    @property
    def query_fingerprint(self) -> str:
        return canonical_sha256({
            "filters": dict(self.filters),
            "snapshotIdentity": _thaw(self.snapshot_identity) if self.snapshot_identity is not None else None,
            "status": self.status,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": QUERY_SCHEMA,
            "status": self.status,
            "snapshotIdentity": _thaw(self.snapshot_identity) if self.snapshot_identity is not None else None,
            "queryFingerprint": self.query_fingerprint,
            "matchedNodes": [_thaw(item) for item in self.matched_nodes],
            "matchedEdges": [_thaw(item) for item in self.matched_edges],
            "evidenceRefs": [_thaw(item) for item in self.evidence_refs],
            "unknownCount": self.unknown_count,
            "invalidCount": self.invalid_count,
            "blockedCount": self.blocked_count,
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }
