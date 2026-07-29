from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from .canonical_evidence_registry import CanonicalEvidenceRegistry
from .workflow_models import canonical_sha256


EVIDENCE_LINEAGE_INPUT_SCHEMA = "governance-graph-evidence-lineage-input-v1"
EVIDENCE_LINEAGE_SCHEMA = "governance-graph-evidence-lineage-v1"
LINEAGE_POLICY_VERSION = "e1-canonical-evidence-lineage-v1"
MAX_EVIDENCE_REFS = 12
MAX_DIAGNOSTICS = 12
MAX_TEXT = 128
SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+@#%=-]{0,127}$")
SECRET_LIKE_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9_-]{6,})", re.IGNORECASE)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_KINDS = frozenset({"node", "finding", "impact"})
RELATIONS = frozenset({"node_evidence", "finding_evidence", "impact_evidence"})
STATUSES = frozenset({"available", "missing", "unknown", "invalid", "blocked", "stale", "fingerprint_mismatch"})
REASONS = frozenset({
    "missing_evidence", "unknown_source", "blocked_upstream", "stale_snapshot",
    "fingerprint_mismatch", "invalid_contract", "invalid_path", "invalid_registry",
    "invalid_run_binding", "duplicate_artifact", "not_finalized", "reader_error",
})
FORBIDDEN_FIELDS = frozenset({
    "raw", "rawPayload", "payload", "prompt", "command", "stdout", "stderr", "secret",
    "absolutePath", "sqlite", "git", "writer", "approve", "dispatch", "apply",
})


class EvidenceLineageSchemaError(ValueError):
    """Raised when an E-1 contract contains unsafe or unbounded data."""


def _keys(value: Mapping[str, Any], required: set[str], allowed: set[str] | None = None) -> None:
    if not isinstance(value, Mapping):
        raise EvidenceLineageSchemaError("payload must be an object")
    allowed = required if allowed is None else allowed
    if set(value) != required or not set(value) <= allowed:
        raise EvidenceLineageSchemaError("payload keys are invalid")


def _optional_keys(value: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(value, Mapping) or not set(value) <= allowed:
        raise EvidenceLineageSchemaError("payload contains unknown fields")
    if FORBIDDEN_FIELDS.intersection(value):
        raise EvidenceLineageSchemaError("payload contains forbidden fields")


def _text(value: Any, key: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise EvidenceLineageSchemaError(f"{key} must be a bounded string")
    if value in {".", ".."} or "/" in value or "\\" in value or ".." in value or not SAFE_TEXT_RE.fullmatch(value):
        raise EvidenceLineageSchemaError(f"{key} must be safe metadata")
    if SECRET_LIKE_RE.search(value):
        raise EvidenceLineageSchemaError(f"{key} must not contain secret-like content")
    return value


def _sha(value: Any, key: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise EvidenceLineageSchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _canonical_filename(value: Any) -> str:
    value = _text(value, "path")
    if value not in {entry.filename for entry in CanonicalEvidenceRegistry().entries()}:
        raise EvidenceLineageSchemaError("path is not a registry-owned canonical evidence artifact")
    return value


def _timestamp(value: Any, key: str, *, allow_none: bool = False) -> str | None:
    value = _text(value, key, allow_none=allow_none)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceLineageSchemaError(f"{key} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EvidenceLineageSchemaError(f"{key} must be UTC")
    return parsed.isoformat()


def _registry_for_path(path: str):
    for entry in CanonicalEvidenceRegistry().entries():
        if entry.filename == path:
            return entry
    raise EvidenceLineageSchemaError("path is not a registry-owned canonical evidence artifact")


@dataclass(frozen=True)
class EvidenceIdentity:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _canonical_filename(self.path)
        _sha(self.sha256, "sha256")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceIdentity":
        _keys(value, {"path", "sha256"})
        return cls(_canonical_filename(value["path"]), _sha(value["sha256"], "sha256"))

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class EvidenceLineageInput:
    run_id: str
    snapshot_fingerprint: str | None
    source_kind: str
    source_identity: str
    evidence: EvidenceIdentity | None

    def __post_init__(self) -> None:
        _text(self.run_id, "runId")
        _sha(self.snapshot_fingerprint, "snapshotFingerprint", allow_none=True)
        if self.source_kind not in SOURCE_KINDS:
            raise EvidenceLineageSchemaError("source.kind is invalid")
        _text(self.source_identity, "source.identity")
        if self.evidence is not None and not isinstance(self.evidence, EvidenceIdentity):
            raise EvidenceLineageSchemaError("evidence is invalid")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceLineageInput":
        _keys(value, {"schemaVersion", "runId", "snapshotFingerprint", "source", "evidence"})
        if value["schemaVersion"] != EVIDENCE_LINEAGE_INPUT_SCHEMA:
            raise EvidenceLineageSchemaError("schemaVersion is invalid")
        run_id = _text(value["runId"], "runId")
        snapshot = _sha(value["snapshotFingerprint"], "snapshotFingerprint", allow_none=True)
        source = value["source"]
        _keys(source, {"kind", "identity"})
        kind = _text(source["kind"], "source.kind")
        if kind not in SOURCE_KINDS:
            raise EvidenceLineageSchemaError("source.kind is invalid")
        identity = _text(source["identity"], "source.identity")
        evidence = None if value["evidence"] is None else EvidenceIdentity.from_dict(value["evidence"])
        return cls(run_id, snapshot, kind, identity, evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": EVIDENCE_LINEAGE_INPUT_SCHEMA,
            "runId": self.run_id,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "source": {"kind": self.source_kind, "identity": self.source_identity},
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


@dataclass(frozen=True)
class EvidenceLineageDetail:
    path: str
    sha256: str
    artifact_kind: str
    schema_version: str
    writer: str
    status: str
    reason_code: str | None
    finalized_at: str | None
    fingerprint_matched: bool

    def __post_init__(self) -> None:
        entry = _registry_for_path(self.path)
        _sha(self.sha256, "sha256")
        if self.artifact_kind != entry.artifact_kind or self.schema_version != entry.schema_version or self.writer != entry.writer:
            raise EvidenceLineageSchemaError("registry metadata does not match path")
        if self.status not in STATUSES:
            raise EvidenceLineageSchemaError("status is invalid")
        if self.reason_code is not None and self.reason_code not in REASONS:
            raise EvidenceLineageSchemaError("reasonCode is invalid")
        _timestamp(self.finalized_at, "finalizedAt", allow_none=True)
        if not isinstance(self.fingerprint_matched, bool):
            raise EvidenceLineageSchemaError("fingerprintMatched must be boolean")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceLineageDetail":
        _keys(value, {"path", "sha256", "artifactKind", "schemaVersion", "writer", "status", "reasonCode", "finalizedAt", "fingerprintMatched"})
        status = _text(value["status"], "status")
        if status not in STATUSES:
            raise EvidenceLineageSchemaError("status is invalid")
        reason = _text(value["reasonCode"], "reasonCode", allow_none=True)
        if reason is not None and reason not in REASONS:
            raise EvidenceLineageSchemaError("reasonCode is invalid")
        if not isinstance(value["fingerprintMatched"], bool):
            raise EvidenceLineageSchemaError("fingerprintMatched must be boolean")
        return cls(
            _canonical_filename(value["path"]), _sha(value["sha256"], "sha256"),
            _text(value["artifactKind"], "artifactKind"), _text(value["schemaVersion"], "schemaVersion"),
            _text(value["writer"], "writer"), status, reason,
            _timestamp(value["finalizedAt"], "finalizedAt", allow_none=True), value["fingerprintMatched"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path, "sha256": self.sha256, "artifactKind": self.artifact_kind,
            "schemaVersion": self.schema_version, "writer": self.writer, "status": self.status,
            "reasonCode": self.reason_code, "finalizedAt": self.finalized_at,
            "fingerprintMatched": self.fingerprint_matched,
        }


@dataclass(frozen=True)
class EvidenceLineageLink:
    relation: str
    source_identity: str
    evidence_path: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if self.relation not in RELATIONS:
            raise EvidenceLineageSchemaError("relation is invalid")
        _text(self.source_identity, "sourceIdentity")
        _canonical_filename(self.evidence_path)
        _sha(self.evidence_sha256, "evidenceSha256")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceLineageLink":
        _keys(value, {"relation", "sourceIdentity", "evidencePath", "evidenceSha256"})
        relation = _text(value["relation"], "relation")
        if relation not in RELATIONS:
            raise EvidenceLineageSchemaError("relation is invalid")
        return cls(relation, _text(value["sourceIdentity"], "sourceIdentity"), _canonical_filename(value["evidencePath"]), _sha(value["evidenceSha256"], "evidenceSha256"))

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.relation, self.source_identity, self.evidence_path, self.evidence_sha256)

    def to_dict(self) -> dict[str, str]:
        return {
            "relation": self.relation, "sourceIdentity": self.source_identity,
            "evidencePath": self.evidence_path, "evidenceSha256": self.evidence_sha256,
        }


@dataclass(frozen=True)
class EvidenceLineageResult:
    status: str
    run_id: str
    snapshot_fingerprint: str | None
    source_kind: str
    source_identity: str
    evidence: tuple[EvidenceLineageDetail, ...]
    links: tuple[EvidenceLineageLink, ...]
    diagnostics: tuple[Mapping[str, str], ...]
    lineage_fingerprint: str | None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise EvidenceLineageSchemaError("status is invalid")
        _text(self.run_id, "runId")
        _sha(self.snapshot_fingerprint, "snapshotFingerprint", allow_none=True)
        if self.source_kind not in SOURCE_KINDS:
            raise EvidenceLineageSchemaError("source.kind is invalid")
        _text(self.source_identity, "source.identity")
        if len(self.evidence) > MAX_EVIDENCE_REFS or len(self.links) > MAX_EVIDENCE_REFS or len(self.diagnostics) > MAX_DIAGNOSTICS:
            raise EvidenceLineageSchemaError("lineage output is too large")
        if any(not isinstance(item, EvidenceLineageDetail) for item in self.evidence):
            raise EvidenceLineageSchemaError("evidence contains invalid detail")
        if any(not isinstance(item, EvidenceLineageLink) for item in self.links):
            raise EvidenceLineageSchemaError("links contains invalid link")
        if any(not isinstance(item, Mapping) or set(item) != {"code", "summary"} for item in self.diagnostics):
            raise EvidenceLineageSchemaError("diagnostics contains invalid entries")
        for item in self.diagnostics:
            _text(item["code"], "diagnostics.code")
            _text(item["summary"], "diagnostics.summary")
        if len(self.evidence) != len(self.links):
            raise EvidenceLineageSchemaError("links and evidence must have equal lengths")
        if tuple(item.sort_key() for item in self.links) != tuple(sorted(item.sort_key() for item in self.links)):
            raise EvidenceLineageSchemaError("links must be deterministically sorted")
        evidence_keys = tuple((item.path, item.sha256) for item in self.evidence)
        if evidence_keys != tuple(sorted(evidence_keys)):
            raise EvidenceLineageSchemaError("evidence must be deterministically sorted")
        if len({item.sort_key() for item in self.links}) != len(self.links):
            raise EvidenceLineageSchemaError("links must not contain duplicates")
        if any((detail.path, detail.sha256) != (link.evidence_path, link.evidence_sha256) for detail, link in zip(self.evidence, self.links)):
            raise EvidenceLineageSchemaError("evidence and links identity pairs must match")
        evidence_keys = [(item.path, item.sha256) for item in self.evidence]
        if len(set(evidence_keys)) != len(evidence_keys):
            raise EvidenceLineageSchemaError("evidence must not contain duplicates")
        if self.status in {"missing", "unknown", "invalid"} and self.evidence:
            raise EvidenceLineageSchemaError("unavailable results must not contain evidence details")
        if self.status == "available" and any(item.status != "available" or not item.fingerprint_matched for item in self.evidence):
            raise EvidenceLineageSchemaError("available results require available, matching evidence")
        if self.status == "fingerprint_mismatch" and any(item.fingerprint_matched for item in self.evidence):
            raise EvidenceLineageSchemaError("fingerprint_mismatch requires an unmatched evidence fingerprint")
        if self.status in {"blocked", "stale"} and any(item.status == "available" and item.fingerprint_matched for item in self.evidence):
            raise EvidenceLineageSchemaError(f"{self.status} results cannot claim available matching evidence")
        if self.lineage_fingerprint is not None and self.lineage_fingerprint != self._computed_fingerprint():
            raise EvidenceLineageSchemaError("lineageFingerprint does not match result")
        if self.status in {"unknown", "missing", "invalid"} and self.snapshot_fingerprint is not None:
            raise EvidenceLineageSchemaError("untrusted results must not have a snapshot fingerprint")
        _sha(self.lineage_fingerprint, "lineageFingerprint", allow_none=True)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceLineageResult":
        _keys(value, {"schemaVersion", "status", "lineagePolicyVersion", "runId", "snapshotFingerprint", "source", "evidence", "links", "diagnostics", "lineageFingerprint"})
        if value["schemaVersion"] != EVIDENCE_LINEAGE_SCHEMA or value["lineagePolicyVersion"] != LINEAGE_POLICY_VERSION:
            raise EvidenceLineageSchemaError("lineage schema or policy is invalid")
        status = _text(value["status"], "status")
        if status not in STATUSES:
            raise EvidenceLineageSchemaError("status is invalid")
        source = value["source"]
        _keys(source, {"kind", "identity"})
        kind = _text(source["kind"], "source.kind")
        identity = _text(source["identity"], "source.identity")
        if kind not in SOURCE_KINDS:
            raise EvidenceLineageSchemaError("source.kind is invalid")
        evidence_values = value["evidence"]
        links_values = value["links"]
        diagnostics_values = value["diagnostics"]
        if not isinstance(evidence_values, list) or len(evidence_values) > MAX_EVIDENCE_REFS:
            raise EvidenceLineageSchemaError("evidence is too large")
        if not isinstance(links_values, list) or len(links_values) > MAX_EVIDENCE_REFS:
            raise EvidenceLineageSchemaError("links is too large")
        if not isinstance(diagnostics_values, list) or len(diagnostics_values) > MAX_DIAGNOSTICS:
            raise EvidenceLineageSchemaError("diagnostics is too large")
        details = tuple(sorted((EvidenceLineageDetail.from_dict(item) for item in evidence_values), key=lambda item: (item.path, item.sha256)))
        links = tuple(sorted((EvidenceLineageLink.from_dict(item) for item in links_values), key=lambda item: item.sort_key()))
        if len(links) != len(details):
            raise EvidenceLineageSchemaError("links and evidence must have equal lengths")
        if len({item.sort_key() for item in links}) != len(links):
            raise EvidenceLineageSchemaError("links must not contain duplicates")
        diagnostics: list[Mapping[str, str]] = []
        for item in diagnostics_values:
            _keys(item, {"code", "summary"})
            diagnostics.append(MappingProxyType({"code": _text(item["code"], "diagnostics.code"), "summary": _text(item["summary"], "diagnostics.summary")}))
        snapshot = _sha(value["snapshotFingerprint"], "snapshotFingerprint", allow_none=True)
        lineage = _sha(value["lineageFingerprint"], "lineageFingerprint", allow_none=True)
        if status in {"unknown", "missing", "invalid"} or snapshot is None:
            if snapshot is not None:
                raise EvidenceLineageSchemaError("untrusted results must not have a snapshot fingerprint")
            if lineage is not None:
                raise EvidenceLineageSchemaError("untrusted results must not have a lineage fingerprint")
            lineage = None
        result = cls(status, _text(value["runId"], "runId"), snapshot, kind, identity, details, links, tuple(diagnostics), lineage)
        if lineage is not None and lineage != result._computed_fingerprint():
            raise EvidenceLineageSchemaError("lineageFingerprint does not match result")
        return result

    def _computed_fingerprint(self) -> str:
        return canonical_sha256(self._fingerprint_payload())

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": EVIDENCE_LINEAGE_SCHEMA,
            "lineagePolicyVersion": LINEAGE_POLICY_VERSION,
            "status": self.status,
            "runId": self.run_id,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "source": {"kind": self.source_kind, "identity": self.source_identity},
            "evidence": [item.to_dict() for item in self.evidence],
            "links": [item.to_dict() for item in self.links],
            "diagnostics": [dict(item) for item in self.diagnostics],
        }

    def with_fingerprint(self) -> "EvidenceLineageResult":
        if self.status in {"unknown", "missing", "invalid"} or self.snapshot_fingerprint is None:
            return self
        return EvidenceLineageResult(
            self.status, self.run_id, self.snapshot_fingerprint, self.source_kind,
            self.source_identity, self.evidence, self.links, self.diagnostics, self._computed_fingerprint(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": EVIDENCE_LINEAGE_SCHEMA,
            "status": self.status,
            "lineagePolicyVersion": LINEAGE_POLICY_VERSION,
            "runId": self.run_id,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "source": {"kind": self.source_kind, "identity": self.source_identity},
            "evidence": [item.to_dict() for item in self.evidence],
            "links": [item.to_dict() for item in self.links],
            "diagnostics": [dict(item) for item in self.diagnostics],
            "lineageFingerprint": self.lineage_fingerprint,
        }
