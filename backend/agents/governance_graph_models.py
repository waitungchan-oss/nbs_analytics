from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .workflow_models import (
    _check_payload,
    _sha256,
    _sha256_value,
    _string,
    _string_value,
    _timestamp,
    _timestamp_value,
    canonical_sha256,
)


GRAPH_SCHEMA = "nbs-governance-graph-v1"
RISK_SCHEMA = "nbs-governance-risk-v1"
GATE_SCHEMA = "nbs-governance-gate-v1"
RISK_LEVELS = frozenset({"R0", "R1", "R2"})
NODE_STATUSES = frozenset({"not_started", "ready", "passed", "failed", "blocked", "skipped"})
EVIDENCE_NODE_TYPES = frozenset({"task_gate", "terra_diagnosis", "protected_incident"})
COMPACT_EVIDENCE_NODE_STATUSES = frozenset({"available", "unknown", "invalid"})
AUTHORIZATION_MODES = frozenset({"per_task", "approved_batch"})
OVERALL_STATUSES = frozenset(
    {
        "not_started",
        "awaiting_authorization",
        "blocked_user_decision",
        "diagnosis_required",
        "protected_incident",
        "blocked_missing_runner",
        "awaiting_documentation",
        "ready_for_integration",
        "completed",
        "blocked",
    }
)
MAX_GRAPH_NODES = 15
MAX_GRAPH_EVIDENCE_REFS = 12
MAX_GRAPH_METADATA_ITEMS = 12


class GovernanceGraphSchemaError(ValueError):
    """Raised when a governance graph projection violates its strict schema."""


SAFE_METADATA_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+@#%=\-]{0,199}$")
SAFE_METADATA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SECRET_LIKE_TEXT_RE = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9_]{6,})", re.IGNORECASE)
BLOCKER_ALLOWED_KEYS = frozenset({"code", "nodeId"})
DIAGNOSTIC_ALLOWED_KEYS = frozenset({"code", "nodeId", "summary"})
FRESHNESS_ALLOWED_KEYS = frozenset({"status", "workflowUpdatedAt", "graphGeneratedAt"})


def _keys(payload: Mapping[str, Any], required: set[str]) -> None:
    try:
        _check_payload(payload, required, required)
    except ValueError as exc:
        raise GovernanceGraphSchemaError(str(exc)) from exc


def _run_id(value: Any) -> str:
    value = _string_value(value, "runId")
    if value in {".", ".."} or "/" in value or "\\" in value or value.startswith("~"):
        raise GovernanceGraphSchemaError("runId must be a safe single path component")
    return value


def _string_tuple(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GovernanceGraphSchemaError(f"{key} must be a list")
    result = tuple(_string_value(item, key) for item in value)
    if len(set(result)) != len(result):
        raise GovernanceGraphSchemaError(f"{key} must not contain duplicates")
    return result


def _safe_metadata_text(value: Any, key: str) -> str:
    value = _string_value(value, key)
    if value.startswith("/") or value.startswith("~") or "\\" in value or "/" in value or "://" in value:
        raise GovernanceGraphSchemaError(f"{key} must be safe projection metadata")
    if ".." in value:
        raise GovernanceGraphSchemaError(f"{key} must be safe projection metadata")
    if any(ord(char) < 32 for char in value):
        raise GovernanceGraphSchemaError(f"{key} must be safe projection metadata")
    if SECRET_LIKE_TEXT_RE.search(value):
        raise GovernanceGraphSchemaError(f"{key} must not contain secret-like content")
    if not SAFE_METADATA_TEXT_RE.fullmatch(value):
        raise GovernanceGraphSchemaError(f"{key} must be safe projection metadata")
    return value


def _safe_identifier(value: Any, key: str) -> str:
    value = _string_value(value, key)
    if not SAFE_METADATA_KEY_RE.fullmatch(value):
        raise GovernanceGraphSchemaError(f"{key} must be a safe identifier")
    return value


def _graph_node_status(node_type: str, status: str) -> bool:
    return isinstance(node_type, str) and (status in NODE_STATUSES or (
        node_type in EVIDENCE_NODE_TYPES and status in COMPACT_EVIDENCE_NODE_STATUSES
    ))


def _safe_metadata_tree(value: Any, key: str, *, depth: int = 0) -> Any:
    if depth > 3:
        raise GovernanceGraphSchemaError(f"{key} is too deeply nested")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_metadata_text(value, key)
    if isinstance(value, dict):
        if len(value) > MAX_GRAPH_METADATA_ITEMS:
            raise GovernanceGraphSchemaError(f"{key} contains too many entries")
        result: dict[str, Any] = {}
        for name, entry in value.items():
            if not isinstance(name, str) or not SAFE_METADATA_KEY_RE.fullmatch(name):
                raise GovernanceGraphSchemaError(f"{key} keys must be safe identifiers")
            result[name] = _safe_metadata_tree(entry, f"{key}.{name}", depth=depth + 1)
        return result
    if isinstance(value, list):
        if len(value) > MAX_GRAPH_METADATA_ITEMS:
            raise GovernanceGraphSchemaError(f"{key} contains too many entries")
        return [_safe_metadata_tree(item, key, depth=depth + 1) for item in value]
    raise GovernanceGraphSchemaError(f"{key} contains unsupported metadata")


def _allowed_evidence_artifacts() -> frozenset[str]:
    from .workflow_store import ALLOWED_ARTIFACTS

    return frozenset(set(ALLOWED_ARTIFACTS) | {"manifest.json", "status.json", "approval.json", "events.jsonl"})


def _canonical_evidence_artifacts() -> dict[str, str]:
    from .canonical_evidence_registry import CanonicalEvidenceRegistry

    return {entry.artifact_kind: entry.filename for entry in CanonicalEvidenceRegistry().entries()}


def _metadata_tuple(value: Any, key: str) -> tuple[dict[str, str], ...]:
    if not isinstance(value, (list, tuple)):
        raise GovernanceGraphSchemaError(f"{key} must be a list")
    if len(value) > MAX_GRAPH_METADATA_ITEMS:
        raise GovernanceGraphSchemaError(f"{key} contains too many entries")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise GovernanceGraphSchemaError(f"{key} entries must be objects")
        normalized: dict[str, str] = {}
        for name, entry in item.items():
            if not isinstance(name, str) or not SAFE_METADATA_KEY_RE.fullmatch(name):
                raise GovernanceGraphSchemaError(f"{key} keys must be safe identifiers")
            normalized[name] = _safe_metadata_text(entry, f"{key}.{name}")
        result.append(normalized)
    return tuple(result)


def _typed_metadata_tuple(
    value: Any,
    key: str,
    *,
    allowed_keys: frozenset[str],
    required_keys: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], ...]:
    items = _metadata_tuple(value, key)
    result = []
    for item in items:
        keys = frozenset(item)
        if not keys.issubset(allowed_keys):
            raise GovernanceGraphSchemaError(f"{key} contains unknown keys")
        if not required_keys.issubset(keys):
            raise GovernanceGraphSchemaError(f"{key} is missing required keys")
        result.append(dict(item))
    return tuple(result)


def _freshness_metadata(value: Any, key: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise GovernanceGraphSchemaError(f"{key} must be an object")
    if len(value) > MAX_GRAPH_METADATA_ITEMS:
        raise GovernanceGraphSchemaError(f"{key} contains too many entries")
    normalized: dict[str, str] = {}
    for name, entry in value.items():
        if name not in FRESHNESS_ALLOWED_KEYS:
            raise GovernanceGraphSchemaError(f"{key} contains unknown keys")
        normalized[name] = _safe_metadata_text(entry, f"{key}.{name}")
    return normalized


def _ensure_instance(value: Any, expected_type: type, key: str) -> Any:
    if not isinstance(value, expected_type):
        raise GovernanceGraphSchemaError(f"{key} must be a {expected_type.__name__}")
    return value


def _validate_evidence_ref_instances(value: tuple[Any, ...], key: str) -> tuple["GovernanceEvidenceRef", ...]:
    if len(value) > MAX_GRAPH_EVIDENCE_REFS:
        raise GovernanceGraphSchemaError(f"{key} contains too many entries")
    refs = []
    for item in value:
        refs.append(_ensure_instance(item, GovernanceEvidenceRef, key))
    return tuple(refs)


def _validate_node_instances(value: tuple[Any, ...], key: str) -> tuple["GovernanceGraphNode", ...]:
    if len(value) > MAX_GRAPH_NODES:
        raise GovernanceGraphSchemaError(f"{key} contains too many entries")
    nodes = []
    for item in value:
        nodes.append(_ensure_instance(item, GovernanceGraphNode, key))
    return tuple(nodes)


def _evidence_ref_tuple(
    value: Any, key: str, *, canonical_evidence: bool = False,
) -> tuple["GovernanceEvidenceRef", ...]:
    if not isinstance(value, list):
        raise GovernanceGraphSchemaError(f"{key} must be a list")
    if len(value) > MAX_GRAPH_EVIDENCE_REFS:
        raise GovernanceGraphSchemaError(f"{key} contains too many entries")
    ref_type = GovernanceCanonicalEvidenceRef if canonical_evidence else GovernanceEvidenceRef
    return tuple(ref_type.from_dict(item) for item in value)


@dataclass(frozen=True)
class GovernanceEvidenceRef:
    schema_version: str
    path: str
    sha256: str
    status: str
    generated_at: str

    def __post_init__(self) -> None:
        if self.schema_version != "nbs-governance-evidence-ref-v1":
            raise GovernanceGraphSchemaError("schemaVersion must be nbs-governance-evidence-ref-v1")
        _safe_identifier(self.status, "status")
        path = self.path
        if path.startswith("/") or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
            raise GovernanceGraphSchemaError("evidence path must be run-relative and safe")
        allowed = _allowed_evidence_artifacts()
        if path not in allowed:
            raise GovernanceGraphSchemaError("evidence path is not an allowed canonical artifact")
        _sha256_value(self.sha256, "sha256")
        _timestamp_value(self.generated_at, "generatedAt")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GovernanceEvidenceRef":
        try:
            required = {"schemaVersion", "path", "sha256", "status", "generatedAt"}
            _keys(payload, required)
            path = _string(payload, "path")
            return cls(
                schema_version=_string(payload, "schemaVersion"),
                path=path,
                sha256=_sha256(payload, "sha256"),
                status=_safe_identifier(payload["status"], "status"),
                generated_at=_timestamp(payload, "generatedAt"),
            )
        except ValueError as exc:
            if isinstance(exc, GovernanceGraphSchemaError):
                raise
            raise GovernanceGraphSchemaError(str(exc)) from exc

    def to_dict(self) -> dict[str, str]:
        return {
            "schemaVersion": self.schema_version,
            "path": self.path,
            "sha256": self.sha256,
            "status": self.status,
            "generatedAt": self.generated_at,
        }


@dataclass(frozen=True)
class GovernanceCanonicalEvidenceRef(GovernanceEvidenceRef):
    """A canonical-evidence reference permitted only on matching evidence nodes."""

    def __post_init__(self) -> None:
        if self.schema_version != "nbs-governance-evidence-ref-v1":
            raise GovernanceGraphSchemaError("schemaVersion must be nbs-governance-evidence-ref-v1")
        _safe_identifier(self.status, "status")
        if self.path not in _canonical_evidence_artifacts().values():
            raise GovernanceGraphSchemaError("evidence path is not a canonical evidence artifact")
        _sha256_value(self.sha256, "sha256")
        _timestamp_value(self.generated_at, "generatedAt")


def _validate_standard_evidence_ref_instances(
    value: tuple[Any, ...], key: str,
) -> tuple["GovernanceEvidenceRef", ...]:
    refs = _validate_evidence_ref_instances(value, key)
    if any(isinstance(item, GovernanceCanonicalEvidenceRef) for item in refs):
        raise GovernanceGraphSchemaError(f"{key} cannot reference canonical evidence")
    return refs


def _validate_node_evidence_ref_instances(
    node_type: str, value: tuple[Any, ...], key: str,
) -> tuple["GovernanceEvidenceRef", ...]:
    refs = _validate_evidence_ref_instances(value, key)
    if node_type not in EVIDENCE_NODE_TYPES:
        return _validate_standard_evidence_ref_instances(refs, key)
    expected_path = _canonical_evidence_artifacts()[node_type]
    if any(
        not isinstance(item, GovernanceCanonicalEvidenceRef) or item.path != expected_path
        for item in refs
    ):
        raise GovernanceGraphSchemaError(f"{key} must match the evidence node canonical artifact")
    return refs


@dataclass(frozen=True)
class GovernanceRisk:
    schema_version: str
    level: str
    surfaces: tuple[str, ...]
    evidence_refs: tuple[GovernanceEvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RISK_SCHEMA:
            raise GovernanceGraphSchemaError(f"schemaVersion must be {RISK_SCHEMA}")
        if self.level not in RISK_LEVELS:
            raise GovernanceGraphSchemaError("risk level is invalid")
        _string_tuple(list(self.surfaces), "surfaces")
        _validate_standard_evidence_ref_instances(self.evidence_refs, "evidenceRefs")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GovernanceRisk":
        try:
            required = {"schemaVersion", "level", "surfaces", "evidenceRefs"}
            _keys(payload, required)
            if _string(payload, "schemaVersion") != RISK_SCHEMA:
                raise GovernanceGraphSchemaError(f"schemaVersion must be {RISK_SCHEMA}")
            level = _string(payload, "level")
            if level not in RISK_LEVELS:
                raise GovernanceGraphSchemaError("risk level is invalid")
            refs = _evidence_ref_tuple(payload["evidenceRefs"], "evidenceRefs")
            return cls(RISK_SCHEMA, level, _string_tuple(payload["surfaces"], "surfaces"), refs)
        except ValueError as exc:
            if isinstance(exc, GovernanceGraphSchemaError):
                raise
            raise GovernanceGraphSchemaError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "level": self.level,
            "surfaces": list(self.surfaces),
            "evidenceRefs": [item.to_dict() for item in self.evidence_refs],
        }


@dataclass(frozen=True)
class GovernanceGate:
    schema_version: str
    gate_id: str
    status: str
    fingerprint: str
    evidence_refs: tuple[GovernanceEvidenceRef, ...]
    reason_code: str | None

    def __post_init__(self) -> None:
        if self.schema_version != GATE_SCHEMA:
            raise GovernanceGraphSchemaError(f"schemaVersion must be {GATE_SCHEMA}")
        _safe_identifier(self.gate_id, "gateId")
        if self.status not in NODE_STATUSES:
            raise GovernanceGraphSchemaError("gate status is invalid")
        _sha256_value(self.fingerprint, "fingerprint")
        _validate_standard_evidence_ref_instances(self.evidence_refs, "evidenceRefs")
        if self.reason_code is not None:
            _safe_metadata_text(self.reason_code, "reasonCode")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GovernanceGate":
        try:
            required = {"schemaVersion", "gateId", "status", "fingerprint", "evidenceRefs", "reasonCode"}
            _keys(payload, required)
            if _string(payload, "schemaVersion") != GATE_SCHEMA:
                raise GovernanceGraphSchemaError(f"schemaVersion must be {GATE_SCHEMA}")
            status = _string(payload, "status")
            if status not in NODE_STATUSES:
                raise GovernanceGraphSchemaError("gate status is invalid")
            reason = payload["reasonCode"]
            if reason is not None:
                reason = _safe_metadata_text(reason, "reasonCode")
            return cls(
                GATE_SCHEMA,
                _safe_identifier(payload["gateId"], "gateId"),
                status,
                _sha256(payload, "fingerprint"),
                _evidence_ref_tuple(payload["evidenceRefs"], "evidenceRefs"),
                reason,
            )
        except ValueError as exc:
            if isinstance(exc, GovernanceGraphSchemaError):
                raise
            raise GovernanceGraphSchemaError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "gateId": self.gate_id,
            "status": self.status,
            "fingerprint": self.fingerprint,
            "evidenceRefs": [item.to_dict() for item in self.evidence_refs],
            "reasonCode": self.reason_code,
        }


@dataclass(frozen=True)
class GovernanceGraphNode:
    node_id: str
    node_type: str
    status: str
    attempt: int
    max_attempts: int
    evidence_refs: tuple[GovernanceEvidenceRef, ...]
    fingerprint: str
    reason_code: str | None

    def __post_init__(self) -> None:
        _safe_identifier(self.node_id, "nodeId")
        _safe_identifier(self.node_type, "nodeType")
        if not _graph_node_status(self.node_type, self.status):
            raise GovernanceGraphSchemaError("node status is invalid")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (self.attempt, self.max_attempts)):
            raise GovernanceGraphSchemaError("node attempts must be non-negative integers")
        if self.attempt > self.max_attempts:
            raise GovernanceGraphSchemaError("attempt cannot exceed maxAttempts")
        _validate_node_evidence_ref_instances(self.node_type, self.evidence_refs, "evidenceRefs")
        _sha256_value(self.fingerprint, "fingerprint")
        if self.reason_code is not None:
            _safe_metadata_text(self.reason_code, "reasonCode")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GovernanceGraphNode":
        try:
            required = {"nodeId", "nodeType", "status", "attempt", "maxAttempts", "evidenceRefs", "fingerprint", "reasonCode"}
            _keys(payload, required)
            node_type = _safe_identifier(payload["nodeType"], "nodeType")
            status = _string(payload, "status")
            if not _graph_node_status(node_type, status):
                raise GovernanceGraphSchemaError("node status is invalid")
            attempt = payload["attempt"]
            max_attempts = payload["maxAttempts"]
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (attempt, max_attempts)):
                raise GovernanceGraphSchemaError("node attempts must be non-negative integers")
            if attempt > max_attempts:
                raise GovernanceGraphSchemaError("attempt cannot exceed maxAttempts")
            reason = payload["reasonCode"]
            if reason is not None:
                reason = _safe_metadata_text(reason, "reasonCode")
            return cls(
                _safe_identifier(payload["nodeId"], "nodeId"),
                node_type,
                status,
                attempt,
                max_attempts,
                _evidence_ref_tuple(
                    payload["evidenceRefs"], "evidenceRefs",
                    canonical_evidence=node_type in EVIDENCE_NODE_TYPES,
                ),
                _sha256(payload, "fingerprint"),
                reason,
            )
        except ValueError as exc:
            if isinstance(exc, GovernanceGraphSchemaError):
                raise
            raise GovernanceGraphSchemaError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeId": self.node_id,
            "nodeType": self.node_type,
            "status": self.status,
            "attempt": self.attempt,
            "maxAttempts": self.max_attempts,
            "evidenceRefs": [item.to_dict() for item in self.evidence_refs],
            "fingerprint": self.fingerprint,
            "reasonCode": self.reason_code,
        }


@dataclass(frozen=True)
class GovernanceGraphSnapshot:
    schema_version: str
    run_id: str
    generated_at: str
    graph_fingerprint: str
    risk: GovernanceRisk | None
    authorization_mode: str
    overall_status: str
    nodes: tuple[GovernanceGraphNode, ...]
    allowed_next_nodes: tuple[str, ...]
    blockers: tuple[dict[str, str], ...]
    freshness: dict[str, Any]
    diagnostics: tuple[dict[str, str], ...]

    def __post_init__(self) -> None:
        if self.schema_version != GRAPH_SCHEMA:
            raise GovernanceGraphSchemaError(f"schemaVersion must be {GRAPH_SCHEMA}")
        _run_id(self.run_id)
        _timestamp_value(self.generated_at, "generatedAt")
        _sha256_value(self.graph_fingerprint, "graphFingerprint")
        if self.risk is not None:
            _ensure_instance(self.risk, GovernanceRisk, "risk")
        if self.authorization_mode not in AUTHORIZATION_MODES:
            raise GovernanceGraphSchemaError("authorizationMode is invalid")
        if self.overall_status not in OVERALL_STATUSES:
            raise GovernanceGraphSchemaError("overallStatus is invalid")
        nodes = _validate_node_instances(self.nodes, "nodes")
        node_ids = tuple(node.node_id for node in nodes)
        if len(set(node_ids)) != len(node_ids):
            raise GovernanceGraphSchemaError("node IDs must be unique")
        allowed_next_nodes = _string_tuple(list(self.allowed_next_nodes), "allowedNextNodes")
        if any(node_id not in node_ids for node_id in allowed_next_nodes):
            raise GovernanceGraphSchemaError("allowedNextNodes must reference existing node IDs")
        _typed_metadata_tuple(self.blockers, "blockers", allowed_keys=BLOCKER_ALLOWED_KEYS, required_keys=frozenset({"code"}))
        _typed_metadata_tuple(
            self.diagnostics,
            "diagnostics",
            allowed_keys=DIAGNOSTIC_ALLOWED_KEYS,
            required_keys=frozenset({"code"}),
        )
        _freshness_metadata(self.freshness, "freshness")
        if self.graph_fingerprint != self.canonical_fingerprint:
            raise GovernanceGraphSchemaError("graphFingerprint does not match canonical graph content")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GovernanceGraphSnapshot":
        try:
            required = {"schemaVersion", "runId", "generatedAt", "graphFingerprint", "risk", "authorizationMode", "overallStatus", "nodes", "allowedNextNodes", "blockers", "freshness", "diagnostics"}
            _keys(payload, required)
            if _string(payload, "schemaVersion") != GRAPH_SCHEMA:
                raise GovernanceGraphSchemaError(f"schemaVersion must be {GRAPH_SCHEMA}")
            authorization = _string(payload, "authorizationMode")
            if authorization not in AUTHORIZATION_MODES:
                raise GovernanceGraphSchemaError("authorizationMode is invalid")
            overall = _string(payload, "overallStatus")
            if overall not in OVERALL_STATUSES:
                raise GovernanceGraphSchemaError("overallStatus is invalid")
            raw_nodes = payload["nodes"]
            if not isinstance(raw_nodes, list):
                raise GovernanceGraphSchemaError("nodes must be a list")
            nodes = tuple(GovernanceGraphNode.from_dict(item) for item in raw_nodes)
            node_ids = tuple(node.node_id for node in nodes)
            if len(set(node_ids)) != len(node_ids):
                raise GovernanceGraphSchemaError("node IDs must be unique")
            allowed = _string_tuple(payload["allowedNextNodes"], "allowedNextNodes")
            if any(node_id not in node_ids for node_id in allowed):
                raise GovernanceGraphSchemaError("allowedNextNodes must reference existing node IDs")
            if payload["risk"] is not None and not isinstance(payload["risk"], dict):
                raise GovernanceGraphSchemaError("risk must be an object or null")
            freshness = payload["freshness"]
            if not isinstance(freshness, dict):
                raise GovernanceGraphSchemaError("freshness must be an object")
            snapshot = cls(
                GRAPH_SCHEMA,
                _run_id(payload["runId"]),
                _timestamp(payload, "generatedAt"),
                _sha256(payload, "graphFingerprint"),
                GovernanceRisk.from_dict(payload["risk"]) if payload["risk"] is not None else None,
                authorization,
                overall,
                nodes,
                allowed,
                _typed_metadata_tuple(
                    payload["blockers"],
                    "blockers",
                    allowed_keys=BLOCKER_ALLOWED_KEYS,
                    required_keys=frozenset({"code"}),
                ),
                _freshness_metadata(dict(freshness), "freshness"),
                _typed_metadata_tuple(
                    payload["diagnostics"],
                    "diagnostics",
                    allowed_keys=DIAGNOSTIC_ALLOWED_KEYS,
                    required_keys=frozenset({"code"}),
                ),
            )
            return snapshot
        except ValueError as exc:
            if isinstance(exc, GovernanceGraphSchemaError):
                raise
            raise GovernanceGraphSchemaError(str(exc)) from exc

    @property
    def canonical_fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("graphFingerprint")
        return canonical_sha256(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "generatedAt": self.generated_at,
            "graphFingerprint": self.graph_fingerprint,
            "risk": self.risk.to_dict() if self.risk is not None else None,
            "authorizationMode": self.authorization_mode,
            "overallStatus": self.overall_status,
            "nodes": [node.to_dict() for node in self.nodes],
            "allowedNextNodes": list(self.allowed_next_nodes),
            "blockers": [dict(item) for item in self.blockers],
            "freshness": dict(self.freshness),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }
