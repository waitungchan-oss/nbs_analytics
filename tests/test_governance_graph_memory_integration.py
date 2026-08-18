from __future__ import annotations

import json
from pathlib import Path

from backend.agents.governance_graph_memory_integration_service import GovernanceGraphMemoryIntegrationService
from backend.agents.governance_graph_query_service import GovernanceGraphQueryService
from backend.agents.memory_hub_integration_models import build_memory_hub_integration_evidence


def _write_evidence(root: Path, run_id: str = "run-memory") -> None:
    evidence = build_memory_hub_integration_evidence(
        project_id="nbs_analytics", consumer_id="context-agent", integration_mode="direct_query",
        status="ready", reason="enriched", query_fingerprint="a" * 64, hints_fingerprint="b" * 64,
        policy_decision_fingerprints=("c" * 64,), source_refs=("memory-hub/catalog.json",), hint_count=3,
        generated_at="2026-08-18T00:00:00+00:00",
    )
    path = root / ".nbs_agent_runtime" / "runs" / run_id / "memory-hub-integration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence.to_dict()), encoding="utf-8")


def test_missing_memory_evidence_is_explicit_and_has_no_inferred_links(tmp_path: Path) -> None:
    result = GovernanceGraphMemoryIntegrationService(tmp_path).project("run-missing")
    assert result["schemaVersion"] == "memory-hub-lineage-v1"
    assert result["status"] == "missing"
    assert result["links"] == []


def test_ready_memory_evidence_projects_deterministic_link(tmp_path: Path) -> None:
    _write_evidence(tmp_path)
    service = GovernanceGraphMemoryIntegrationService(tmp_path)
    first = service.project("run-memory")
    second = service.project("run-memory")
    assert first == second
    assert first["status"] == "ready"
    assert first["links"][0]["relation"] == "produces"
    assert first["links"][0]["evidenceFingerprint"]
    assert first["lineageFingerprint"]


def test_symlinked_integration_evidence_is_blocked(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    path = tmp_path / ".nbs_agent_runtime" / "runs" / "run-symlink" / "memory-hub-integration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    result = GovernanceGraphMemoryIntegrationService(tmp_path).project("run-symlink")
    assert result["status"] == "blocked"
    assert result["links"] == []


def test_graph_query_exposes_memory_lineage_as_separate_read_model(tmp_path: Path) -> None:
    _write_evidence(tmp_path)
    result = GovernanceGraphQueryService(tmp_path).memory_lineage("run-memory")
    assert result["schemaVersion"] == "memory-hub-lineage-v1"
    assert result["status"] == "ready"
