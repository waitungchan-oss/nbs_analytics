from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .canonical_evidence_registry import (
    CANONICAL_EVIDENCE_SCHEMA,
    CanonicalEvidenceRegistry,
)
from .workflow_models import canonical_sha256


class CanonicalEvidenceSchemaError(ValueError):
    """Raised when a canonical evidence envelope violates the v1 contract."""


_FIELDS = frozenset({
    "schemaVersion", "artifactKind", "runId", "writer", "writerVersion",
    "contractFingerprint", "status", "reasonCode", "lifecycle",
    "evidenceFingerprint", "payload",
})
_LIFECYCLE_REQUIRED_FIELDS = frozenset({"createdAt", "startedAt"})
_LIFECYCLE_OPTIONAL_FIELDS = frozenset({"decidedAt", "finalizedAt"})
_LIFECYCLE_ORDER = ("createdAt", "startedAt", "decidedAt", "finalizedAt")
_TASK_EVIDENCE_KINDS = frozenset({
    "risk", "spec_gate", "plan_gate", "implementation", "targeted_verification",
    "review", "full_verification", "hermes", "documentation", "git_integration",
})
_DIAGNOSIS_KINDS = frozenset({"protected_incident", "task_gate", "workflow_failure", "data_integrity"})
_DIAGNOSIS_OUTCOMES = frozenset({"diagnosed", "not_reproducible", "blocked", "no_action"})
_FINDING_CODES = frozenset({"malformed_artifact", "stale_artifact", "gate_failed", "protected_incident", "dependency_blocked", "unknown"})
_INCIDENT_CODES = frozenset({"policy_violation", "data_integrity", "security_boundary", "protected_incident", "stale_artifact"})
_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_AFFECTED_SCOPES = frozenset({"workflow_artifact", "canonical_evidence", "runtime", "security_boundary", "data_integrity"})


def _object(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CanonicalEvidenceSchemaError(f"{key} must be an object")
    return value


def _exact_keys(value: Any, key: str, expected: frozenset[str]) -> Mapping[str, Any]:
    mapping = _object(value, key)
    if set(mapping) != expected:
        raise CanonicalEvidenceSchemaError(f"{key} keys are invalid")
    return mapping


def _string(value: Any, key: str, cap: int | None = None) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise CanonicalEvidenceSchemaError(f"{key} must be a non-empty string")
    if cap is not None and len(value) > cap:
        raise CanonicalEvidenceSchemaError(f"{key} exceeds hard cap")
    return value


def _sha256(value: Any, key: str) -> str:
    value = _string(value, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CanonicalEvidenceSchemaError(f"{key} must be a lowercase SHA-256 hex digest")
    return value


def _run_id(value: Any) -> str:
    value = _string(value, "runId", 128)
    if Path(value).name != value or value in {".", ".."}:
        raise CanonicalEvidenceSchemaError("runId must be a bounded basename")
    return value


def _utc_timestamp(value: Any, key: str) -> tuple[str, datetime]:
    value = _string(value, key)
    if not value.endswith("Z"):
        raise CanonicalEvidenceSchemaError(f"{key} must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CanonicalEvidenceSchemaError(f"{key} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise CanonicalEvidenceSchemaError(f"{key} must use UTC")
    return value, parsed


def _reference(value: Any, key: str) -> str:
    value = _string(value, key, 128)
    if Path(value).name != value or value in {".", ".."}:
        raise CanonicalEvidenceSchemaError(f"{key} must be a basename")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _payload_task_gate(value: Any) -> dict[str, Any]:
    payload = _exact_keys(value, "payload", frozenset({"taskId", "decision", "requiredEvidenceKinds", "missingEvidenceKinds"}))
    task_id = _string(payload["taskId"], "taskId", 128)
    decision = _string(payload["decision"], "decision")
    required = _evidence_kind_list(payload["requiredEvidenceKinds"], "requiredEvidenceKinds")
    missing = _evidence_kind_list(payload["missingEvidenceKinds"], "missingEvidenceKinds")
    if not set(missing).issubset(required):
        raise CanonicalEvidenceSchemaError("missingEvidenceKinds must be a subset of requiredEvidenceKinds")
    return {"taskId": task_id, "decision": decision, "requiredEvidenceKinds": list(required), "missingEvidenceKinds": list(missing)}


def _evidence_kind_list(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 16:
        raise CanonicalEvidenceSchemaError(f"{key} must be a list capped at 16")
    items = tuple(_string(item, key, 64) for item in value)
    if any(item not in _TASK_EVIDENCE_KINDS for item in items):
        raise CanonicalEvidenceSchemaError(f"{key} contains an unknown evidence kind")
    return items


def _payload_terra(value: Any) -> dict[str, Any]:
    payload = _exact_keys(value, "payload", frozenset({"diagnosisKind", "outcome", "incidentRef", "findingCode"}))
    result = {
        "diagnosisKind": _string(payload["diagnosisKind"], "diagnosisKind", 128),
        "outcome": _string(payload["outcome"], "outcome", 128),
        "incidentRef": _reference(payload["incidentRef"], "incidentRef"),
        "findingCode": _string(payload["findingCode"], "findingCode", 128),
    }
    if result["diagnosisKind"] not in _DIAGNOSIS_KINDS or result["outcome"] not in _DIAGNOSIS_OUTCOMES or result["findingCode"] not in _FINDING_CODES:
        raise CanonicalEvidenceSchemaError("terra diagnosis payload enum is invalid")
    return result


def _payload_incident(value: Any) -> dict[str, Any]:
    payload = _object(value, "payload")
    required = {"incidentCode", "severity", "affectedScope", "terraDiagnosisRequired"}
    optional = {"terraDiagnosisRef"}
    if not required <= set(payload) or not set(payload) <= required | optional:
        raise CanonicalEvidenceSchemaError("protected incident payload keys are invalid")
    required_flag = payload["terraDiagnosisRequired"]
    if not isinstance(required_flag, bool):
        raise CanonicalEvidenceSchemaError("terraDiagnosisRequired must be a boolean scalar")
    result: dict[str, Any] = {
        "incidentCode": _string(payload["incidentCode"], "incidentCode", 128),
        "severity": _string(payload["severity"], "severity", 128),
        "affectedScope": _string(payload["affectedScope"], "affectedScope", 128),
        "terraDiagnosisRequired": required_flag,
    }
    if result["incidentCode"] not in _INCIDENT_CODES or result["severity"] not in _SEVERITIES or result["affectedScope"] not in _AFFECTED_SCOPES:
        raise CanonicalEvidenceSchemaError("protected incident payload enum is invalid")
    if "terraDiagnosisRef" in payload:
        result["terraDiagnosisRef"] = _reference(payload["terraDiagnosisRef"], "terraDiagnosisRef")
    return result


@dataclass(frozen=True)
class CanonicalEvidenceEnvelope:
    schema_version: str
    artifact_kind: str
    run_id: str
    writer: str
    writer_version: str
    contract_fingerprint: str
    status: str
    reason_code: str | None
    lifecycle: Mapping[str, str]
    evidence_fingerprint: str
    payload: Mapping[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any], expected_kind: str | None = None) -> "CanonicalEvidenceEnvelope":
        payload = _exact_keys(value, "canonical evidence envelope", _FIELDS)
        if _string(payload["schemaVersion"], "schemaVersion") != CANONICAL_EVIDENCE_SCHEMA:
            raise CanonicalEvidenceSchemaError("schemaVersion is invalid")
        kind = _string(payload["artifactKind"], "artifactKind")
        if expected_kind is not None and kind != expected_kind:
            raise CanonicalEvidenceSchemaError("artifactKind does not match expected kind")
        try:
            entry = CanonicalEvidenceRegistry().for_kind(kind)
        except ValueError as exc:
            raise CanonicalEvidenceSchemaError("artifactKind is invalid") from exc
        writer = _string(payload["writer"], "writer", 128)
        if writer != entry.writer:
            raise CanonicalEvidenceSchemaError("writer does not match artifactKind")
        writer_version = _string(payload["writerVersion"], "writerVersion", 128)
        if writer_version not in entry.writer_versions:
            raise CanonicalEvidenceSchemaError("writerVersion is not registered for writer")
        status = _string(payload["status"], "status")
        if status not in entry.status_reasons:
            raise CanonicalEvidenceSchemaError("status is invalid for artifactKind")
        reason = payload["reasonCode"]
        allowed_reasons = entry.status_reasons[status]
        if allowed_reasons is None:
            if reason is not None:
                raise CanonicalEvidenceSchemaError("successful status must have a null reasonCode")
        elif not isinstance(reason, str) or reason not in allowed_reasons:
            raise CanonicalEvidenceSchemaError("reasonCode is invalid for status")
        lifecycle = _object(payload["lifecycle"], "lifecycle")
        lifecycle_keys = set(lifecycle)
        if (
            not _LIFECYCLE_REQUIRED_FIELDS <= lifecycle_keys
            or not lifecycle_keys <= _LIFECYCLE_REQUIRED_FIELDS | _LIFECYCLE_OPTIONAL_FIELDS
            or "finalizedAt" in lifecycle_keys and "decidedAt" not in lifecycle_keys
        ):
            raise CanonicalEvidenceSchemaError("lifecycle keys are invalid")
        normalized_lifecycle: dict[str, str] = {}
        ordered = []
        for key in _LIFECYCLE_ORDER:
            if key not in lifecycle:
                continue
            raw, parsed = _utc_timestamp(lifecycle[key], key)
            normalized_lifecycle[key] = raw
            ordered.append(parsed)
        if ordered != sorted(ordered):
            raise CanonicalEvidenceSchemaError("lifecycle timestamps are out of order")
        if kind == "task_gate":
            kind_payload = _payload_task_gate(payload["payload"])
            if kind_payload["decision"] != status:
                raise CanonicalEvidenceSchemaError("task gate decision must equal status")
        elif kind == "terra_diagnosis":
            kind_payload = _payload_terra(payload["payload"])
        else:
            kind_payload = _payload_incident(payload["payload"])
        evidence_fingerprint = _sha256(payload["evidenceFingerprint"], "evidenceFingerprint")
        fingerprint_payload = dict(payload)
        fingerprint_payload.pop("evidenceFingerprint")
        if evidence_fingerprint != canonical_sha256(fingerprint_payload):
            raise CanonicalEvidenceSchemaError("evidenceFingerprint does not match canonical payload")
        contract_fingerprint = _sha256(payload["contractFingerprint"], "contractFingerprint")
        return cls(
            schema_version=CANONICAL_EVIDENCE_SCHEMA,
            artifact_kind=kind,
            run_id=_run_id(payload["runId"]),
            writer=writer,
            writer_version=writer_version,
            contract_fingerprint=contract_fingerprint,
            status=status,
            reason_code=reason,
            lifecycle=_freeze(normalized_lifecycle),
            evidence_fingerprint=evidence_fingerprint,
            payload=_freeze(kind_payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version, "artifactKind": self.artifact_kind,
            "runId": self.run_id, "writer": self.writer, "writerVersion": self.writer_version,
            "contractFingerprint": self.contract_fingerprint, "status": self.status,
            "reasonCode": self.reason_code, "lifecycle": _thaw(self.lifecycle),
            "evidenceFingerprint": self.evidence_fingerprint, "payload": _thaw(self.payload),
        }

    def canonical_fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("evidenceFingerprint")
        return canonical_sha256(payload)

    @property
    def is_finalized(self) -> bool:
        return "decidedAt" in self.lifecycle and "finalizedAt" in self.lifecycle
