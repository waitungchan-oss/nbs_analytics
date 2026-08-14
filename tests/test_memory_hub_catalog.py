from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.memory_hub_catalog import (
    CatalogBuildPolicy,
    MemoryCatalog,
    MemoryHubCatalogError,
    build_catalog,
    load_catalog,
)
from backend.agents.memory_hub_models import MemoryRecord, MemorySource


def _write_source(root: Path, relative: str = "docs/spec.md", content: str = "canonical spec") -> MemorySource:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    import hashlib

    kind = "governance_document"
    return MemorySource.from_parts(
        source_kind=kind,
        artifact_ref=relative,
        artifact_sha256=hashlib.sha256(content.encode()).hexdigest(),
        run_id=None,
        git_head=None,
        scope="project",
        owner="governance",
        status="verified",
        generated_at="2026-08-14T00:00:00+00:00",
        expires_at="2026-11-12T00:00:00+00:00",
        policy_version="memory-freshness-v1",
    )


def _policy(root: Path, output: Path, source: MemorySource) -> CatalogBuildPolicy:
    record = MemoryRecord.from_parts(
        memory_kind="governance", summary="canonical spec", source_refs=(source,),
        scope="project", owner="governance", freshness="fresh", status="ready",
    )
    return CatalogBuildPolicy(
        source_root=root, output_root=output.parent, sources=(source,), records=(record,),
        built_from_head="a" * 40, policy_fingerprint="b" * 64,
    )


def test_catalog_rebuild_is_deterministic_and_round_trips(tmp_path: Path):
    source_root = tmp_path / "sources"
    output = tmp_path / "catalog" / "memory.json"
    source = _write_source(source_root)
    policy = _policy(source_root, output, source)
    first = build_catalog(source_root, output, policy)
    second = build_catalog(source_root, output, policy)
    assert first.to_dict() == second.to_dict()
    assert first.catalog_fingerprint == second.catalog_fingerprint
    assert load_catalog(output, output.parent, policy).to_dict() == first.to_dict()


def test_catalog_rejects_source_hash_drift_and_missing_source(tmp_path: Path):
    source_root = tmp_path / "sources"
    output = tmp_path / "catalog" / "memory.json"
    source = _write_source(source_root)
    policy = _policy(source_root, output, source)
    (source_root / "docs/spec.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        build_catalog(source_root, output, policy)
    (source_root / "docs/spec.md").unlink()
    with pytest.raises(MemoryHubCatalogError):
        build_catalog(source_root, output, policy)


def test_catalog_rejects_symlink_traversal_and_output_escape(tmp_path: Path):
    source_root = tmp_path / "sources"
    output = tmp_path / "catalog" / "memory.json"
    source = _write_source(source_root)
    policy = _policy(source_root, output, source)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (source_root / "link.md").symlink_to(outside)
    linked = MemorySource.from_parts(
        source_kind="governance_document", artifact_ref="link.md",
        artifact_sha256="a" * 64, run_id=None, git_head=None, scope="project",
        owner="governance", status="verified", generated_at="2026-08-14T00:00:00+00:00",
        expires_at="2026-11-12T00:00:00+00:00", policy_version="memory-freshness-v1",
    )
    with pytest.raises(MemoryHubCatalogError):
        build_catalog(source_root, output, _policy(source_root, output, linked))
    with pytest.raises(MemoryHubCatalogError):
        load_catalog(output, tmp_path / "other-root", policy)


def test_catalog_rejects_tampering_and_conflicting_existing_output(tmp_path: Path):
    source_root = tmp_path / "sources"
    output = tmp_path / "catalog" / "memory.json"
    source = _write_source(source_root)
    policy = _policy(source_root, output, source)
    built = build_catalog(source_root, output, policy)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["catalogFingerprint"] = "f" * 64
    output.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        load_catalog(output, output.parent, policy)
    output.write_text("different", encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        build_catalog(source_root, output, policy)


def test_catalog_rejects_output_root_equal_to_or_nested_in_source_root(tmp_path: Path):
    source_root = tmp_path / "sources"
    source = _write_source(source_root)
    for output_root in (source_root, source_root / "derived"):
        output = output_root / "memory.json"
        with pytest.raises(MemoryHubCatalogError):
            build_catalog(source_root, output, _policy(source_root, output, source))


def test_catalog_rejects_in_root_symlink_output_and_loader_alias(tmp_path: Path):
    source_root = tmp_path / "sources"
    output = tmp_path / "catalog" / "memory.json"
    source = _write_source(source_root)
    policy = _policy(source_root, output, source)
    build_catalog(source_root, output, policy)
    output_alias = output.parent / "alias.json"
    output_alias.symlink_to(output)
    with pytest.raises(MemoryHubCatalogError):
        build_catalog(source_root, output_alias, policy)
    with pytest.raises(MemoryHubCatalogError):
        load_catalog(output_alias, output.parent, policy)


def test_catalog_is_immutable_value_object():
    assert issubclass(MemoryCatalog, object)
