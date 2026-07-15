from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


MANIFEST_SCHEMA = "agent-workflow-manifest-v1"
APPROVAL_SCHEMA = "agent-workflow-approval-v1"
STATUS_SCHEMA = "agent-workflow-status-v1"
EVENT_SCHEMA = "agent-workflow-event-v1"

WORKFLOW_STATUSES = frozenset({
    "created", "context_running", "awaiting_authorization",
    "implementation_running", "targeted_verification_running",
    "review_running", "changes_required", "full_verification_running",
    "hermes_running", "completed", "blocked", "failed",
})
TERMINAL_STATUSES = frozenset({"completed", "changes_required", "blocked", "failed"})
TRANSITIONS = {
    "created": frozenset({"context_running", "blocked", "failed"}),
    "context_running": frozenset({"awaiting_authorization", "blocked", "failed"}),
    "awaiting_authorization": frozenset({"implementation_running", "blocked", "failed"}),
    "implementation_running": frozenset({"targeted_verification_running", "blocked", "failed"}),
    "targeted_verification_running": frozenset({"review_running", "blocked", "failed"}),
    "review_running": frozenset({"changes_required", "full_verification_running", "blocked", "failed"}),
    "full_verification_running": frozenset({"hermes_running", "blocked", "failed"}),
    "hermes_running": frozenset({"completed", "blocked", "failed"}),
}


class WorkflowSchemaError(ValueError):
    """Raised when a workflow artifact is not valid for its schema."""


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def legal_transition(current: str, next_status: str) -> bool:
    return next_status in TRANSITIONS.get(current, frozenset())


def _check_payload(payload: Mapping[str, Any], required: set[str], allowed: set[str]) -> None:
    if not isinstance(payload, dict):
        raise WorkflowSchemaError("workflow payload must be an object")
    keys = set(payload)
    if keys != required or not keys <= allowed:
        missing = sorted(required - keys)
        unknown = sorted(keys - allowed)
        detail = []
        if missing:
            detail.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            detail.append(f"unknown fields: {', '.join(unknown)}")
        raise WorkflowSchemaError("workflow payload keys are invalid (" + "; ".join(detail) + ")")


def _string_value(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowSchemaError(f"{key} must be a non-empty string")
    return value


def _string(payload: Mapping[str, Any], key: str) -> str:
    return _string_value(payload[key], key)


def _timestamp_value(value: Any, key: str) -> str:
    value = _string_value(value, key)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise WorkflowSchemaError(f"{key} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WorkflowSchemaError(f"{key} must include a timezone")
    return parsed.isoformat()


def _timestamp(payload: Mapping[str, Any], key: str) -> str:
    return _timestamp_value(payload[key], key)


def _sha256(payload: Mapping[str, Any], key: str) -> str:
    value = _string(payload, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise WorkflowSchemaError(f"{key} must be a lowercase SHA-256 hex digest")
    return value


def _sha256_value(value: Any, key: str) -> str:
    value = _string_value(value, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise WorkflowSchemaError(f"{key} must be a lowercase SHA-256 hex digest")
    return value


def _git_sha(payload: Mapping[str, Any], key: str) -> str:
    value = _string(payload, key)
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise WorkflowSchemaError(f"{key} must be a Git SHA-1 hex digest")
    return value


def _git_sha_value(value: Any, key: str) -> str:
    value = _string_value(value, key)
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise WorkflowSchemaError(f"{key} must be a Git SHA-1 hex digest")
    return value


def _normalize_dirty_files(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise WorkflowSchemaError("dirtyFiles must be a list")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise WorkflowSchemaError("dirtyFiles entries must be objects")
        keys = set(item)
        if keys == {"path", "sha256"}:
            normalized.append({"path": _string(item, "path"), "sha256": _sha256(item, "sha256")})
            continue
        if keys not in (
            {"status", "path", "sha256"},
            {"status", "path", "originalPath", "sha256"},
        ):
            raise WorkflowSchemaError("dirtyFiles entry schema is invalid")
        status = _string(item, "status")
        if len(status) != 2:
            raise WorkflowSchemaError("dirtyFiles status must contain two porcelain columns")
        normalized_item = {
            "status": status,
            "path": _string(item, "path"),
            "sha256": _sha256(item, "sha256"),
        }
        if "originalPath" in item:
            normalized_item["originalPath"] = _string(item, "originalPath")
        normalized.append(normalized_item)
    return tuple(normalized)


def _normalize_constructor_dirty_files(value: Any) -> tuple[dict[str, str], ...]:
    if isinstance(value, tuple):
        value = list(value)
    return _normalize_dirty_files(value)


@dataclass(frozen=True)
class WorkflowManifest:
    schema_version: str
    run_id: str
    brief_path: str
    brief_sha256: str
    git_branch: str
    git_head: str
    dirty_files: tuple[dict[str, str], ...]
    created_at: str
    context_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {MANIFEST_SCHEMA}")
        _string_value(self.run_id, "runId")
        _string_value(self.brief_path, "briefPath")
        _sha256_value(self.brief_sha256, "briefSha256")
        _string_value(self.git_branch, "gitBranch")
        _git_sha_value(self.git_head, "gitHead")
        object.__setattr__(self, "dirty_files", _normalize_constructor_dirty_files(self.dirty_files))
        object.__setattr__(self, "created_at", _timestamp_value(self.created_at, "createdAt"))
        _sha256_value(self.context_fingerprint, "contextFingerprint")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowManifest":
        keys = {"schemaVersion", "runId", "briefPath", "briefSha256", "gitBranch", "gitHead", "dirtyFiles", "createdAt", "contextFingerprint"}
        _check_payload(payload, keys, keys)
        if _string(payload, "schemaVersion") != MANIFEST_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {MANIFEST_SCHEMA}")
        return cls(
            schema_version=MANIFEST_SCHEMA,
            run_id=_string(payload, "runId"),
            brief_path=_string(payload, "briefPath"),
            brief_sha256=_sha256(payload, "briefSha256"),
            git_branch=_string(payload, "gitBranch"),
            git_head=_git_sha(payload, "gitHead"),
            dirty_files=_normalize_dirty_files(payload["dirtyFiles"]),
            created_at=_timestamp(payload, "createdAt"),
            context_fingerprint=_sha256(payload, "contextFingerprint"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "briefPath": self.brief_path,
            "briefSha256": self.brief_sha256,
            "gitBranch": self.git_branch,
            "gitHead": self.git_head,
            "dirtyFiles": [dict(item) for item in self.dirty_files],
            "createdAt": self.created_at,
            "contextFingerprint": self.context_fingerprint,
        }


@dataclass(frozen=True)
class WorkflowApproval:
    schema_version: str
    run_id: str
    contract_path: str
    contract_fingerprint: str
    approved_base_sha: str
    approved_at: str
    authorization_status: str

    def __post_init__(self) -> None:
        if self.schema_version != APPROVAL_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {APPROVAL_SCHEMA}")
        _string_value(self.run_id, "runId")
        _string_value(self.contract_path, "contractPath")
        _sha256_value(self.contract_fingerprint, "contractFingerprint")
        _git_sha_value(self.approved_base_sha, "approvedBaseSha")
        object.__setattr__(self, "approved_at", _timestamp_value(self.approved_at, "approvedAt"))
        if self.authorization_status != "approved":
            raise WorkflowSchemaError("authorizationStatus must be approved")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowApproval":
        keys = {"schemaVersion", "runId", "contractPath", "contractFingerprint", "approvedBaseSha", "approvedAt", "authorizationStatus"}
        _check_payload(payload, keys, keys)
        if _string(payload, "schemaVersion") != APPROVAL_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {APPROVAL_SCHEMA}")
        if _string(payload, "authorizationStatus") != "approved":
            raise WorkflowSchemaError("authorizationStatus must be approved")
        return cls(
            schema_version=APPROVAL_SCHEMA,
            run_id=_string(payload, "runId"),
            contract_path=_string(payload, "contractPath"),
            contract_fingerprint=_sha256(payload, "contractFingerprint"),
            approved_base_sha=_git_sha(payload, "approvedBaseSha"),
            approved_at=_timestamp(payload, "approvedAt"),
            authorization_status="approved",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "contractPath": self.contract_path,
            "contractFingerprint": self.contract_fingerprint,
            "approvedBaseSha": self.approved_base_sha,
            "approvedAt": self.approved_at,
            "authorizationStatus": self.authorization_status,
        }


@dataclass(frozen=True)
class WorkflowStatus:
    schema_version: str
    run_id: str
    stage: str
    status: str
    started_at: str
    updated_at: str
    completed_at: str | None
    message: str
    error_code: str | None
    artifact_bytes: int

    def __post_init__(self) -> None:
        if self.schema_version != STATUS_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {STATUS_SCHEMA}")
        _string_value(self.run_id, "runId")
        _string_value(self.stage, "stage")
        _string_value(self.status, "status")
        if self.status not in WORKFLOW_STATUSES:
            raise WorkflowSchemaError("status is not a known workflow status")
        object.__setattr__(self, "started_at", _timestamp_value(self.started_at, "startedAt"))
        object.__setattr__(self, "updated_at", _timestamp_value(self.updated_at, "updatedAt"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _timestamp_value(self.completed_at, "completedAt"))
        if self.error_code is not None:
            _string_value(self.error_code, "errorCode")
        _string_value(self.message, "message")
        if isinstance(self.artifact_bytes, bool) or not isinstance(self.artifact_bytes, int) or self.artifact_bytes < 0:
            raise WorkflowSchemaError("artifactBytes must be a non-negative integer")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowStatus":
        keys = {"schemaVersion", "runId", "stage", "status", "startedAt", "updatedAt", "completedAt", "message", "errorCode", "artifactBytes"}
        _check_payload(payload, keys, keys)
        if _string(payload, "schemaVersion") != STATUS_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {STATUS_SCHEMA}")
        status = _string(payload, "status")
        if status not in WORKFLOW_STATUSES:
            raise WorkflowSchemaError("status is not a known workflow status")
        completed_at = payload["completedAt"]
        if completed_at is not None:
            completed_at = _timestamp(payload, "completedAt")
        error_code = payload["errorCode"]
        if error_code is not None and (not isinstance(error_code, str) or not error_code.strip()):
            raise WorkflowSchemaError("errorCode must be null or a non-empty string")
        artifact_bytes = payload["artifactBytes"]
        if isinstance(artifact_bytes, bool) or not isinstance(artifact_bytes, int) or artifact_bytes < 0:
            raise WorkflowSchemaError("artifactBytes must be a non-negative integer")
        return cls(
            schema_version=STATUS_SCHEMA,
            run_id=_string(payload, "runId"),
            stage=_string(payload, "stage"),
            status=status,
            started_at=_timestamp(payload, "startedAt"),
            updated_at=_timestamp(payload, "updatedAt"),
            completed_at=completed_at,
            message=_string(payload, "message"),
            error_code=error_code,
            artifact_bytes=artifact_bytes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "stage": self.stage,
            "status": self.status,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "message": self.message,
            "errorCode": self.error_code,
            "artifactBytes": self.artifact_bytes,
        }


@dataclass(frozen=True)
class WorkflowEvent:
    schema_version: str
    run_id: str
    event_id: str
    event_type: str
    from_status: str | None
    to_status: str | None
    occurred_at: str
    message: str
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {EVENT_SCHEMA}")
        _string_value(self.run_id, "runId")
        _string_value(self.event_id, "eventId")
        _string_value(self.event_type, "eventType")
        if (self.from_status is None) != (self.to_status is None):
            raise WorkflowSchemaError("fromStatus and toStatus must both be null or strings")
        if self.from_status is not None:
            _string_value(self.from_status, "fromStatus")
            _string_value(self.to_status, "toStatus")
            if self.from_status not in WORKFLOW_STATUSES or self.to_status not in WORKFLOW_STATUSES:
                raise WorkflowSchemaError("event statuses are not known workflow statuses")
            if not legal_transition(self.from_status, self.to_status):
                raise WorkflowSchemaError("event transition is illegal")
        object.__setattr__(self, "occurred_at", _timestamp_value(self.occurred_at, "occurredAt"))
        _string_value(self.message, "message")
        if not isinstance(self.metadata, dict):
            raise WorkflowSchemaError("metadata must be an object")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowEvent":
        keys = {"schemaVersion", "runId", "eventId", "eventType", "fromStatus", "toStatus", "occurredAt", "message", "metadata"}
        _check_payload(payload, keys, keys)
        if _string(payload, "schemaVersion") != EVENT_SCHEMA:
            raise WorkflowSchemaError(f"schemaVersion must be {EVENT_SCHEMA}")
        from_status = payload["fromStatus"]
        to_status = payload["toStatus"]
        if (from_status is None) != (to_status is None):
            raise WorkflowSchemaError("fromStatus and toStatus must both be null or strings")
        if from_status is not None:
            _string_value(from_status, "fromStatus")
            _string_value(to_status, "toStatus")
            if from_status not in WORKFLOW_STATUSES or to_status not in WORKFLOW_STATUSES:
                raise WorkflowSchemaError("event statuses are not known workflow statuses")
            if not legal_transition(from_status, to_status):
                raise WorkflowSchemaError("event transition is illegal")
        metadata = payload["metadata"]
        if not isinstance(metadata, dict):
            raise WorkflowSchemaError("metadata must be an object")
        return cls(
            schema_version=EVENT_SCHEMA,
            run_id=_string(payload, "runId"),
            event_id=_string(payload, "eventId"),
            event_type=_string(payload, "eventType"),
            from_status=from_status,
            to_status=to_status,
            occurred_at=_timestamp(payload, "occurredAt"),
            message=_string(payload, "message"),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "eventId": self.event_id,
            "eventType": self.event_type,
            "fromStatus": self.from_status,
            "toStatus": self.to_status,
            "occurredAt": self.occurred_at,
            "message": self.message,
            "metadata": dict(self.metadata),
        }
