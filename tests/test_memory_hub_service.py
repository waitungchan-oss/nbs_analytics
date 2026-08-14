from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from backend.agents.memory_hub_catalog import CatalogBuildPolicy, build_catalog
from backend.agents.memory_hub_models import MemoryQuery, MemoryRecord, MemorySource, RuntimeIdentity
from backend.agents.memory_hub_service import MemoryHubService


def _source(root: Path, name: str, *, scope: str = "project", owner: str = "governance", status: str = "verified", generated_at: str | None = None, expires_at: str | None = None) -> MemorySource:
    import hashlib

    relative = f"docs/{name}.md"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{name} evidence"
    path.write_text(content, encoding="utf-8")
    generated = datetime.now(timezone.utc) if generated_at is None else datetime.fromisoformat(generated_at)
    expires = generated + timedelta(days=30) if expires_at is None else datetime.fromisoformat(expires_at)
    return MemorySource.from_parts(
        source_kind="governance_document", artifact_ref=relative,
        artifact_sha256=hashlib.sha256(content.encode()).hexdigest(), run_id=None, git_head=None,
        scope=scope, owner=owner, status=status, generated_at=generated.isoformat(),
        expires_at=expires.isoformat(), policy_version="memory-freshness-v1",
    )


def _service(tmp_path: Path) -> MemoryHubService:
    source_root, output_root = tmp_path / "sources", tmp_path / "catalog"
    project = _source(source_root, "project")
    agent = _source(source_root, "agent", scope="agent", owner="agent-a")
    team = _source(source_root, "team", scope="team", owner="team-a")
    stale = _source(source_root, "stale", status="stale")
    expired = _source(source_root, "expired", expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), generated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    records = (
        MemoryRecord.from_parts(memory_kind="governance", summary="project guidance", source_refs=(project,), scope="project", owner="governance", freshness="fresh", status="ready"),
        MemoryRecord.from_parts(memory_kind="skill", summary="agent guidance", source_refs=(agent,), scope="agent", owner="agent-a", freshness="fresh", status="ready"),
        MemoryRecord.from_parts(memory_kind="skill", summary="team guidance", source_refs=(team,), scope="team", owner="team-a", freshness="fresh", status="ready"),
        MemoryRecord.from_parts(memory_kind="evidence", summary="expired guidance", source_refs=(expired,), scope="project", owner="governance", freshness="fresh", status="ready"),
    )
    policy = CatalogBuildPolicy(source_root, output_root, (project, agent, team, stale, expired), records, "a" * 40, "b" * 64)
    catalog = build_catalog(source_root, output_root / "memory.json", policy)
    return MemoryHubService(catalog, project_id="nbs_analytics")


def test_query_returns_deterministic_ready_records_with_acl_decisions(tmp_path: Path):
    service = _service(tmp_path)
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="project", memory_kinds=("governance", "skill"))
    result = service.query(query, RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a", team_id="team-a"))
    assert result.status == "ready"
    assert [record.memory_id for record in result.records] == sorted(record.memory_id for record in result.records)
    assert any(item.decision == "allow" for item in result.acl_decisions)
    allowed_ids = {record.memory_id for record, decision in zip(result.records, result.acl_decisions) if decision.decision == "allow"}
    assert allowed_ids == {record.memory_id for record in result.records}


def test_query_denies_wrong_project_and_team_without_claim(tmp_path: Path):
    service = _service(tmp_path)
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="project", memory_kinds=("governance",))
    assert service.query(query, RuntimeIdentity.from_parts(project_id="other", consumer_id="agent-a")).status == "blocked"
    team_query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="team", memory_kinds=("skill",))
    assert service.query(team_query, RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a")).status == "blocked"


def test_query_rejects_consumer_identity_mismatch(tmp_path: Path):
    service = _service(tmp_path)
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-b", scope="project", memory_kinds=("governance",))
    result = service.query(query, RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a"))
    assert result.status == "blocked"


def test_query_preserves_denied_scope_decision_for_audit(tmp_path: Path):
    service = _service(tmp_path)
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="team", memory_kinds=("skill",))
    result = service.query(query, RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a", team_id="team-b"))
    assert result.status == "empty"
    assert result.acl_decisions
    assert any(item.decision == "blocked" for item in result.acl_decisions)


def test_query_filters_non_fresh_records_and_enforces_bounds(tmp_path: Path):
    service = _service(tmp_path)
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="project", memory_kinds=("governance",))
    result = service.query(query, RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a"))
    assert result.status == "ready"
    assert len(result.records) <= 3
    assert result.to_dict()["resultFingerprint"]
    expired_query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="project", memory_kinds=("evidence",))
    assert service.query(expired_query, RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a")).status == "empty"


def test_resolve_source_is_bounded_and_rejects_unknown_id(tmp_path: Path):
    service = _service(tmp_path)
    identity = RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a")
    source = next(item for item in service.catalog.sources if item.scope == "project" and item.status == "verified" and datetime.fromisoformat(item.expires_at) > datetime.now(timezone.utc))
    resolved = service.resolve_source(source.source_id, identity)
    assert resolved.status == "ready"
    assert resolved.source_id == source.source_id
    assert service.resolve_source("f" * 64, identity).status == "empty"
    stale = next(item for item in service.catalog.sources if item.status == "stale")
    assert service.resolve_source(stale.source_id, identity).status in {"blocked", "empty"}


def test_query_rejects_malformed_identity_without_side_effect(tmp_path: Path):
    service = _service(tmp_path)
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="project", memory_kinds=("governance",))
    with pytest.raises(ValueError):
        service.query(query, object())


def test_missing_catalog_returns_bounded_fallback():
    service = MemoryHubService(None, project_id="nbs_analytics")  # type: ignore[arg-type]
    query = MemoryQuery.from_parts(query="guidance", consumer_id="agent-a", scope="project", memory_kinds=("governance",))
    identity = RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="agent-a")
    assert service.query(query, identity).status == "blocked"
    assert service.resolve_source("a" * 64, identity).status == "blocked"
