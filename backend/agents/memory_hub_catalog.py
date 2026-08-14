from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .evidence_models import canonical_fingerprint
from .memory_hub_models import MemoryRecord, MemorySource, MemoryHubSchemaError


CATALOG_SCHEMA = "memory-catalog-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")


class MemoryHubCatalogError(ValueError):
    """Raised when a catalog source, output or immutable envelope is unsafe."""


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise MemoryHubCatalogError(f"{key} must be a lowercase SHA-256")
    return value


def _safe_root(path: Path, key: str) -> Path:
    if not isinstance(path, Path) or path.exists() and path.is_symlink():
        raise MemoryHubCatalogError(f"{key} must not be a symlink")
    resolved = path.resolve(strict=False)
    if path.exists() and not path.is_dir():
        raise MemoryHubCatalogError(f"{key} must be a directory")
    return resolved


def _relative_regular(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith("/") or "\\" in relative or ".." in relative.split("/"):
        raise MemoryHubCatalogError("artifactRef is unsafe")
    candidate = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise MemoryHubCatalogError("artifactRef contains a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise MemoryHubCatalogError("artifactRef escapes or is missing") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise MemoryHubCatalogError("artifactRef must be a regular file")
    return resolved


def _write_immutable(path: Path, payload: str, output_root: Path) -> None:
    output_root = _safe_root(output_root, "outputRoot")
    raw_path = path.absolute()
    if raw_path.is_symlink():
        raise MemoryHubCatalogError("catalog output must not be a symlink")
    try:
        raw_relative = raw_path.relative_to(output_root)
    except ValueError as exc:
        raise MemoryHubCatalogError("catalog output escapes outputRoot") from exc
    current = output_root
    for part in raw_relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise MemoryHubCatalogError("catalog output contains a symlink")
    candidate = raw_path.resolve(strict=False)
    try:
        candidate.relative_to(output_root)
    except ValueError as exc:
        raise MemoryHubCatalogError("catalog output escapes outputRoot") from exc
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() or not candidate.is_file() or candidate.read_text(encoding="utf-8") != payload:
            raise MemoryHubCatalogError("existing catalog output is not identical")
        return
    candidate.write_text(payload, encoding="utf-8")


@dataclass(frozen=True)
class CatalogBuildPolicy:
    source_root: Path
    output_root: Path
    sources: tuple[MemorySource, ...]
    records: tuple[MemoryRecord, ...]
    built_from_head: str
    policy_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.sources, tuple) or not isinstance(self.records, tuple):
            raise MemoryHubCatalogError("sources and records must be immutable tuples")
        if not _HEAD.fullmatch(self.built_from_head) or not _SHA.fullmatch(self.policy_fingerprint):
            raise MemoryHubCatalogError("catalog policy identity is invalid")
        source_root = Path(self.source_root).resolve(strict=False)
        output_root = Path(self.output_root).resolve(strict=False)
        try:
            output_root.relative_to(source_root)
        except ValueError:
            pass
        else:
            raise MemoryHubCatalogError("outputRoot must be independent from sourceRoot")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise MemoryHubCatalogError("source identities must be unique")
        source_ids = {source.source_id for source in self.sources}
        if any(ref.source_id not in source_ids for record in self.records for ref in record.source_refs):
            raise MemoryHubCatalogError("record references a source outside the policy")


@dataclass(frozen=True)
class MemoryCatalog:
    built_from_head: str
    source_set_fingerprint: str
    policy_fingerprint: str
    sources: tuple[MemorySource, ...]
    records: tuple[MemoryRecord, ...]
    catalog_fingerprint: str

    def __post_init__(self) -> None:
        _HEAD.fullmatch(self.built_from_head) or (_ for _ in ()).throw(MemoryHubCatalogError("builtFromHead is invalid"))
        _sha(self.source_set_fingerprint, "sourceSetFingerprint")
        _sha(self.policy_fingerprint, "policyFingerprint")
        _sha(self.catalog_fingerprint, "catalogFingerprint")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise MemoryHubCatalogError("catalog source identities must be unique")
        if len({record.memory_id for record in self.records}) != len(self.records):
            raise MemoryHubCatalogError("catalog memory identities must be unique")
        unsigned = self._unsigned()
        if self.catalog_fingerprint != canonical_fingerprint(unsigned):
            raise MemoryHubCatalogError("catalogFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": CATALOG_SCHEMA,
            "builtFromHead": self.built_from_head,
            "sourceSetFingerprint": self.source_set_fingerprint,
            "policyFingerprint": self.policy_fingerprint,
            "sources": [source.to_dict() for source in self.sources],
            "records": [record.to_dict() for record in self.records],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "catalogFingerprint": self.catalog_fingerprint}

    def source_index(self) -> dict[str, MemorySource]:
        return {source.source_id: source for source in self.sources}

    def record_by_id(self, memory_id: str) -> MemoryRecord | None:
        return next((record for record in self.records if record.memory_id == memory_id), None)


def _build(source_root: Path, policy: CatalogBuildPolicy) -> MemoryCatalog:
    root = _safe_root(source_root, "sourceRoot")
    if root != _safe_root(policy.source_root, "policy.sourceRoot"):
        raise MemoryHubCatalogError("sourceRoot does not match policy")
    checked_sources: list[MemorySource] = []
    for source in sorted(policy.sources, key=lambda item: item.source_id):
        path = _relative_regular(root, source.artifact_ref)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.artifact_sha256:
            raise MemoryHubCatalogError(f"artifact hash mismatch for {source.artifact_ref}")
        checked_sources.append(source)
    source_set = canonical_fingerprint([source.to_dict() for source in checked_sources])
    records = tuple(sorted(policy.records, key=lambda item: item.memory_id))
    unsigned = {
        "schemaVersion": CATALOG_SCHEMA,
        "builtFromHead": policy.built_from_head,
        "sourceSetFingerprint": source_set,
        "policyFingerprint": policy.policy_fingerprint,
        "sources": [source.to_dict() for source in checked_sources],
        "records": [record.to_dict() for record in records],
    }
    return MemoryCatalog(policy.built_from_head, source_set, policy.policy_fingerprint, tuple(checked_sources), records, canonical_fingerprint(unsigned))


def build_catalog(source_root: Path, output_path: Path, policy: CatalogBuildPolicy) -> MemoryCatalog:
    catalog = _build(source_root, policy)
    payload = json.dumps(catalog.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    _write_immutable(output_path, payload, policy.output_root)
    return catalog


def load_catalog(catalog_path: Path, runtime_root: Path, policy: CatalogBuildPolicy) -> MemoryCatalog:
    root = _safe_root(runtime_root, "runtimeRoot")
    raw_path = catalog_path.absolute()
    if raw_path.is_symlink():
        raise MemoryHubCatalogError("catalog path must not be a symlink")
    try:
        raw_relative = raw_path.relative_to(root)
    except ValueError as exc:
        raise MemoryHubCatalogError("catalog path escapes runtimeRoot") from exc
    current = root
    for part in raw_relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise MemoryHubCatalogError("catalog path contains a symlink")
    candidate = raw_path.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MemoryHubCatalogError("catalog path escapes runtimeRoot") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise MemoryHubCatalogError("catalog path must be a regular file")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MemoryHubCatalogError("catalog payload is unreadable") from exc
    expected = {"schemaVersion", "builtFromHead", "sourceSetFingerprint", "policyFingerprint", "sources", "records", "catalogFingerprint"}
    if not isinstance(payload, Mapping) or set(payload) != expected or payload["schemaVersion"] != CATALOG_SCHEMA:
        raise MemoryHubCatalogError("catalog envelope is invalid")
    if payload["policyFingerprint"] != policy.policy_fingerprint or payload["builtFromHead"] != policy.built_from_head:
        raise MemoryHubCatalogError("catalog policy identity mismatch")
    try:
        sources = tuple(MemorySource.from_dict(item) for item in payload["sources"])
        source_index = {source.source_id: source for source in sources}
        records = tuple(MemoryRecord.from_dict(item, source_index) for item in payload["records"])
        catalog = MemoryCatalog(payload["builtFromHead"], payload["sourceSetFingerprint"], payload["policyFingerprint"], sources, records, payload["catalogFingerprint"])
    except (MemoryHubSchemaError, KeyError, TypeError, ValueError) as exc:
        raise MemoryHubCatalogError("catalog contract is invalid") from exc
    expected_catalog = _build(policy.source_root, policy)
    if catalog.to_dict() != expected_catalog.to_dict():
        raise MemoryHubCatalogError("catalog does not match current source set")
    return catalog
