from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.agents.memory_hub_catalog import CatalogBuildPolicy, build_catalog
from backend.agents.memory_hub_models import MemoryRecord, MemorySource
from backend.agents.memory_hub_ui_service import MemoryHubUiService, _record_rows


def _catalog(tmp_path: Path):
    source_root = tmp_path / "sources"
    output_root = tmp_path / "catalog"
    artifact = source_root / "docs" / "guide.md"
    artifact.parent.mkdir(parents=True)
    content = "canonical governance guide"
    artifact.write_text(content, encoding="utf-8")
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    source = MemorySource.from_parts(
        source_kind="governance_document", artifact_ref="docs/guide.md",
        artifact_sha256=hashlib.sha256(content.encode()).hexdigest(), run_id=None,
        git_head=None, scope="project", owner="governance", status="verified",
        generated_at=now.isoformat(), expires_at=(now + timedelta(days=30)).isoformat(),
        policy_version="memory-freshness-v1",
    )
    record = MemoryRecord.from_parts(
        memory_kind="governance", summary="canonical governance guide",
        source_refs=(source,), scope="project", owner="governance",
        freshness="fresh", status="ready",
    )
    policy = CatalogBuildPolicy(
        source_root, output_root, (source,), (record,), "a" * 40, "b" * 64,
    )
    return build_catalog(source_root, output_root / "memory.json", policy)


def test_missing_provider_is_explicitly_read_only_and_bounded() -> None:
    result = MemoryHubUiService(None, project_id="nbs").catalog_status()
    assert result.status == "catalog_missing"
    assert result.records == ()
    assert result.diagnostics == ("catalog_missing",)


def test_ready_query_preserves_record_and_acl_evidence(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    result = MemoryHubUiService(lambda: catalog, project_id="nbs").query(
        query="governance", consumer_id="review-agent", scope="project",
        memory_kinds=("governance",), team_id=None,
    )
    assert result.status == "ready"
    assert result.records[0]["memoryId"] == catalog.records[0].memory_id
    assert result.records[0]["sourceCount"] == 1
    assert result.decisions[0]["decision"] == "allow"


def test_invalid_or_unknown_source_resolves_fail_closed(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    result = MemoryHubUiService(lambda: catalog, project_id="nbs").resolve_source(
        "f" * 64, consumer_id="review-agent", team_id=None,
    )
    assert result.status == "empty"
    assert result.source is None
    assert result.diagnostics == ("missing_source",)


def test_source_resolution_preserves_verified_provenance_metadata(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    source_id = catalog.sources[0].source_id
    result = MemoryHubUiService(lambda: catalog, project_id="nbs").resolve_source(
        source_id, consumer_id="review-agent", team_id=None,
    )
    assert result.status == "ready"
    assert result.source["sourceKind"] == "governance_document"
    assert result.source["generatedAt"]
    assert result.source["expiresAt"]
    assert result.source["sourceFingerprint"] == catalog.sources[0].source_fingerprint


def test_record_summary_is_bounded_for_ui(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    record = catalog.records[0]
    oversized = type(record).from_parts(
        memory_kind=record.memory_kind, summary="繁" * 600,
        source_refs=record.source_refs, scope=record.scope, owner=record.owner,
        freshness=record.freshness, status=record.status,
    )
    row = _record_rows((oversized,))[0]
    assert len(row["summary"].encode("utf-8")) <= 512
    assert row["summary"].endswith("...")


def test_missing_team_identity_is_blocked_without_raw_artifact_content(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    result = MemoryHubUiService(lambda: catalog, project_id="nbs").query(
        query="governance", consumer_id="review-agent", scope="team",
        memory_kinds=("governance",), team_id=None,
    )
    assert result.status == "blocked"
    assert result.records == ()
    assert "canonical governance guide" not in str(result.to_dict())
