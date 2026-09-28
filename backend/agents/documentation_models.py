from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Mapping

from .workflow_models import canonical_sha256


DOCUMENTATION_EVIDENCE_SCHEMA = "documentation-evidence-v1"
DOCUMENTATION_DRAFT_SCHEMA = "documentation-draft-v1"
DOCUMENTATION_PROPOSAL_SCHEMA = "documentation-proposal-v1"
DOCUMENTATION_APPLICATION_SCHEMA = "documentation-application-v1"
DOCUMENTATION_POLICY_SCHEMA = "documentation-policy-v1"
DOCUMENTATION_EVIDENCE_V2_SCHEMA = "documentation-evidence-v2"
DOCUMENTATION_DRAFT_V2_SCHEMA = "documentation-draft-v2"
DOCUMENTATION_PROPOSAL_V2_SCHEMA = "documentation-proposal-v2"
DOCUMENTATION_APPLICATION_V2_SCHEMA = "documentation-application-v2"
DOCUMENTATION_TARGET_V2_SCHEMA = "documentation-target-v2"
DOCUMENTATION_TARGET_POLICY_V2_SCHEMA = "documentation-target-policy-v2"

TARGET_KINDS = frozenset({"brief_backfill", "system_map", "adr"})
OPERATIONS = frozenset({"update_managed_block", "replace_section", "create_file"})
PROPOSAL_STATUSES = frozenset({
    "ready", "no_documentation_needed", "blocked", "context_overflow", "invalid_agent_output",
})
DRAFT_STATUSES = frozenset({
    "ready", "no_documentation_needed", "blocked", "context_overflow",
})
APPLICATION_STATUSES = frozenset({
    "preview_ready", "awaiting_target_approval", "applied", "partially_applied", "blocked",
})

_REVENUE_SCOPE = "不含掛賬核銷與TT退款轉團款"
_MAY_BASELINE = "HKD 12,057,968"
_TARGET_POLICY_RULES = {
    "brief_backfill": {
        "riskTier": "low", "operations": ("update_managed_block",),
        "repoRoots": ("docs/briefs",), "repoPaths": (),
        "obsidianSubdirectory": "70_Codex_Briefs", "requiresExplicitTargetApproval": False,
    },
    "system_map": {
        "riskTier": "high", "operations": ("replace_section",),
        "repoRoots": (), "repoPaths": ("NBS_ANALYTICS_SYSTEM_MAP.md",),
        "obsidianSubdirectory": "10_System", "requiresExplicitTargetApproval": True,
    },
    "adr": {
        "riskTier": "high", "operations": ("create_file",),
        "repoRoots": ("Summay",), "repoPaths": (),
        "obsidianSubdirectory": "20_Decisions", "requiresExplicitTargetApproval": True,
    },
}
_TARGET_V2_ROWS = (
    ("handoff.current-conclusion", "handoff", "NBS_ANALYTICS_HANDOFF.md", "## 1. 本輪交接結論"),
    ("handoff.verification-snapshot", "handoff", "NBS_ANALYTICS_HANDOFF.md", "### 5.3 最近驗證快照"),
    ("runbook.pipeline-rollout-gate", "runbook", "docs/agents/ACCEPTANCE_PIPELINE_HARDENING_RUNBOOK.md", "## Rollout handoff gate（Task 6）"),
    ("runbook.shard-boundary", "runbook", "docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md", "## Boundary"),
    ("runbook.parallel-rollout-boundary", "runbook", "docs/agents/ACCEPTANCE_PARALLEL_ROLLOUT_RUNBOOK.md", "## Boundary"),
)
DOCUMENTATION_TARGETS_V2 = tuple({
    "schemaVersion": DOCUMENTATION_TARGET_V2_SCHEMA, "targetId": target_id,
    "targetKind": kind, "repoPath": path, "sectionHeading": heading,
    "operation": "replace_section", "riskTier": "high",
    "requiredApprovalId": target_id, "mustExist": True,
} for target_id, kind, path, heading in _TARGET_V2_ROWS)


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


def _v2_keys(payload: Any, fields: set[str], expected: str) -> None:
    if not isinstance(payload, dict):
        raise DocumentationSchemaError("documentation payload must be an object")
    if "schemaVersion" not in payload:
        raise DocumentationSchemaError("schemaVersion is required")
    _schema(payload, expected)
    _keys(payload, fields)


def _source(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DocumentationSchemaError("sources entries must be objects")
    _keys(value, {"path", "sha256"})
    return {"path": _string(value["path"], "path"), "sha256": _hash(value["sha256"], "sha256")}


def _repo_relative_path(value: Any, key: str) -> str:
    value = _string(value, key)
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise DocumentationSchemaError(f"{key} must be a safe repo-relative POSIX path")
    return value


def _canonical_fingerprint(payload: dict[str, Any], fingerprint_key: str) -> str:
    unsigned = dict(payload)
    unsigned.pop(fingerprint_key)
    return canonical_sha256(unsigned)


def _target_dict(target_id: Any) -> dict[str, Any]:
    target_id = _string(target_id, "targetId")
    for target in DOCUMENTATION_TARGETS_V2:
        if target["targetId"] == target_id:
            return dict(target)
    raise DocumentationSchemaError("targetId is not in the fixed documentation target catalog")


def _gate_result(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DocumentationSchemaError("gateResults entries must be objects")
    _keys(value, {"gate", "status", "sourceFingerprint", "evidenceFingerprint"})
    return {
        "gate": _string(value["gate"], "gate"),
        "status": _string(value["status"], "status"),
        "sourceFingerprint": _hash(value["sourceFingerprint"], "sourceFingerprint"),
        "evidenceFingerprint": _hash(value["evidenceFingerprint"], "evidenceFingerprint"),
    }


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
    content = _string(value["content"], "content")
    content_hash = _hash(value["contentSha256"], "contentSha256")
    if content_hash != sha256(content.encode("utf-8")).hexdigest():
        raise DocumentationSchemaError("contentSha256 does not match content")
    return {
        "targetKind": target_kind,
        "targetIdentity": _string(value["targetIdentity"], "targetIdentity"),
        "operation": operation,
        "content": content,
        "contentSha256": content_hash,
    }


def _draft_item(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DocumentationSchemaError("draft proposals entries must be objects")
    _keys(value, {"targetKind", "content"})
    target_kind = _string(value["targetKind"], "targetKind")
    if target_kind not in TARGET_KINDS:
        raise DocumentationSchemaError(
            f"targetKind must be one of: {', '.join(sorted(TARGET_KINDS))}"
        )
    return {"targetKind": target_kind, "content": _string(value["content"], "content")}


@dataclass(frozen=True)
class DocumentationDraft:
    schema_version: str
    evidence_fingerprint: str
    status: str
    proposals: tuple[dict[str, str], ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationDraft":
        fields = {"schemaVersion", "evidenceFingerprint", "status", "proposals"}
        _keys(payload, fields)
        _schema(payload, DOCUMENTATION_DRAFT_SCHEMA)
        status = _string(payload["status"], "status")
        if status not in DRAFT_STATUSES:
            raise DocumentationSchemaError("status is not a valid draft status")
        if not isinstance(payload["proposals"], list):
            raise DocumentationSchemaError("proposals must be a list")
        proposals = tuple(_draft_item(item) for item in payload["proposals"])
        if status != "ready" and proposals:
            raise DocumentationSchemaError("non-ready draft must not contain proposals")
        return cls(
            DOCUMENTATION_DRAFT_SCHEMA,
            _hash(payload["evidenceFingerprint"], "evidenceFingerprint"),
            status,
            proposals,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "evidenceFingerprint": self.evidence_fingerprint,
            "status": self.status,
            "proposals": [dict(item) for item in self.proposals],
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
        if guardrails != {"revenueScope": _REVENUE_SCOPE, "mayBaseline": _MAY_BASELINE}:
            raise DocumentationSchemaError("guardrails do not match protected governance")
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
        proposal_fingerprint = _hash(payload["proposalFingerprint"], "proposalFingerprint")
        fingerprint_payload = dict(payload)
        fingerprint_payload.pop("proposalFingerprint")
        if proposal_fingerprint != canonical_sha256(fingerprint_payload):
            raise DocumentationSchemaError("proposal fingerprint does not match canonical payload")
        return cls(
            DOCUMENTATION_PROPOSAL_SCHEMA,
            _string(payload["taskId"], "taskId"),
            _timestamp(payload["generatedAt"], "generatedAt"),
            evidence,
            evidence_fingerprint,
            status,
            proposals,
            proposal_fingerprint,
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
        identities = [item["targetIdentity"] for item in applications]
        if len(identities) != len(set(identities)):
            raise DocumentationSchemaError("duplicate targetIdentity in applications")
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
        actual = {
            "riskTier": _string(payload["riskTier"], "riskTier"),
            "operations": operations,
            "repoRoots": _strings(payload["repoRoots"], "repoRoots"),
            "repoPaths": _strings(payload["repoPaths"], "repoPaths"),
            "obsidianSubdirectory": _string(payload["obsidianSubdirectory"], "obsidianSubdirectory"),
            "requiresExplicitTargetApproval": payload["requiresExplicitTargetApproval"],
        }
        if actual != _TARGET_POLICY_RULES[target_kind]:
            raise DocumentationSchemaError(f"policy mapping is invalid for targetKind {target_kind}")
        return cls(
            DOCUMENTATION_POLICY_SCHEMA,
            target_kind,
            actual["riskTier"],
            operations,
            actual["repoRoots"],
            actual["repoPaths"],
            actual["obsidianSubdirectory"],
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


@dataclass(frozen=True)
class DocumentationTargetV2:
    schema_version: str
    target_id: str
    target_kind: str
    repo_path: str
    section_heading: str
    operation: str
    risk_tier: str
    required_approval_id: str
    must_exist: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationTargetV2":
        fields = {
            "schemaVersion", "targetId", "targetKind", "repoPath", "sectionHeading",
            "operation", "riskTier", "requiredApprovalId", "mustExist",
        }
        _v2_keys(payload, fields, DOCUMENTATION_TARGET_V2_SCHEMA)
        target_id = _string(payload["targetId"], "targetId")
        if not isinstance(payload["mustExist"], bool):
            raise DocumentationSchemaError("mustExist must be a boolean")
        expected = _target_dict(target_id)
        if payload != expected:
            raise DocumentationSchemaError("target catalog mapping does not match the fixed policy")
        return cls(
            DOCUMENTATION_TARGET_V2_SCHEMA,
            target_id,
            _string(payload["targetKind"], "targetKind"),
            _repo_relative_path(payload["repoPath"], "repoPath"),
            _string(payload["sectionHeading"], "sectionHeading"),
            _string(payload["operation"], "operation"),
            _string(payload["riskTier"], "riskTier"),
            _string(payload["requiredApprovalId"], "requiredApprovalId"),
            payload["mustExist"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "targetId": self.target_id,
            "targetKind": self.target_kind,
            "repoPath": self.repo_path,
            "sectionHeading": self.section_heading,
            "operation": self.operation,
            "riskTier": self.risk_tier,
            "requiredApprovalId": self.required_approval_id,
            "mustExist": self.must_exist,
        }


@dataclass(frozen=True)
class DocumentationEvidenceV2:
    schema_version: str
    task_id: str
    generated_at: str
    run_id: str
    commit_sha: str
    source_fingerprint: str
    selected_target_id: str
    sources: tuple[dict[str, str], ...]
    gate_results: tuple[dict[str, str], ...]
    guardrails: dict[str, str]
    expected_section_sha256: str
    evidence_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        return _canonical_fingerprint(self.to_dict(), "evidenceFingerprint")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationEvidenceV2":
        fields = {
            "schemaVersion", "taskId", "generatedAt", "runId", "commitSha",
            "sourceFingerprint", "selectedTargetId", "sources", "gateResults",
            "guardrails", "expectedSectionSha256", "evidenceFingerprint",
        }
        _v2_keys(payload, fields, DOCUMENTATION_EVIDENCE_V2_SCHEMA)
        target_id = _string(payload["selectedTargetId"], "selectedTargetId")
        _target_dict(target_id)
        if not isinstance(payload["sources"], list) or not payload["sources"]:
            raise DocumentationSchemaError("sources must be a non-empty list")
        sources = []
        for item in payload["sources"]:
            if not isinstance(item, dict):
                raise DocumentationSchemaError("sources entries must be objects")
            _keys(item, {"path", "sha256"})
            sources.append({
                "path": _repo_relative_path(item["path"], "path"),
                "sha256": _hash(item["sha256"], "sha256"),
            })
        if len({item["path"] for item in sources}) != len(sources):
            raise DocumentationSchemaError("duplicate source path in evidence")
        if not isinstance(payload["gateResults"], list) or not payload["gateResults"]:
            raise DocumentationSchemaError("gateResults must be a non-empty list")
        gate_results = tuple(_gate_result(item) for item in payload["gateResults"])
        gate_names = [item["gate"] for item in gate_results]
        if len(set(gate_names)) != len(gate_names):
            raise DocumentationSchemaError("duplicate gate in gateResults")
        source_fingerprint = _hash(payload["sourceFingerprint"], "sourceFingerprint")
        if any(item["sourceFingerprint"] != source_fingerprint for item in gate_results):
            raise DocumentationSchemaError("gate result source fingerprint differs from evidence")
        guardrails = payload["guardrails"]
        if not isinstance(guardrails, dict):
            raise DocumentationSchemaError("guardrails must be an object")
        _keys(guardrails, {"revenueScope", "mayBaseline"})
        if guardrails != {"revenueScope": _REVENUE_SCOPE, "mayBaseline": _MAY_BASELINE}:
            raise DocumentationSchemaError("guardrails do not match protected governance")
        commit_sha = _string(payload["commitSha"], "commitSha")
        if len(commit_sha) not in {40, 64} or any(char not in "0123456789abcdef" for char in commit_sha):
            raise DocumentationSchemaError("commitSha must be a lowercase Git object ID")
        model = cls(
            DOCUMENTATION_EVIDENCE_V2_SCHEMA,
            _string(payload["taskId"], "taskId"),
            _timestamp(payload["generatedAt"], "generatedAt"),
            _string(payload["runId"], "runId"),
            commit_sha,
            source_fingerprint,
            target_id,
            tuple(sources),
            gate_results,
            {key: _string(guardrails[key], key) for key in ("revenueScope", "mayBaseline")},
            _hash(payload["expectedSectionSha256"], "expectedSectionSha256"),
            _hash(payload["evidenceFingerprint"], "evidenceFingerprint"),
        )
        if model.canonical_fingerprint != model.evidence_fingerprint:
            raise DocumentationSchemaError("evidence fingerprint does not match canonical payload")
        return model

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "runId": self.run_id,
            "commitSha": self.commit_sha,
            "sourceFingerprint": self.source_fingerprint,
            "selectedTargetId": self.selected_target_id,
            "sources": [dict(item) for item in self.sources],
            "gateResults": [dict(item) for item in self.gate_results],
            "guardrails": dict(self.guardrails),
            "expectedSectionSha256": self.expected_section_sha256,
            "evidenceFingerprint": self.evidence_fingerprint,
        }


@dataclass(frozen=True)
class DocumentationDraftV2:
    schema_version: str
    evidence_fingerprint: str
    status: str
    proposals: tuple[dict[str, str], ...]
    draft_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        return _canonical_fingerprint(self.to_dict(), "draftFingerprint")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationDraftV2":
        fields = {"schemaVersion", "evidenceFingerprint", "status", "proposals", "draftFingerprint"}
        _v2_keys(payload, fields, DOCUMENTATION_DRAFT_V2_SCHEMA)
        status = _string(payload["status"], "status")
        if status not in DRAFT_STATUSES:
            raise DocumentationSchemaError("status is not a valid draft status")
        if not isinstance(payload["proposals"], list):
            raise DocumentationSchemaError("proposals must be a list")
        proposals = []
        for item in payload["proposals"]:
            if not isinstance(item, dict):
                raise DocumentationSchemaError("draft proposals entries must be objects")
            _keys(item, {"targetId", "content"})
            target_id = _string(item["targetId"], "targetId")
            _target_dict(target_id)
            proposals.append({"targetId": target_id, "content": _string(item["content"], "content")})
        if len(proposals) != 1:
            raise DocumentationSchemaError("v2 draft must contain exactly one proposal")
        model = cls(
            DOCUMENTATION_DRAFT_V2_SCHEMA,
            _hash(payload["evidenceFingerprint"], "evidenceFingerprint"),
            status,
            tuple(proposals),
            _hash(payload["draftFingerprint"], "draftFingerprint"),
        )
        if model.canonical_fingerprint != model.draft_fingerprint:
            raise DocumentationSchemaError("draft fingerprint does not match canonical payload")
        return model

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "evidenceFingerprint": self.evidence_fingerprint,
            "status": self.status,
            "proposals": [dict(item) for item in self.proposals],
            "draftFingerprint": self.draft_fingerprint,
        }


@dataclass(frozen=True)
class DocumentationProposalV2:
    schema_version: str
    task_id: str
    generated_at: str
    evidence: DocumentationEvidenceV2
    evidence_fingerprint: str
    status: str
    target_id: str
    target_kind: str
    repo_path: str
    section_heading: str
    operation: str
    expected_section_sha256: str
    content: str
    content_sha256: str
    proposal_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        return _canonical_fingerprint(self.to_dict(), "proposalFingerprint")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationProposalV2":
        fields = {
            "schemaVersion", "taskId", "generatedAt", "evidence", "evidenceFingerprint",
            "status", "targetId", "targetKind", "repoPath", "sectionHeading", "operation",
            "expectedSectionSha256", "content", "contentSha256", "proposalFingerprint",
        }
        _v2_keys(payload, fields, DOCUMENTATION_PROPOSAL_V2_SCHEMA)
        evidence = DocumentationEvidenceV2.from_dict(payload["evidence"])
        target_id = _string(payload["targetId"], "targetId")
        target = _target_dict(target_id)
        evidence_fingerprint = _hash(payload["evidenceFingerprint"], "evidenceFingerprint")
        if evidence_fingerprint != evidence.evidence_fingerprint:
            raise DocumentationSchemaError("evidence fingerprint differs from evidence")
        task_id = _string(payload["taskId"], "taskId")
        generated_at = _timestamp(payload["generatedAt"], "generatedAt")
        if task_id != evidence.task_id:
            raise DocumentationSchemaError("proposal taskId differs from evidence taskId")
        if datetime.fromisoformat(generated_at) < datetime.fromisoformat(evidence.generated_at):
            raise DocumentationSchemaError("proposal generatedAt predates its evidence")
        if target_id != evidence.selected_target_id:
            raise DocumentationSchemaError("proposal targetId differs from selected evidence target")
        derived = {
            "targetKind": target["targetKind"],
            "repoPath": target["repoPath"],
            "sectionHeading": target["sectionHeading"],
            "operation": target["operation"],
            "expectedSectionSha256": evidence.expected_section_sha256,
        }
        actual = {
            "targetKind": _string(payload["targetKind"], "targetKind"),
            "repoPath": _repo_relative_path(payload["repoPath"], "repoPath"),
            "sectionHeading": _string(payload["sectionHeading"], "sectionHeading"),
            "operation": _string(payload["operation"], "operation"),
            "expectedSectionSha256": _hash(payload["expectedSectionSha256"], "expectedSectionSha256"),
        }
        if actual != derived:
            raise DocumentationSchemaError("proposal target identity differs from fixed policy or evidence")
        status = _string(payload["status"], "status")
        if status not in PROPOSAL_STATUSES:
            raise DocumentationSchemaError("status is not a valid proposal status")
        content = _string(payload["content"], "content")
        content_sha256 = _hash(payload["contentSha256"], "contentSha256")
        if content_sha256 != sha256(content.encode("utf-8")).hexdigest():
            raise DocumentationSchemaError("contentSha256 does not match content")
        proposal_fingerprint = _hash(payload["proposalFingerprint"], "proposalFingerprint")
        model = cls(
            DOCUMENTATION_PROPOSAL_V2_SCHEMA,
            task_id,
            generated_at,
            evidence,
            evidence_fingerprint,
            status,
            target_id,
            actual["targetKind"],
            actual["repoPath"],
            actual["sectionHeading"],
            actual["operation"],
            actual["expectedSectionSha256"],
            content,
            content_sha256,
            proposal_fingerprint,
        )
        if model.canonical_fingerprint != proposal_fingerprint:
            raise DocumentationSchemaError("proposal fingerprint does not match canonical payload")
        return model

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "evidence": self.evidence.to_dict(),
            "evidenceFingerprint": self.evidence_fingerprint,
            "status": self.status,
            "targetId": self.target_id,
            "targetKind": self.target_kind,
            "repoPath": self.repo_path,
            "sectionHeading": self.section_heading,
            "operation": self.operation,
            "expectedSectionSha256": self.expected_section_sha256,
            "content": self.content,
            "contentSha256": self.content_sha256,
            "proposalFingerprint": self.proposal_fingerprint,
        }


@dataclass(frozen=True)
class DocumentationApplicationV2:
    schema_version: str
    task_id: str
    generated_at: str
    run_id: str
    source_fingerprint: str
    proposal_fingerprint: str
    status: str
    target_id: str
    repo_path: str
    approval_id: str
    before_section_sha256: str
    after_section_sha256: str
    result: str
    application_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        return _canonical_fingerprint(self.to_dict(), "applicationFingerprint")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationApplicationV2":
        fields = {
            "schemaVersion", "taskId", "generatedAt", "runId", "sourceFingerprint",
            "proposalFingerprint", "status", "targetId", "repoPath", "approvalId",
            "beforeSectionSha256", "afterSectionSha256", "result", "applicationFingerprint",
        }
        _v2_keys(payload, fields, DOCUMENTATION_APPLICATION_V2_SCHEMA)
        target_id = _string(payload["targetId"], "targetId")
        target = _target_dict(target_id)
        repo_path = _repo_relative_path(payload["repoPath"], "repoPath")
        approval_id = _string(payload["approvalId"], "approvalId")
        if repo_path != target["repoPath"] or approval_id != target["requiredApprovalId"]:
            raise DocumentationSchemaError("application target or approval differs from fixed policy")
        status = _string(payload["status"], "status")
        if status not in {"preview_ready", "awaiting_target_approval", "applied", "blocked"}:
            raise DocumentationSchemaError("status is not a valid v2 application status")
        result = _string(payload["result"], "result")
        if result not in {"preview", "applied", "blocked"}:
            raise DocumentationSchemaError("result is not a valid bounded application result")
        expected_result = {
            "preview_ready": "preview",
            "awaiting_target_approval": "preview",
            "applied": "applied",
            "blocked": "blocked",
        }[status]
        if result != expected_result:
            raise DocumentationSchemaError("application result does not match status")
        model = cls(
            DOCUMENTATION_APPLICATION_V2_SCHEMA,
            _string(payload["taskId"], "taskId"),
            _timestamp(payload["generatedAt"], "generatedAt"),
            _string(payload["runId"], "runId"),
            _hash(payload["sourceFingerprint"], "sourceFingerprint"),
            _hash(payload["proposalFingerprint"], "proposalFingerprint"),
            status,
            target_id,
            repo_path,
            approval_id,
            _hash(payload["beforeSectionSha256"], "beforeSectionSha256"),
            _hash(payload["afterSectionSha256"], "afterSectionSha256"),
            result,
            _hash(payload["applicationFingerprint"], "applicationFingerprint"),
        )
        if model.canonical_fingerprint != model.application_fingerprint:
            raise DocumentationSchemaError("application fingerprint does not match canonical payload")
        return model

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "runId": self.run_id,
            "sourceFingerprint": self.source_fingerprint,
            "proposalFingerprint": self.proposal_fingerprint,
            "status": self.status,
            "targetId": self.target_id,
            "repoPath": self.repo_path,
            "approvalId": self.approval_id,
            "beforeSectionSha256": self.before_section_sha256,
            "afterSectionSha256": self.after_section_sha256,
            "result": self.result,
            "applicationFingerprint": self.application_fingerprint,
        }


__all__ = [
    "APPLICATION_STATUSES", "DOCUMENTATION_APPLICATION_SCHEMA", "DOCUMENTATION_EVIDENCE_SCHEMA",
    "DOCUMENTATION_APPLICATION_V2_SCHEMA", "DOCUMENTATION_DRAFT_V2_SCHEMA",
    "DOCUMENTATION_EVIDENCE_V2_SCHEMA", "DOCUMENTATION_POLICY_SCHEMA",
    "DOCUMENTATION_PROPOSAL_SCHEMA", "DOCUMENTATION_PROPOSAL_V2_SCHEMA",
    "DOCUMENTATION_TARGETS_V2", "DOCUMENTATION_TARGET_POLICY_V2_SCHEMA",
    "DOCUMENTATION_TARGET_V2_SCHEMA", "OPERATIONS", "PROPOSAL_STATUSES", "TARGET_KINDS",
    "DocumentationApplication", "DocumentationApplicationV2", "DocumentationDraftV2",
    "DocumentationEvidence", "DocumentationEvidenceV2", "DocumentationProposal",
    "DocumentationProposalV2", "DocumentationSchemaError", "DocumentationTargetPolicy",
    "DocumentationTargetV2",
]
