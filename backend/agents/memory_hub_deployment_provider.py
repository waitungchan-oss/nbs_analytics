from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .evidence_models import canonical_fingerprint
from .memory_hub_catalog import (
    CatalogBuildPolicy,
    MemoryCatalog,
    MemoryHubCatalogError,
    load_catalog,
)
from .memory_hub_models import MemoryRecord, MemorySource, MemoryHubSchemaError


MANIFEST_SCHEMA = "memory-hub-deployment-provider-v1"
MANIFEST_RELATIVE = Path("agent_config/memory_hub_catalog_deployment.json")
SOURCE_ROOT_RELATIVE = Path("docs/memory_hub_sources")
RUNTIME_ROOT_RELATIVE = Path(".nbs_agent_runtime/memory-hub")
CATALOG_FILENAME = "catalog.json"
_MANIFEST_KEYS = {
    "schemaVersion", "sourceRoot", "runtimeRoot", "catalogFile", "builtFromHead",
    "policyFingerprint", "sources", "records", "manifestFingerprint",
}


def _fixed_path(project_root: Path, relative: Path, key: str) -> Path:
    if not isinstance(project_root, Path) or project_root.is_symlink():
        raise MemoryHubCatalogError("project root must be a regular directory")
    root = project_root.resolve(strict=False)
    candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise MemoryHubCatalogError(f"{key} contains a symlink")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise MemoryHubCatalogError(f"{key} escapes project root") from exc
    return candidate


def _read_manifest(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise MemoryHubCatalogError("deployment manifest must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MemoryHubCatalogError("deployment manifest is unreadable") from exc
    if not isinstance(payload, Mapping) or set(payload) != _MANIFEST_KEYS:
        raise MemoryHubCatalogError("deployment manifest keys are invalid")
    if payload["schemaVersion"] != MANIFEST_SCHEMA:
        raise MemoryHubCatalogError("deployment manifest schema is invalid")
    if payload["sourceRoot"] != SOURCE_ROOT_RELATIVE.as_posix() or payload["runtimeRoot"] != RUNTIME_ROOT_RELATIVE.as_posix() or payload["catalogFile"] != CATALOG_FILENAME:
        raise MemoryHubCatalogError("deployment manifest paths are not the fixed allowlist")
    unsigned = {key: payload[key] for key in _MANIFEST_KEYS if key != "manifestFingerprint"}
    if payload["manifestFingerprint"] != canonical_fingerprint(unsigned):
        raise MemoryHubCatalogError("deployment manifest fingerprint mismatch")
    return payload


def _load_deployment_catalog(project_root: Path) -> MemoryCatalog | None:
    manifest_path = _fixed_path(project_root, MANIFEST_RELATIVE, "manifest path")
    payload = _read_manifest(manifest_path)
    if payload is None:
        return None
    source_root = _fixed_path(project_root, SOURCE_ROOT_RELATIVE, "source root")
    runtime_root = _fixed_path(project_root, RUNTIME_ROOT_RELATIVE, "runtime root")
    catalog_path = runtime_root / CATALOG_FILENAME
    if not catalog_path.exists():
        return None
    try:
        sources = tuple(MemorySource.from_dict(item) for item in payload["sources"])
        source_index = {source.source_id: source for source in sources}
        records = tuple(MemoryRecord.from_dict(item, source_index) for item in payload["records"])
        policy = CatalogBuildPolicy(
            source_root=source_root,
            output_root=runtime_root,
            sources=sources,
            records=records,
            built_from_head=payload["builtFromHead"],
            policy_fingerprint=payload["policyFingerprint"],
        )
        return load_catalog(catalog_path, runtime_root, policy)
    except (MemoryHubSchemaError, MemoryHubCatalogError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, MemoryHubCatalogError):
            raise
        raise MemoryHubCatalogError("deployment catalog contract is invalid") from exc


def deployment_owned_catalog_provider(project_root: Path):
    """Return a zero-argument, read-only provider for the fixed deployment catalog."""
    root = Path(project_root)

    def provider() -> MemoryCatalog | None:
        return _load_deployment_catalog(root)

    return provider
