from __future__ import annotations

from pathlib import Path

from backend.services.agent_operations_service import AgentOperationsService
from scripts.provision_memory_hub_catalog import provision


def test_agent_operations_snapshot_projects_memory_hub_readiness() -> None:
    root = Path(__file__).resolve().parents[1]
    # The service is intentionally read-only; prepare its deployment-owned
    # catalog here so this test does not depend on another test's order.
    provision(root)
    snapshot = AgentOperationsService(root).build_snapshot()
    observation = snapshot["memoryHubIntegration"]
    assert observation["status"] == "ready"
    assert observation["consumers"][0]["consumerId"] == "context-agent"
    assert observation["consumers"][0]["integrationMode"] == "direct_query"
    assert observation["consumers"][0]["hintCount"] == 0
    assert observation["consumers"][0]["authority"] == "non_authoritative_memory"


def test_agent_operations_snapshot_reports_missing_catalog_without_creating_it(tmp_path: Path) -> None:
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    observation = snapshot["memoryHubIntegration"]
    assert observation["status"] == "blocked"
    assert observation["reason"] == "provider_unavailable"
    assert not (tmp_path / ".nbs_agent_runtime" / "memory-hub").exists()
