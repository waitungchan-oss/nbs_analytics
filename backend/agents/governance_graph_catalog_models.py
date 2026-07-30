from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .workflow_models import canonical_sha256

OWNER_CATALOG_SCHEMA = "governance-graph-owner-catalog-v1"
DEPENDENCY_CATALOG_SCHEMA = "governance-graph-dependency-catalog-v1"
OWNER_POLICY_VERSION = "e3-owner-policy-v1"
DEPENDENCY_POLICY_VERSION = "e3-dependency-policy-v1"
READ_MODEL_SCHEMA = "governance-graph-owner-dependency-read-v1"
SOURCE_KINDS = frozenset({"approved_catalog", "graph_contract", "canonical_evidence"})
OWNER_ROLES = frozenset(
    {
        "spec_owner",
        "plan_owner",
        "implementation_owner",
        "review_owner",
        "verification_owner",
        "hermes_owner",
        "documentation_owner",
    }
)
RELATIONS = frozenset(
    {
        "requires",
        "produces",
        "implements",
        "reviews",
        "verifies",
        "blocks",
        "derived_from",
        "committed_as",
        "documented_by",
    }
)
RELATION_KINDS = frozenset({"workflow_edge", "declared_dependency"})
CATALOG_STATUSES = frozenset({"available", "unavailable", "missing", "unknown", "blocked", "stale", "invalid"})
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_TEXT_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9_-]{6,})", re.IGNORECASE)
_FORBIDDEN_KEYS = frozenset({"absolutePath", "prompt", "command", "stdout", "stderr", "secret"})


class GovernanceGraphCatalogSchemaError(ValueError):
    """Raised when an owner or dependency catalog violates its public contract."""


def _require_keys(payload: Any, expected: frozenset[str], key: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise GovernanceGraphCatalogSchemaError(f"{key} keys are invalid")
    return payload


def _identifier(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise GovernanceGraphCatalogSchemaError(f"{key} must be a bounded safe identifier")
    if value.startswith(".") or ".." in value or "/" in value or "\\" in value:
        raise GovernanceGraphCatalogSchemaError(f"{key} must be a bounded safe identifier")
    if _FORBIDDEN_TEXT_RE.search(value) or any(token in value.lower() for token in ("prompt", "command", "stdout", "stderr", "secret")):
        raise GovernanceGraphCatalogSchemaError(f"{key} contains forbidden text")
    return value


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise GovernanceGraphCatalogSchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _status(value: Any, key: str) -> str:
    if not isinstance(value, str) or value not in CATALOG_STATUSES:
        raise GovernanceGraphCatalogSchemaError(f"{key} is invalid")
    return value


def _source(value: Any, key: str) -> MappingProxyType:
    source = _require_keys(value, frozenset({"kind", "identity", "fingerprint"}), key)
    kind = source["kind"]
    if not isinstance(kind, str) or kind not in SOURCE_KINDS:
        raise GovernanceGraphCatalogSchemaError(f"{key}.kind is not allowlisted")
    identity = source["identity"]
    if not isinstance(identity, str) or not identity or len(identity) > 128:
        raise GovernanceGraphCatalogSchemaError(f"{key}.identity is invalid")
    if identity.startswith(("/", "~")) or "/" in identity or "\\" in identity or "://" in identity:
        raise GovernanceGraphCatalogSchemaError(f"{key}.identity must not be a path or URI")
    if any(ord(char) < 32 for char in identity) or _FORBIDDEN_TEXT_RE.search(identity):
        raise GovernanceGraphCatalogSchemaError(f"{key}.identity contains unsafe text")
    if any(token in identity.lower() for token in ("prompt", "command", "stdout", "stderr", "secret")):
        raise GovernanceGraphCatalogSchemaError(f"{key}.identity contains forbidden text")
    return MappingProxyType({"kind": kind, "identity": identity, "fingerprint": _sha(source["fingerprint"], f"{key}.fingerprint")})


def _snapshot(value: Any, key: str) -> str:
    return _sha(value, key)


def _subject(value: Any, key: str) -> MappingProxyType:
    subject = _require_keys(value, frozenset({"kind", "id"}), key)
    return MappingProxyType({"kind": _identifier(subject["kind"], f"{key}.kind"), "id": _identifier(subject["id"], f"{key}.id")})


def _entry_source_and_status(payload: Mapping[str, Any], key: str) -> tuple[MappingProxyType, str, str]:
    source = _source(payload["source"], f"{key}.source")
    snapshot = _snapshot(payload["snapshotFingerprint"], f"{key}.snapshotFingerprint")
    status = _status(payload["status"], f"{key}.status")
    return source, snapshot, status


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _dedupe_entries(entries: list[MappingProxyType], identity_fn, key: str) -> tuple[MappingProxyType, ...]:
    by_identity: dict[Any, MappingProxyType] = {}
    for entry in entries:
        identity = identity_fn(entry)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = entry
        elif existing != entry:
            raise GovernanceGraphCatalogSchemaError(f"{key} contains a conflicting duplicate")
    return tuple(by_identity[identity] for identity in sorted(by_identity, key=lambda item: repr(item)))


def _catalog_fingerprint(payload: Mapping[str, Any], fingerprint_key: str) -> str:
    supplied = _sha(payload[fingerprint_key], fingerprint_key)
    unsigned = {key: value for key, value in payload.items() if key != fingerprint_key}
    expected = canonical_sha256(unsigned)
    if supplied != expected:
        raise GovernanceGraphCatalogSchemaError(f"{fingerprint_key} does not match canonical envelope")
    return supplied


OWNER_CATALOG_KEYS = frozenset({"schemaVersion", "catalogPolicyVersion", "catalogFingerprint", "snapshotFingerprint", "source", "entries", "diagnostics"})
OWNER_ENTRY_KEYS = frozenset({"subject", "owner", "source", "snapshotFingerprint", "status"})
DEPENDENCY_CATALOG_KEYS = frozenset({"schemaVersion", "catalogPolicyVersion", "catalogFingerprint", "snapshotFingerprint", "source", "entries", "diagnostics"})
DEPENDENCY_ENTRY_KEYS = frozenset({"from", "to", "relation", "relationKind", "source", "snapshotFingerprint", "status"})


@dataclass(frozen=True)
class GovernanceGraphOwnerCatalog:
    catalog_fingerprint: str
    snapshot_fingerprint: str
    source: Mapping[str, str]
    entries: tuple[Mapping[str, Any], ...]
    status: str
    diagnostics: tuple[Mapping[str, str], ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphOwnerCatalog":
        data = _require_keys(payload, OWNER_CATALOG_KEYS, "owner catalog")
        if data["schemaVersion"] != OWNER_CATALOG_SCHEMA or data["catalogPolicyVersion"] != OWNER_POLICY_VERSION:
            raise GovernanceGraphCatalogSchemaError("owner catalog schema or policy version is invalid")
        source = _source(data["source"], "source")
        catalog_fingerprint = _catalog_fingerprint(data, "catalogFingerprint")
        snapshot_fingerprint = _snapshot(data["snapshotFingerprint"], "snapshotFingerprint")
        entries_raw = data["entries"]
        if not isinstance(entries_raw, list) or len(entries_raw) > 128:
            raise GovernanceGraphCatalogSchemaError("owner catalog entries are invalid")
        entries: list[MappingProxyType] = []
        for index, raw in enumerate(entries_raw):
            entry = _require_keys(raw, OWNER_ENTRY_KEYS, f"entries[{index}]")
            subject = _subject(entry["subject"], f"entries[{index}].subject")
            owner = _require_keys(entry["owner"], frozenset({"kind", "id"}), f"entries[{index}].owner")
            if not isinstance(owner["kind"], str) or not isinstance(owner["id"], str):
                raise GovernanceGraphCatalogSchemaError(f"entries[{index}].owner is malformed")
            owner_kind = _identifier(owner["kind"], f"entries[{index}].owner.kind")
            owner_id = _identifier(owner["id"], f"entries[{index}].owner.id")
            if owner_kind != "governance_role" or owner_id not in OWNER_ROLES:
                raise GovernanceGraphCatalogSchemaError(f"entries[{index}].owner is not an allowlisted governance role")
            entry_source, entry_snapshot, entry_status = _entry_source_and_status(entry, f"entries[{index}]")
            if entry_snapshot != snapshot_fingerprint:
                raise GovernanceGraphCatalogSchemaError(f"entries[{index}].snapshotFingerprint is stale")
            entries.append(MappingProxyType({"subject": subject, "owner": MappingProxyType({"kind": owner_kind, "id": owner_id}), "source": entry_source, "snapshotFingerprint": entry_snapshot, "status": entry_status}))
        normalized = _dedupe_entries(entries, lambda item: (item["subject"]["kind"], item["subject"]["id"]), "owner entries")
        if normalized and any(entry["source"] != source for entry in normalized):
            raise GovernanceGraphCatalogSchemaError("owner entry source does not match catalog source")
        diagnostics = _diagnostics(data["diagnostics"], "diagnostics")
        return cls(catalog_fingerprint, snapshot_fingerprint, source, normalized, "available", diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_CATALOG_SCHEMA,
            "catalogPolicyVersion": OWNER_POLICY_VERSION,
            "catalogFingerprint": self.catalog_fingerprint,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "source": _thaw(self.source),
            "entries": [_thaw(entry) for entry in self.entries],
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }


@dataclass(frozen=True)
class GovernanceGraphDependencyCatalog:
    catalog_fingerprint: str
    snapshot_fingerprint: str
    source: Mapping[str, str]
    entries: tuple[Mapping[str, Any], ...]
    status: str
    diagnostics: tuple[Mapping[str, str], ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphDependencyCatalog":
        data = _require_keys(payload, DEPENDENCY_CATALOG_KEYS, "dependency catalog")
        if data["schemaVersion"] != DEPENDENCY_CATALOG_SCHEMA or data["catalogPolicyVersion"] != DEPENDENCY_POLICY_VERSION:
            raise GovernanceGraphCatalogSchemaError("dependency catalog schema or policy version is invalid")
        source = _source(data["source"], "source")
        catalog_fingerprint = _catalog_fingerprint(data, "catalogFingerprint")
        snapshot_fingerprint = _snapshot(data["snapshotFingerprint"], "snapshotFingerprint")
        entries_raw = data["entries"]
        if not isinstance(entries_raw, list) or len(entries_raw) > 128:
            raise GovernanceGraphCatalogSchemaError("dependency catalog entries are invalid")
        entries: list[MappingProxyType] = []
        for index, raw in enumerate(entries_raw):
            entry = _require_keys(raw, DEPENDENCY_ENTRY_KEYS, f"entries[{index}]")
            source_value, entry_snapshot, entry_status = _entry_source_and_status(entry, f"entries[{index}]")
            from_value = _subject(entry["from"], f"entries[{index}].from")
            to_value = _subject(entry["to"], f"entries[{index}].to")
            if from_value == to_value:
                raise GovernanceGraphCatalogSchemaError("dependency entries must not contain self-loops")
            relation = _identifier(entry["relation"], f"entries[{index}].relation")
            relation_kind = _identifier(entry["relationKind"], f"entries[{index}].relationKind")
            if relation not in RELATIONS or relation_kind not in RELATION_KINDS:
                raise GovernanceGraphCatalogSchemaError("dependency relation is not allowlisted")
            if entry_snapshot != snapshot_fingerprint:
                raise GovernanceGraphCatalogSchemaError(f"entries[{index}].snapshotFingerprint is stale")
            entries.append(MappingProxyType({"from": from_value, "to": to_value, "relation": relation, "relationKind": relation_kind, "source": source_value, "snapshotFingerprint": entry_snapshot, "status": entry_status}))
        normalized = _dedupe_entries(entries, lambda item: (item["from"]["kind"], item["from"]["id"], item["to"]["kind"], item["to"]["id"], item["relation"], item["relationKind"]), "dependency entries")
        if normalized and any(entry["source"] != source for entry in normalized):
            raise GovernanceGraphCatalogSchemaError("dependency entry source does not match catalog source")
        diagnostics = _diagnostics(data["diagnostics"], "diagnostics")
        return cls(catalog_fingerprint, snapshot_fingerprint, source, normalized, "available", diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": DEPENDENCY_CATALOG_SCHEMA,
            "catalogPolicyVersion": DEPENDENCY_POLICY_VERSION,
            "catalogFingerprint": self.catalog_fingerprint,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "source": _thaw(self.source),
            "entries": [_thaw(entry) for entry in self.entries],
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }


def _diagnostics(value: Any, key: str) -> tuple[MappingProxyType, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise GovernanceGraphCatalogSchemaError(f"{key} must be a bounded list")
    result = []
    for index, item in enumerate(value):
        data = _require_keys(item, frozenset({"code", "summary"}), f"{key}[{index}]")
        result.append(MappingProxyType({"code": _identifier(data["code"], f"{key}[{index}].code"), "summary": _public_text(data["summary"], f"{key}[{index}].summary")}))
    return tuple(result)


def _public_text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise GovernanceGraphCatalogSchemaError(f"{key} must be bounded public text")
    if value.startswith(("/", "~")) or "\\" in value or "://" in value or ".." in value:
        raise GovernanceGraphCatalogSchemaError(f"{key} must not expose a path or URI")
    if _FORBIDDEN_TEXT_RE.search(value) or any(token in value.lower() for token in ("prompt", "command", "stdout", "stderr", "secret")):
        raise GovernanceGraphCatalogSchemaError(f"{key} contains forbidden text")
    return value


READ_MODEL_STATUSES = frozenset({"available", "unavailable", "missing", "unknown", "blocked", "stale", "invalid"})
READ_MODEL_COVERAGE_KEYS = frozenset({"ownerStatus", "dependencyStatus", "ownerEntries", "dependencyEntries", "unknownCount", "missingCount", "staleCount", "blockedCount"})


@dataclass(frozen=True)
class GovernanceGraphOwnerDependencyReadModel:
    status: str
    snapshot_fingerprint: str
    owner_catalog_fingerprint: str | None
    dependency_catalog_fingerprint: str | None
    owners: tuple[Mapping[str, Any], ...]
    dependencies: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, str], ...]

    @classmethod
    def from_parts(cls, *, status: str, snapshot_fingerprint: str, owner_catalog_fingerprint: str | None, dependency_catalog_fingerprint: str | None, owners: Any, dependencies: Any, coverage: Mapping[str, Any], diagnostics: Any) -> "GovernanceGraphOwnerDependencyReadModel":
        if status not in READ_MODEL_STATUSES:
            raise GovernanceGraphCatalogSchemaError("read model status is invalid")
        snapshot = _sha(snapshot_fingerprint, "snapshotFingerprint")
        owner_fp = None if owner_catalog_fingerprint is None else _sha(owner_catalog_fingerprint, "ownerCatalogFingerprint")
        dependency_fp = None if dependency_catalog_fingerprint is None else _sha(dependency_catalog_fingerprint, "dependencyCatalogFingerprint")
        if not isinstance(owners, (list, tuple)) or not isinstance(dependencies, (list, tuple)) or len(owners) > 128 or len(dependencies) > 128:
            raise GovernanceGraphCatalogSchemaError("read model entries are invalid")
        if not isinstance(coverage, Mapping) or set(coverage) != READ_MODEL_COVERAGE_KEYS:
            raise GovernanceGraphCatalogSchemaError("read model coverage keys are invalid")
        for key in ("ownerStatus", "dependencyStatus"):
            _status(coverage[key], f"coverage.{key}")
        for key in ("ownerEntries", "dependencyEntries", "unknownCount", "missingCount", "staleCount", "blockedCount"):
            if isinstance(coverage[key], bool) or not isinstance(coverage[key], int) or coverage[key] < 0:
                raise GovernanceGraphCatalogSchemaError(f"coverage.{key} is invalid")
        if not isinstance(diagnostics, (list, tuple)) or len(diagnostics) > 32:
            raise GovernanceGraphCatalogSchemaError("read model diagnostics are invalid")
        normalized_diagnostics = _diagnostics(list(diagnostics), "diagnostics")
        normalized_owners = tuple(dict(item) for item in owners if isinstance(item, Mapping))
        normalized_dependencies = tuple(dict(item) for item in dependencies if isinstance(item, Mapping))
        if len(normalized_owners) != len(owners) or len(normalized_dependencies) != len(dependencies):
            raise GovernanceGraphCatalogSchemaError("read model entries must be objects")
        return cls(status, snapshot, owner_fp, dependency_fp, normalized_owners, normalized_dependencies, dict(coverage), normalized_diagnostics)

    @property
    def read_model_fingerprint(self) -> str | None:
        if self.status in {"invalid", "unavailable"}:
            return None
        payload = {
            "schemaVersion": READ_MODEL_SCHEMA,
            "status": self.status,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "ownerCatalogFingerprint": self.owner_catalog_fingerprint,
            "dependencyCatalogFingerprint": self.dependency_catalog_fingerprint,
            "readModelFingerprint": None,
            "owners": [_thaw(item) for item in self.owners],
            "dependencies": [_thaw(item) for item in self.dependencies],
            "coverage": _thaw(self.coverage),
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }
        return canonical_sha256(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": READ_MODEL_SCHEMA,
            "status": self.status,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "ownerCatalogFingerprint": self.owner_catalog_fingerprint,
            "dependencyCatalogFingerprint": self.dependency_catalog_fingerprint,
            "readModelFingerprint": self.read_model_fingerprint,
            "owners": [_thaw(item) for item in self.owners],
            "dependencies": [_thaw(item) for item in self.dependencies],
            "coverage": _thaw(self.coverage),
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }


__all__ = [
    "CATALOG_STATUSES",
    "DEPENDENCY_CATALOG_SCHEMA",
    "GovernanceGraphCatalogSchemaError",
    "GovernanceGraphDependencyCatalog",
    "GovernanceGraphOwnerDependencyReadModel",
    "GovernanceGraphOwnerCatalog",
    "OWNER_CATALOG_SCHEMA",
    "OWNER_ROLES",
]
