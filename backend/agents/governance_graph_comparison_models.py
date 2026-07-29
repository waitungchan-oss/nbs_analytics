from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .governance_graph_models import GovernanceGraphSchemaError
from .workflow_models import canonical_sha256


COMPARISON_SCHEMA = "governance-graph-comparison-v1"
COMPARISON_STATUSES = frozenset({"available", "unavailable", "unknown", "invalid", "blocked"})
CHANGE_TYPES = frozenset({"added", "removed", "changed"})
_SAFE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SAFE_VALUE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_KEYS = frozenset({"runId", "snapshotFingerprint"})
_IDENTITY_KEYS = frozenset({"runId", "graphFingerprint", "generatedAt", "freshness"})
_SUMMARY_KEYS = frozenset({
    "addedNodes", "removedNodes", "changedNodes", "unchangedNodes",
    "addedEdges", "removedEdges", "changedEdges",
    "addedEvidenceRefs", "removedEvidenceRefs", "changedEvidenceRefs",
})
_CHANGE_IDENTITY_KEYS = frozenset({"nodeId", "source", "target", "type", "path", "sha256"})
_FORBIDDEN_KEYS = frozenset({"absolutePath", "prompt", "command", "stdout", "stderr", "secret"})


class GovernanceGraphComparisonSchemaError(GovernanceGraphSchemaError):
    """Raised when a comparison contract or bounded result is invalid."""


def _safe_value(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SAFE_VALUE_RE.fullmatch(value):
        raise GovernanceGraphComparisonSchemaError(f"{key} must be bounded public text")
    if value.startswith("/") or "\\" in value or "\n" in value or "\r" in value:
        raise GovernanceGraphComparisonSchemaError(f"{key} must not expose an unsafe path or control text")
    return value


def _safe_run_id(value: Any) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise GovernanceGraphComparisonSchemaError("runId must be a safe single path component")
    if "/" in value or "\\" in value or "." in value or len(value) > 128:
        raise GovernanceGraphComparisonSchemaError("runId must be a safe single path component")
    return _safe_value(value, "runId")


def _sha256(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise GovernanceGraphComparisonSchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _freeze(value: Any, key: str, depth: int = 0) -> Any:
    if depth > 3:
        raise GovernanceGraphComparisonSchemaError(f"{key} is too deeply nested")
    if isinstance(value, Mapping):
        if len(value) > 32:
            raise GovernanceGraphComparisonSchemaError(f"{key} contains too many fields")
        result = {}
        for name, item in value.items():
            if not isinstance(name, str) or not _SAFE_KEY_RE.fullmatch(name) or name in _FORBIDDEN_KEYS:
                raise GovernanceGraphComparisonSchemaError(f"{key} contains an unsafe field")
            result[name] = _freeze(item, f"{key}.{name}", depth + 1)
        return MappingProxyType(dict(sorted(result.items())))
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            raise GovernanceGraphComparisonSchemaError(f"{key} contains too many items")
        return tuple(_freeze(item, key, depth + 1) for item in value)
    if isinstance(value, str):
        return _safe_value(value, key)
    if value is None or isinstance(value, (bool, int)):
        return value
    raise GovernanceGraphComparisonSchemaError(f"{key} contains unsupported metadata")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class GovernanceGraphSnapshotReference:
    run_id: str
    snapshot_fingerprint: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphSnapshotReference":
        if not isinstance(payload, Mapping) or not set(payload) <= _REFERENCE_KEYS or "runId" not in payload:
            raise GovernanceGraphComparisonSchemaError("snapshot reference keys are invalid")
        fingerprint = payload.get("snapshotFingerprint")
        return cls(_safe_run_id(payload["runId"]), None if fingerprint is None else _sha256(fingerprint, "snapshotFingerprint"))

    def to_dict(self) -> dict[str, str | None]:
        return {"runId": self.run_id, "snapshotFingerprint": self.snapshot_fingerprint}


@dataclass(frozen=True)
class GovernanceGraphComparisonResult:
    status: str
    left_reference: GovernanceGraphSnapshotReference
    right_reference: GovernanceGraphSnapshotReference
    left_snapshot: Mapping[str, Any] | None
    right_snapshot: Mapping[str, Any] | None
    summary: Mapping[str, int]
    node_changes: tuple[Mapping[str, Any], ...]
    edge_changes: tuple[Mapping[str, Any], ...]
    evidence_changes: tuple[Mapping[str, Any], ...]
    diagnostics: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_parts(
        cls,
        *,
        status: str,
        left_reference: GovernanceGraphSnapshotReference | Mapping[str, Any],
        right_reference: GovernanceGraphSnapshotReference | Mapping[str, Any],
        left_snapshot: Mapping[str, Any] | None,
        right_snapshot: Mapping[str, Any] | None,
        summary: Mapping[str, Any],
        node_changes: Any,
        edge_changes: Any,
        evidence_changes: Any,
        diagnostics: Any,
    ) -> "GovernanceGraphComparisonResult":
        if status not in COMPARISON_STATUSES:
            raise GovernanceGraphComparisonSchemaError("status is invalid")
        left_ref = left_reference if isinstance(left_reference, GovernanceGraphSnapshotReference) else GovernanceGraphSnapshotReference.from_dict(left_reference)
        right_ref = right_reference if isinstance(right_reference, GovernanceGraphSnapshotReference) else GovernanceGraphSnapshotReference.from_dict(right_reference)
        left_identity = _identity(left_snapshot, "leftSnapshot")
        right_identity = _identity(right_snapshot, "rightSnapshot")
        if not isinstance(summary, Mapping) or set(summary) != _SUMMARY_KEYS:
            raise GovernanceGraphComparisonSchemaError("summary keys are invalid")
        normalized_summary = MappingProxyType({
            key: _count(summary[key], key) for key in sorted(_SUMMARY_KEYS)
        })
        normalized_nodes = tuple(_change(item, "nodeChanges") for item in node_changes)
        normalized_edges = tuple(_change(item, "edgeChanges") for item in edge_changes)
        normalized_evidence = tuple(_change(item, "evidenceChanges") for item in evidence_changes)
        _reject_duplicate_keys(normalized_nodes, _node_identity_key, "nodeChanges")
        _reject_duplicate_keys(normalized_edges, _edge_identity_key, "edgeChanges")
        _reject_duplicate_keys(normalized_evidence, _evidence_identity_key, "evidenceChanges")
        if status == "available" and (left_identity is None or right_identity is None):
            raise GovernanceGraphComparisonSchemaError("available results require both snapshot identities")
        _validate_summary_counts(normalized_summary, normalized_nodes, "Nodes")
        _validate_summary_counts(normalized_summary, normalized_edges, "Edges")
        _validate_summary_counts(normalized_summary, normalized_evidence, "EvidenceRefs")
        return cls(
            status,
            left_ref,
            right_ref,
            left_identity,
            right_identity,
            normalized_summary,
            tuple(sorted(normalized_nodes, key=_node_change_key)),
            tuple(sorted(normalized_edges, key=_edge_change_key)),
            tuple(sorted(normalized_evidence, key=_evidence_change_key)),
            tuple(sorted((_diagnostic(item) for item in diagnostics), key=_diagnostic_key)),
        )

    @property
    def comparison_fingerprint(self) -> str:
        return canonical_sha256({
            "leftReference": self.left_reference.to_dict(),
            "rightReference": self.right_reference.to_dict(),
            "leftSnapshot": _thaw(self.left_snapshot),
            "rightSnapshot": _thaw(self.right_snapshot),
            "status": self.status,
            "summary": _thaw(self.summary),
            "nodeChanges": [_thaw(item) for item in self.node_changes],
            "edgeChanges": [_thaw(item) for item in self.edge_changes],
            "evidenceChanges": [_thaw(item) for item in self.evidence_changes],
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": COMPARISON_SCHEMA,
            "status": self.status,
            "leftSnapshot": _thaw(self.left_snapshot),
            "rightSnapshot": _thaw(self.right_snapshot),
            "comparisonFingerprint": self.comparison_fingerprint,
            "summary": _thaw(self.summary),
            "nodeChanges": [_thaw(item) for item in self.node_changes],
            "edgeChanges": [_thaw(item) for item in self.edge_changes],
            "evidenceChanges": [_thaw(item) for item in self.evidence_changes],
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }


def _identity(value: Mapping[str, Any] | None, key: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_KEYS:
        raise GovernanceGraphComparisonSchemaError(f"{key} keys are invalid")
    return MappingProxyType({
        "runId": _safe_run_id(value["runId"]),
        "graphFingerprint": _sha256(value["graphFingerprint"], f"{key}.graphFingerprint"),
        "generatedAt": _safe_value(value["generatedAt"], f"{key}.generatedAt"),
        "freshness": _safe_value(value["freshness"], f"{key}.freshness"),
    })


def _count(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100000:
        raise GovernanceGraphComparisonSchemaError(f"{key} must be a bounded non-negative integer")
    return value


def _change(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) - (_CHANGE_IDENTITY_KEYS | {"changeType", "before", "after"}):
        raise GovernanceGraphComparisonSchemaError(f"{key} contains unsupported fields")
    if value.get("changeType") not in CHANGE_TYPES or "before" not in value or "after" not in value:
        raise GovernanceGraphComparisonSchemaError(f"{key} change record is incomplete")
    identity = set(value) & _CHANGE_IDENTITY_KEYS
    if not identity:
        raise GovernanceGraphComparisonSchemaError(f"{key} change record has no identity")
    result = {name: _freeze(value[name], f"{key}.{name}") for name in sorted(identity)}
    result["changeType"] = value["changeType"]
    result["before"] = None if value["before"] is None else _freeze(value["before"], f"{key}.before")
    result["after"] = None if value["after"] is None else _freeze(value["after"], f"{key}.after")
    return MappingProxyType(result)


def _diagnostic(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"code", "summary"}:
        raise GovernanceGraphComparisonSchemaError("diagnostic keys are invalid")
    return MappingProxyType({
        "code": _safe_value(value["code"], "diagnostics.code"),
        "summary": _safe_value(value["summary"], "diagnostics.summary"),
    })


def _change_type_counts(changes: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    return {change_type: sum(item["changeType"] == change_type for item in changes) for change_type in CHANGE_TYPES}


def _validate_summary_counts(summary: Mapping[str, int], changes: tuple[Mapping[str, Any], ...], label: str) -> None:
    counts = _change_type_counts(changes)
    for change_type, field in (("added", "added"), ("removed", "removed"), ("changed", "changed")):
        key = f"{field}{label}"
        if summary[key] != counts[change_type]:
            raise GovernanceGraphComparisonSchemaError(f"{key} does not match change records")


def _node_change_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return (str(value.get("nodeId", "")), str(value["changeType"]))


def _node_identity_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return (str(value.get("nodeId", "")), str(value["changeType"]))


def _edge_change_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("source", "")), str(value.get("target", "")),
        str(value.get("type", "")), str(value["changeType"]),
    )


def _edge_identity_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return _edge_change_key(value)


def _evidence_change_key(value: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(value.get("path", "")), str(value.get("sha256", "")), str(value["changeType"]))


def _evidence_identity_key(value: Mapping[str, Any]) -> tuple[str, str, str]:
    return _evidence_change_key(value)


def _diagnostic_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return (str(value["code"]), str(value["summary"]))


def _reject_duplicate_keys(
    changes: tuple[Mapping[str, Any], ...], key_fn, label: str,
) -> None:
    keys = [key_fn(item) for item in changes]
    if len(keys) != len(set(keys)):
        raise GovernanceGraphComparisonSchemaError(f"{label} contains duplicate identities")
