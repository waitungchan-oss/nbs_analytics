from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_hub_catalog import CatalogBuildPolicy, MemoryHubCatalogError, build_catalog
from backend.agents.memory_hub_deployment_provider import deployment_owned_catalog_provider
from backend.agents.memory_hub_models import MemoryRecord, MemorySource


def _fixture(root: Path) -> tuple[Path, dict]:
    source_root = root / "docs" / "memory_hub_sources"
    runtime_root = root / ".nbs_agent_runtime" / "memory-hub"
    manifest_root = root / "agent_config"
    artifact = source_root / "guide.md"
    artifact.parent.mkdir(parents=True)
    runtime_root.mkdir(parents=True)
    manifest_root.mkdir(parents=True)
    content = "deployment-owned governance source"
    artifact.write_text(content, encoding="utf-8")
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    source = MemorySource.from_parts(
        source_kind="governance_document", artifact_ref="guide.md",
        artifact_sha256=hashlib.sha256(content.encode()).hexdigest(), run_id=None,
        git_head=None, scope="project", owner="governance", status="verified",
        generated_at=now.isoformat(), expires_at=(now + timedelta(days=30)).isoformat(),
        policy_version="memory-hub-policy-v1",
    )
    record = MemoryRecord.from_parts(
        memory_kind="governance", summary="deployment-owned governance source",
        source_refs=(source,), scope="project", owner="governance",
        freshness="fresh", status="ready",
    )
    policy = CatalogBuildPolicy(
        source_root, runtime_root, (source,), (record,), "a" * 40, "b" * 64,
    )
    catalog = build_catalog(source_root, runtime_root / "catalog.json", policy)
    unsigned = {
        "schemaVersion": "memory-hub-deployment-provider-v1",
        "sourceRoot": "docs/memory_hub_sources", "runtimeRoot": ".nbs_agent_runtime/memory-hub",
        "catalogFile": "catalog.json", "builtFromHead": policy.built_from_head,
        "policyFingerprint": policy.policy_fingerprint,
        "sources": [source.to_dict()], "records": [record.to_dict()],
    }
    manifest = {**unsigned, "manifestFingerprint": canonical_fingerprint(unsigned)}
    manifest_path = manifest_root / "memory_hub_catalog_deployment.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path, {"catalog": catalog, "manifest": manifest}


def test_missing_manifest_is_missing_without_building(tmp_path: Path) -> None:
    provider = deployment_owned_catalog_provider(tmp_path)
    assert provider() is None
    assert not (tmp_path / ".nbs_agent_runtime").exists()


def test_valid_manifest_loads_immutable_catalog(tmp_path: Path) -> None:
    _, expected = _fixture(tmp_path)
    loaded = deployment_owned_catalog_provider(tmp_path)()
    assert loaded is not None
    assert loaded.to_dict() == expected["catalog"].to_dict()


def test_tampered_manifest_fails_closed(tmp_path: Path) -> None:
    manifest_path, _ = _fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["catalogFile"] = "../escape.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        deployment_owned_catalog_provider(tmp_path)()


def test_tampered_catalog_fails_closed(tmp_path: Path) -> None:
    _, _ = _fixture(tmp_path)
    catalog_path = tmp_path / ".nbs_agent_runtime" / "memory-hub" / "catalog.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    payload["catalogFingerprint"] = "f" * 64
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        deployment_owned_catalog_provider(tmp_path)()


def test_manifest_present_but_catalog_missing_is_missing(tmp_path: Path) -> None:
    _, _ = _fixture(tmp_path)
    (tmp_path / ".nbs_agent_runtime" / "memory-hub" / "catalog.json").unlink()
    assert deployment_owned_catalog_provider(tmp_path)() is None


def test_unknown_manifest_key_fails_closed(tmp_path: Path) -> None:
    manifest_path, _ = _fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        deployment_owned_catalog_provider(tmp_path)()


def test_source_hash_drift_fails_closed(tmp_path: Path) -> None:
    _, _ = _fixture(tmp_path)
    (tmp_path / "docs" / "memory_hub_sources" / "guide.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        deployment_owned_catalog_provider(tmp_path)()


def test_manifest_path_traversal_fails_closed(tmp_path: Path) -> None:
    manifest_path, _ = _fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["runtimeRoot"] = "../outside"
    payload["manifestFingerprint"] = canonical_fingerprint({key: payload[key] for key in payload if key != "manifestFingerprint"})
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemoryHubCatalogError):
        deployment_owned_catalog_provider(tmp_path)()


def test_source_root_symlink_fails_closed(tmp_path: Path) -> None:
    _, _ = _fixture(tmp_path)
    source_root = tmp_path / "docs" / "memory_hub_sources"
    outside = tmp_path / "outside"
    outside.mkdir()
    source_root.rename(tmp_path / "docs" / "memory_hub_sources_real")
    source_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(MemoryHubCatalogError):
        deployment_owned_catalog_provider(tmp_path)()
