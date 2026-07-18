from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .workflow_models import canonical_sha256


DOCUMENTATION_EVIDENCE_SCHEMA = "documentation-evidence-v1"
DOCUMENTATION_PROPOSAL_SCHEMA = "documentation-proposal-v1"
DOCUMENTATION_APPLICATION_SCHEMA = "documentation-application-v1"
DOCUMENTATION_POLICY_SCHEMA = "documentation-policy-v1"

TARGET_KINDS = frozenset({"brief_backfill", "system_map", "adr"})
OPERATIONS = frozenset({"update_managed_block", "replace_section", "create_file"})
PROPOSAL_STATUSES = frozenset({
    "ready", "no_documentation_needed", "blocked", "context_overflow", "invalid_agent_output",
})
APPLICATION_STATUSES = frozenset({
    "preview_ready", "awaiting_target_approval", "applied", "partially_applied", "blocked",
})


class DocumentationSchemaError(ValueError):
    """Raised when a documentation artifact violates its strict schema."""


def _keys(payload: Mapping[str, Any], required: set[str]) -> None:
    if not isinstance(payload, dict):
        raise DocumentationSchemaError("documentation payload must be an object")
    actual = set(payload)
    missing = sorted(required - actual)
    unknown = sorted(actual - required)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing fields: " + ", ".join(missing))
        if unknown:
            details.append("unknown fields: " + ", ".join(unknown))
        raise DocumentationSchemaError("documentation payload keys are invalid (" + "; ".join(details) + ")")


def _string(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocumentationSchemaError(f"{key} must be a non-empty string")
    return value


def _hash(value: Any, key: str) -> str:
    value = _string(value, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise DocumentationSchemaError(f"{key} must be a lowercase SHA-256 hex digest")
    return value


def _timestamp(value: Any, key: str) -> str:
    value = _string(value, key)
    parsed_value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(parsed_value)
    except ValueError as exc:
        raise DocumentationSchemaError(f"{key} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DocumentationSchemaError(f"{key} must include a timezone")
    return parsed.isoformat()


def _strings(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DocumentationSchemaError(f"{key} must be a list")
    return tuple(_string(item, key) for item in value)


def _schema(payload: Mapping[str, Any], expected: str) -> None:
    if _string(payload["schemaVersion"], "schemaVersion") != expected:
        raise DocumentationSchemaError(f"schemaVersion must be {expected}")


def _source(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DocumentationSchemaError("sources entries must be objects")
    _keys(value, {"path", "sha256"})
    return {"path": _string(value["path"], "path"), "sha256": _hash(value["sha256"], "sha256")}


def _proposal_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DocumentationSchemaError("proposals entries must be objects")
    _keys(value, {"targetKind", "targetIdentity", "operation", "content", "contentSha256"})
    target_kind = _string(value["targetKind"], "targetKind")
    if target_kind not in TARGET_KINDS:
        raise DocumentationSchemaError(f"targetKind must be one of: {', '.join(sorted(TARGET_KINDS))}")
    operation = _string(value["operation"], "operation")
    if operation not in OPERATIONS:
        raise DocumentationSchemaError(f"operation must be one of: {', '.join(sorted(OPERATIONS))}")
    return {
        "targetKind": target_kind,
        "targetIdentity": _string(value["targetIdentity"], "targetIdentity"),
        "operation": operation,
        "content": _string(value["content"], "content"),
        "contentSha256": _hash(value["contentSha256"], "contentSha256"),
    }


@dataclass(frozen=True)
class DocumentationEvidence:
    schema_version: str
    task_id: str
    generated_at: str
    sources: tuple[dict[str, str], ...]
    guardrails: dict[str, str]
    evidence_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("evidenceFingerprint")
        return canonical_sha256(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationEvidence":
        fields = {"schemaVersion", "taskId", "generatedAt", "sources", "guardrails", "evidenceFingerprint"}
        _keys(payload, fields)
        _schema(payload, DOCUMENTATION_EVIDENCE_SCHEMA)
        if not isinstance(payload["sources"], list):
            raise DocumentationSchemaError("sources must be a list")
        guardrails = payload["guardrails"]
        if not isinstance(guardrails, dict):
            raise DocumentationSchemaError("guardrails must be an object")
        _keys(guardrails, {"revenueScope", "mayBaseline"})
        return cls(
            DOCUMENTATION_EVIDENCE_SCHEMA,
            _string(payload["taskId"], "taskId"),
            _timestamp(payload["generatedAt"], "generatedAt"),
            tuple(_source(item) for item in payload["sources"]),
            {key: _string(guardrails[key], key) for key in ("revenueScope", "mayBaseline")},
            _hash(payload["evidenceFingerprint"], "evidenceFingerprint"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "sources": [dict(item) for item in self.sources],
            "guardrails": dict(self.guardrails),
            "evidenceFingerprint": self.evidence_fingerprint,
        }


@dataclass(frozen=True)
class DocumentationProposal:
    schema_version: str
    task_id: str
    generated_at: str
    evidence: DocumentationEvidence
    evidence_fingerprint: str
    status: str
    proposals: tuple[dict[str, Any], ...]
    proposal_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("proposalFingerprint")
        return canonical_sha256(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationProposal":
        fields = {"schemaVersion", "taskId", "generatedAt", "evidence", "evidenceFingerprint", "status", "proposals", "proposalFingerprint"}
        _keys(payload, fields)
        _schema(payload, DOCUMENTATION_PROPOSAL_SCHEMA)
        evidence = DocumentationEvidence.from_dict(payload["evidence"])
        evidence_fingerprint = _hash(payload["evidenceFingerprint"], "evidenceFingerprint")
        if evidence_fingerprint != evidence.evidence_fingerprint:
            raise DocumentationSchemaError("evidence fingerprint differs from evidence")
        status = _string(payload["status"], "status")
        if status not in PROPOSAL_STATUSES:
            raise DocumentationSchemaError("status is not a valid proposal status")
        if not isinstance(payload["proposals"], list):
            raise DocumentationSchemaError("proposals must be a list")
        proposals = tuple(_proposal_item(item) for item in payload["proposals"])
        identities = [item["targetIdentity"] for item in proposals]
        if len(identities) != len(set(identities)):
            raise DocumentationSchemaError("duplicate targetIdentity in proposals")
        return cls(
            DOCUMENTATION_PROPOSAL_SCHEMA,
            _string(payload["taskId"], "taskId"),
            _timestamp(payload["generatedAt"], "generatedAt"),
            evidence,
            evidence_fingerprint,
            status,
            proposals,
            _hash(payload["proposalFingerprint"], "proposalFingerprint"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "evidence": self.evidence.to_dict(),
            "evidenceFingerprint": self.evidence_fingerprint,
            "status": self.status,
            "proposals": [dict(item) for item in self.proposals],
            "proposalFingerprint": self.proposal_fingerprint,
        }


@dataclass(frozen=True)
class DocumentationApplication:
    schema_version: str
    task_id: str
    generated_at: str
    proposal_fingerprint: str
    status: str
    applications: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationApplication":
        fields = {"schemaVersion", "taskId", "generatedAt", "proposalFingerprint", "status", "applications"}
        _keys(payload, fields)
        _schema(payload, DOCUMENTATION_APPLICATION_SCHEMA)
        status = _string(payload["status"], "status")
        if status not in APPLICATION_STATUSES:
            raise DocumentationSchemaError("status is not a valid application status")
        if not isinstance(payload["applications"], list):
            raise DocumentationSchemaError("applications must be a list")
        applications = []
        for item in payload["applications"]:
            if not isinstance(item, dict):
                raise DocumentationSchemaError("applications entries must be objects")
            _keys(item, {"targetKind", "targetIdentity", "operation", "result", "appliedSha256"})
            target_kind = _string(item["targetKind"], "targetKind")
            operation = _string(item["operation"], "operation")
            if target_kind not in TARGET_KINDS:
                raise DocumentationSchemaError("targetKind is invalid")
            if operation not in OPERATIONS:
                raise DocumentationSchemaError("operation is invalid")
            applied_hash = item["appliedSha256"]
            if applied_hash is not None:
                applied_hash = _hash(applied_hash, "appliedSha256")
            applications.append({
                "targetKind": target_kind,
                "targetIdentity": _string(item["targetIdentity"], "targetIdentity"),
                "operation": operation,
                "result": _string(item["result"], "result"),
                "appliedSha256": applied_hash,
            })
        return cls(
            DOCUMENTATION_APPLICATION_SCHEMA,
            _string(payload["taskId"], "taskId"),
            _timestamp(payload["generatedAt"], "generatedAt"),
            _hash(payload["proposalFingerprint"], "proposalFingerprint"),
            status,
            tuple(applications),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "proposalFingerprint": self.proposal_fingerprint,
            "status": self.status,
            "applications": [dict(item) for item in self.applications],
        }


@dataclass(frozen=True)
class DocumentationTargetPolicy:
    schema_version: str
    target_kind: str
    risk_tier: str
    operations: tuple[str, ...]
    repo_roots: tuple[str, ...]
    repo_paths: tuple[str, ...]
    obsidian_subdirectory: str
    requires_explicit_target_approval: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationTargetPolicy":
        fields = {"schemaVersion", "targetKind", "riskTier", "operations", "repoRoots", "repoPaths", "obsidianSubdirectory", "requiresExplicitTargetApproval"}
        _keys(payload, fields)
        _schema(payload, DOCUMENTATION_POLICY_SCHEMA)
        target_kind = _string(payload["targetKind"], "targetKind")
        if target_kind not in TARGET_KINDS:
            raise DocumentationSchemaError("targetKind is invalid")
        operations = _strings(payload["operations"], "operations")
        if not set(operations) <= OPERATIONS:
            raise DocumentationSchemaError("operations contains an invalid operation")
        if not isinstance(payload["requiresExplicitTargetApproval"], bool):
            raise DocumentationSchemaError("requiresExplicitTargetApproval must be a boolean")
        return cls(
            DOCUMENTATION_POLICY_SCHEMA,
            target_kind,
            _string(payload["riskTier"], "riskTier"),
            operations,
            _strings(payload["repoRoots"], "repoRoots"),
            _strings(payload["repoPaths"], "repoPaths"),
            _string(payload["obsidianSubdirectory"], "obsidianSubdirectory"),
            payload["requiresExplicitTargetApproval"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "targetKind": self.target_kind,
            "riskTier": self.risk_tier,
            "operations": list(self.operations),
            "repoRoots": list(self.repo_roots),
            "repoPaths": list(self.repo_paths),
            "obsidianSubdirectory": self.obsidian_subdirectory,
            "requiresExplicitTargetApproval": self.requires_explicit_target_approval,
        }


__all__ = [
    "APPLICATION_STATUSES", "DOCUMENTATION_APPLICATION_SCHEMA", "DOCUMENTATION_EVIDENCE_SCHEMA",
    "DOCUMENTATION_POLICY_SCHEMA", "DOCUMENTATION_PROPOSAL_SCHEMA", "OPERATIONS", "PROPOSAL_STATUSES",
    "TARGET_KINDS", "DocumentationApplication", "DocumentationEvidence", "DocumentationProposal",
    "DocumentationSchemaError", "DocumentationTargetPolicy",
]
